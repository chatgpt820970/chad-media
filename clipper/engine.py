"""Campaign assignment, captions, earnings split, and duplicate checks."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from clipper.loadconfig import PLATFORMS, RETIRED_TOKENS

_YT = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})")
_TT = re.compile(r"/video/(\d+)")
_IG = re.compile(r"/(?:reel|p|tv)/([A-Za-z0-9_-]+)")


def fingerprint(clip: dict) -> str:
    if clip.get("fingerprint"):
        return str(clip["fingerprint"])
    url = (clip.get("url") or "").strip()
    if url:
        return normalize_source(url)
    return str(clip.get("source_id") or "")


def normalize_source(url: str) -> str:
    raw = url.strip()
    match = _YT.search(raw)
    if match:
        return "yt:" + match.group(1)
    match = _TT.search(raw)
    if match:
        return "tt:" + match.group(1)
    match = _IG.search(raw)
    if match:
        return "ig:" + match.group(1)
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        query = parse_qs(parsed.query)
        video_id = (query.get("v") or [""])[0]
        if video_id:
            return "yt:" + video_id
        path = parsed.path.rstrip("/")
        return f"{parsed.netloc.lower()}{path}"
    return raw


def source_rank(url: str, pipeline: dict) -> int:
    """Lower is a cleaner source. YouTube originals before TikTok or Instagram links."""
    host = urlparse(url).netloc.lower()
    rules = pipeline.get("watermark") or {}
    for preferred in rules.get("prefer_clean_hosts") or []:
        if preferred in host or preferred in url:
            return 0
    for blocked in rules.get("deprioritize_hosts") or []:
        if blocked in host or blocked in url:
            return 2
    return 1


def rejection(campaign: dict, clip: dict, platform_cap: float | None = None) -> str | None:
    """Why this campaign cannot take the clip.

    Views-to-first-payout is intentionally not a reason. A high minimum payout
    is logged later as a summary warning.
    """
    if clip["content_type"] not in campaign["content_types"]:
        return "content"
    if clip["platform"] not in campaign["platforms"]:
        return "platform"
    if not campaign.get("active"):
        return "inactive"
    duration = clip.get("duration_sec")
    rules = campaign.get("rules") or {}
    if duration is not None and rules.get("min_duration_sec") is not None:
        if duration < rules["min_duration_sec"]:
            return "too_short"
    if duration is not None and rules.get("max_duration_sec") is not None:
        if duration > rules["max_duration_sec"]:
            return "too_long"
    if duration is not None and platform_cap is not None and duration > float(platform_cap):
        return "platform_too_long"
    return None


def choose_campaign(
    clip: dict,
    campaigns: list[dict],
    platform_cap: float | None = None,
) -> tuple[dict | None, str]:
    """Pick the eligible campaign with the highest rate. Inactive matches are reported."""
    eligible: list[dict] = []
    inactive: list[str] = []
    blocked: list[str] = []
    for campaign in campaigns:
        why = rejection(campaign, clip, platform_cap)
        if why is None:
            eligible.append(campaign)
        elif why == "inactive":
            inactive.append(campaign["id"])
        elif why not in {"content", "platform"}:
            blocked.append(why)
    if eligible:
        best = sorted(eligible, key=lambda row: (-float(row["rate_per_1k_views_usd"]), row["id"]))[0]
        return best, ""
    if blocked:
        return None, blocked[0]
    if inactive:
        return None, "inactive:" + ",".join(inactive)
    return None, "unassigned"


def money(campaign: dict, name: str) -> float:
    """Read min_payout / max_payout, falling back to the *_usd keys."""
    if name in campaign and campaign[name] is not None:
        return float(campaign[name])
    legacy = f"{name}_usd"
    if legacy in campaign and campaign[legacy] is not None:
        return float(campaign[legacy])
    raise KeyError(name)


def views_to_first_payout(campaign: dict) -> float:
    """Views required before the first payout. Not a routing gate."""
    rate = float(campaign.get("rate_per_1k_views_usd") or 0)
    if rate <= 0:
        return float("inf")
    return money(campaign, "min_payout") / rate * 1000.0


def threshold_warning(campaign: dict, threshold: float) -> str | None:
    """Warning text when views-to-first-payout is above the configured line.

    Activation stays a manual config edit. This never rejects a campaign.
    """
    views = views_to_first_payout(campaign)
    if views <= float(threshold):
        return None
    payout = money(campaign, "min_payout")
    rate = campaign.get("rate_per_1k_views_usd")
    return (
        f"{campaign.get('title') or campaign['id']} ({campaign['id']}): about {views:.0f} views "
        f"to the first ${payout:g} payout at ${rate:g}/1k, above the {int(threshold)} warning line. "
        "This does not reject the campaign. Leave it off, or set active to true, yourself."
    )


def out_of_budget(campaign: dict) -> bool:
    snapshot = campaign.get("budget_snapshot") or {}
    remaining = float(snapshot.get("remaining_usd") or 0)
    return remaining < money(campaign, "min_payout")


def campaign_score(campaign: dict, allocation: dict) -> float:
    if not campaign.get("active") or out_of_budget(campaign):
        return 0.0
    rate = float(campaign["rate_per_1k_views_usd"])
    if rate <= 0:
        return 0.0
    remaining = float((campaign.get("budget_snapshot") or {}).get("remaining_usd") or 0)
    cap = float(allocation.get("headroom_cap_blocks") or 100)
    blocks = remaining / rate
    headroom = min(blocks, cap) / cap
    weight = float(campaign.get("allocation_weight") or 1)
    return weight * rate * (0.35 + 0.65 * headroom)


def allocate_slots(scores: dict[str, float], slots: int) -> dict[str, int]:
    """Largest-remainder split. Zero scores are already omitted."""
    if slots <= 0 or not scores:
        return {}
    total = sum(scores.values())
    if total <= 0:
        return {}
    raw = {cid: slots * score / total for cid, score in scores.items()}
    quotas = {cid: int(value) for cid, value in raw.items()}
    left = slots - sum(quotas.values())
    order = sorted(raw, key=lambda cid: (-(raw[cid] - int(raw[cid])), cid))
    for cid in order:
        if left <= 0:
            break
        quotas[cid] += 1
        left -= 1
    return {cid: count for cid, count in quotas.items() if count > 0}


def deal_sequence(quotas: dict[str, int]) -> list[str]:
    """Spread a campaign's slots through the day instead of stacking them."""
    left = {cid: count for cid, count in quotas.items() if count > 0}
    order = sorted(left, key=lambda cid: (-left[cid], cid))
    sequence: list[str] = []
    while sum(left.values()):
        for cid in order:
            if left[cid] <= 0:
                continue
            sequence.append(cid)
            left[cid] -= 1
    return sequence


def spread_indices(total: int, count: int) -> list[int]:
    if count <= 0 or total <= 0:
        return []
    if count >= total:
        return list(range(total))
    if count == 1:
        return [0]
    indexes = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    unique: list[int] = []
    for index in indexes:
        if index not in unique:
            unique.append(index)
    cursor = 0
    while len(unique) < count and cursor < total:
        if cursor not in unique:
            unique.append(cursor)
        cursor += 1
    return sorted(unique)[:count]


def clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _slot_gaps(clock_times: list[str]) -> list[int]:
    minutes = sorted(clock_minutes(value) for value in clock_times)
    if len(minutes) < 2:
        return []
    gaps = [minutes[index + 1] - minutes[index] for index in range(len(minutes) - 1)]
    gaps.append((minutes[0] + 24 * 60) - minutes[-1])
    return gaps


def quiet_gap_minutes(clock_times: list[str]) -> int:
    """Largest break between slots, including the wrap past midnight."""
    gaps = _slot_gaps(clock_times)
    return max(gaps) if gaps else 0


def min_slot_gap_minutes(clock_times: list[str]) -> int:
    """Smallest break between slots, including the wrap past midnight."""
    gaps = _slot_gaps(clock_times)
    return min(gaps) if gaps else 0


def upcoming_slots(now: datetime, clock_times: list[str], timezone: str, count: int) -> list[datetime]:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    chosen = [clock_times[index] for index in spread_indices(len(clock_times), count)]
    slots: list[datetime] = []
    for value in chosen:
        hour, minute = (int(part) for part in value.split(":"))
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        slots.append(candidate.astimezone(ZoneInfo("UTC")))
    slots.sort()
    return slots


def cross_post_block(mode: str, platform: str, already: set[str], risky: set[str]) -> str | None:
    if platform in already:
        return "same_platform"
    if mode == "exact":
        return None
    if mode == "one_shortform" and platform in risky and (already & risky):
        return "risky_shortform"
    if mode == "blocked" and already:
        return "cross_post_blocked"
    return None


def variant_for(platform: str) -> dict:
    """Distinct crop and start so the same scene is not byte-identical on two platforms."""
    table = {
        "x": {"crop_x": 0.0, "start_sec": 0.0},
        "youtube": {"crop_x": 0.04, "start_sec": 0.35},
        "instagram": {"crop_x": 0.08, "start_sec": 0.7},
        "tiktok": {"crop_x": 0.02, "start_sec": 0.15},
    }
    return dict(table.get(platform) or {"crop_x": 0.0, "start_sec": 0.0})


def _stable_index(key: str, size: int) -> int:
    if size <= 1:
        return 0
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % size


def rotated_hashtags(limits: dict | None, salt: str, reserved: int = 0) -> list[str]:
    """Pick a different optional-tag slice for each post, inside the platform cap.

    `reserved` counts hashtags the caption already has to keep, such as `#Ad`.
    Required tags (`#shorts`) are counted from `limits` and are not repeated here.
    """
    limits = limits or {}
    pool = [str(tag) for tag in (limits.get("optional_hashtags") or []) if str(tag).startswith("#")]
    if not pool:
        return []
    required = [str(tag) for tag in (limits.get("require_hashtags") or []) if tag]
    max_tags = int(limits["max_hashtags"]) if limits.get("max_hashtags") is not None else len(pool)
    room = max(0, max_tags - len(required) - max(0, int(reserved)))
    if room <= 0:
        return []
    start = _stable_index(f"{salt}:tags", len(pool))
    ordered = pool[start:] + pool[:start]
    picked: list[str] = []
    for tag in ordered:
        if tag in picked or tag in required:
            continue
        picked.append(tag)
        if len(picked) >= room:
            break
    return picked


_HASHTAG = re.compile(r"#[A-Za-z0-9_]+")


def hashtags_in(text: str) -> list[str]:
    return _HASHTAG.findall(text or "")


def normalize_caption(text: str) -> str:
    return " ".join((text or "").split()).lower()


def trim_hashtags(text: str, max_hashtags: int, keep: set[str]) -> str:
    """Drop optional tags until the caption is within the platform cap."""
    tags = hashtags_in(text)
    if len(tags) <= max_hashtags:
        return text
    for tag in reversed([item for item in tags if item not in keep]):
        if len(hashtags_in(text)) <= max_hashtags:
            break
        text = re.sub(rf"(?<!\w){re.escape(tag)}\b", "", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" *\n *", "\n", text).strip()
    return text


def _assemble(
    campaign: dict,
    platform: str,
    char_limit: int,
    hook: str,
    limits: dict | None,
) -> str:
    caption = campaign["caption"]
    limits = limits or {}
    parts: list[str] = []
    disclosure = (caption.get("disclosure") or "").strip()
    if disclosure:
        parts.append(disclosure)
    if hook:
        parts.append(hook)
    text = "\n".join(parts)
    for phrase in caption.get("required_phrases") or []:
        if phrase not in text:
            text = (text + "\n" + phrase).strip()
    tags: list[str] = []
    pool = (
        list(caption.get("hashtags") or [])
        + list((caption.get("platform_tags") or {}).get(platform) or [])
        + list(limits.get("require_hashtags") or [])
    )
    for tag in pool:
        if tag and tag not in tags:
            tags.append(tag)
    keep: set[str] = set()
    if disclosure.startswith("#"):
        keep.add(disclosure.split()[0])
    keep.update(
        tag
        for tag in ((caption.get("platform_tags") or {}).get(platform) or [])
        if str(tag).startswith("#")
    )
    keep.update(limits.get("require_hashtags") or [])
    if tags:
        text = (text + "\n" + " ".join(tags)).strip()
    max_tags = limits.get("max_hashtags")
    if max_tags is not None:
        text = trim_hashtags(text, int(max_tags), keep)
    limit = int(limits.get("char_limit") or char_limit)
    if platform == "x":
        text = strip_urls(text)
        text = fit_x(text, caption, limit)
    missing = [phrase for phrase in caption.get("required_phrases") or [] if phrase not in text]
    if missing:
        raise ValueError(f"{campaign['id']} caption missing {missing}")
    for tag in (caption.get("platform_tags") or {}).get(platform) or []:
        if tag not in text:
            raise ValueError(f"{campaign['id']} caption missing {tag} on {platform}")
    for tag in limits.get("require_hashtags") or []:
        if tag not in text:
            if platform == "x" and len(text) + 1 + len(tag) > limit:
                raise ValueError(f"{campaign['id']} cannot fit {tag} on X")
            text = (text + " " + tag).strip()
    return text


def build_caption(
    campaign: dict,
    clip: dict,
    platform: str,
    char_limit: int,
    limits: dict | None = None,
    used: set[str] | None = None,
) -> str:
    """Build one caption. Hooks rotate so the same platform does not repeat a caption."""
    caption = campaign["caption"]
    hooks = list(caption.get("hooks") or [])
    if not hooks:
        hooks = [""]
    start = _stable_index(f"{platform}:{clip['source_id']}", len(hooks))
    ordered = hooks[start:] + hooks[:start]
    last = ""
    for hook in ordered:
        text = _assemble(campaign, platform, char_limit, hook, limits)
        key = normalize_caption(text)
        if used is not None and key in used:
            last = text
            continue
        if used is not None:
            used.add(key)
        return text
    text = (last or _assemble(campaign, platform, char_limit, ordered[0], limits)).rstrip()
    text = f"{text}\n{clip['source_id']}"
    if platform == "x":
        text = fit_x(strip_urls(text), campaign["caption"], int((limits or {}).get("char_limit") or char_limit))
    if used is not None:
        used.add(normalize_caption(text))
    return text


def strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text).strip()


def fit_x(text: str, caption: dict, limit: int) -> str:
    if len(text) <= limit:
        return text
    required = list(caption.get("required_phrases") or [])
    tags = list(caption.get("hashtags") or [])
    disclosure = (caption.get("disclosure") or "").strip()
    rebuilt = "\n".join(part for part in [disclosure, *required, " ".join(tags)] if part)
    if len(rebuilt) <= limit:
        return rebuilt
    raise ValueError(f"required X caption is {len(rebuilt)} characters")


def foreign_tokens(text: str, campaign: dict, campaigns: list[dict]) -> list[str]:
    own = set(campaign.get("caption", {}).get("exclusive_tokens") or [])
    hits: list[str] = []
    for other in campaigns:
        if other["id"] == campaign["id"]:
            continue
        for token in other.get("caption", {}).get("exclusive_tokens") or []:
            if token in own or not token:
                continue
            if token in text and token not in hits:
                hits.append(token)
    for token in RETIRED_TOKENS:
        if token.lower() in text.lower() and token not in hits:
            hits.append(token)
    return hits


def publish_blockers(campaign: dict, clip: dict, channel_id: str) -> list[str]:
    blockers: list[str] = []
    if campaign.get("account_mode") == "dedicated" and not channel_id:
        blockers.append("needs_account")
    logo = campaign.get("logo") or {}
    if logo.get("required") and (logo.get("placeholder") or not logo.get("file")):
        blockers.append("needs_official_logo")
    if campaign.get("footage") == "own_edit" and not clip.get("ready"):
        blockers.append("needs_edit")
    return blockers


def content_plan_caption(
    clip: dict,
    platform: str,
    char_limit: int,
    limits: dict | None = None,
    used: set[str] | None = None,
    extra_hashtags: list[str] | None = None,
) -> str:
    """Owner content-plan caption. No paid disclosure and no campaign logo line."""
    limits = limits or {}
    title = (clip.get("title") or clip["source_id"]).strip()
    if platform == "x":
        title = strip_urls(title)
    tags = [tag for tag in (limits.get("require_hashtags") or []) if tag]
    for tag in extra_hashtags or []:
        if tag and tag not in tags:
            tags.append(tag)
    text = title if not tags else title + "\n" + " ".join(tags)
    max_tags = limits.get("max_hashtags")
    if max_tags is not None:
        text = trim_hashtags(text, int(max_tags), set(tags))
    limit = int(limits.get("char_limit") or char_limit)
    if platform == "x":
        text = fit_x(strip_urls(text), {"required_phrases": [], "hashtags": [], "disclosure": ""}, limit)
    key = normalize_caption(text)
    if used is not None and key in used:
        suffix = clip["source_id"]
        text = f"{text}\n{suffix}"
        if platform == "x":
            text = fit_x(strip_urls(text), {"required_phrases": [], "hashtags": [], "disclosure": ""}, limit)
        key = normalize_caption(text)
    if used is not None:
        used.add(key)
    return text


def ytdlp_format() -> str:
    """Best available picture up to 4K, then 1080p. Watermarks are removed after download, not by the format string."""
    return (
        "bv*[height<=2160]+ba/"
        "bv*[height<=1080]+ba/"
        "b[height<=2160]/b"
    )


def ytdlp_command(url: str, output: str, cookies: str | None) -> list[str]:
    command = [
        "yt-dlp",
        "-f",
        ytdlp_format(),
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-o",
        output,
    ]
    if cookies:
        command.extend(["--cookies", cookies])
    command.append(url)
    return command
