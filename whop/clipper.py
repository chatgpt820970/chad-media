#!/usr/bin/env python3
"""Platform-specific Whop campaign mapping for X (@chadriyo) and YouTube Shorts (Denzen).

Every X view is paired with a campaign whose accepted_platforms includes "x".
Every YouTube Shorts view is paired with a campaign whose accepted_platforms
includes "youtube_shorts". FastLoanPilot does not pay for X. Pool Guard rejects
bounties whose denial rate is above the configured cap (Shane Gillis is 99.8%).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAMPAIGNS_PATH = ROOT / "campaigns.json"
MANIFEST_PATH = ROOT / "manifest.json"

SUPPORTED_PLATFORMS = ("x", "youtube_shorts")
PLATFORM_ACCOUNTS = {
    "x": "@chadriyo",
    "youtube_shorts": "Denzen (@denzen0)",
}
X_CAMPAIGN_IDS = (
    "dishpilot-x-clipping",
    "finsta-ugc-clipping",
    "darkroom-clipping",
)
YOUTUBE_PRIMARY_ID = "flp-logo-clipping"
DAILY_SLOT_COUNT = 10
OPEN_STATUSES = ("open", "active", "live")

# Ten posting slots. X campaigns rotate in routing order so each paying
# campaign receives @chadriyo views. YouTube Shorts stays on FastLoanPilot.
DAILY_SLOTS = (
    {"slot": 1, "time_ist": "08:00", "hook": "Wait for the ending"},
    {"slot": 2, "time_ist": "09:30", "hook": "Nobody saw this coming"},
    {"slot": 3, "time_ist": "11:00", "hook": "Watch this twice"},
    {"slot": 4, "time_ist": "12:30", "hook": "This should not be possible"},
    {"slot": 5, "time_ist": "14:00", "hook": "Pause on the last second"},
    {"slot": 6, "time_ist": "15:30", "hook": "The whole crowd went quiet"},
    {"slot": 7, "time_ist": "17:00", "hook": "He only needed one try"},
    {"slot": 8, "time_ist": "18:30", "hook": "Look at the bottom left"},
    {"slot": 9, "time_ist": "20:00", "hook": "This ending is unreal"},
    {"slot": 10, "time_ist": "21:30", "hook": "Count how many you catch"},
)

_LINK_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)


class CampaignPlatformError(ValueError):
    """A clip was paired with a campaign that does not pay for that platform."""


def load_campaigns(path: Path | None = None) -> dict:
    target = path or CAMPAIGNS_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _rules(campaign: dict) -> dict:
    rules = campaign.get("rules")
    return rules if isinstance(rules, dict) else {}


def _total_pool(campaign: dict) -> float:
    if campaign.get("total_pool_usd") is not None:
        return float(campaign["total_pool_usd"])
    return float(campaign.get("budget_total_usd") or 0.0)


def _remaining_pool(campaign: dict) -> float:
    if campaign.get("remaining_pool_usd") is not None:
        return float(campaign["remaining_pool_usd"])
    if campaign.get("remaining_budget_usd") is not None:
        return float(campaign["remaining_budget_usd"])
    return _total_pool(campaign) - float(campaign.get("paid_out_usd") or 0.0)


def _rate(campaign: dict) -> float:
    if campaign.get("rate_per_1k_views_usd") is not None:
        return float(campaign["rate_per_1k_views_usd"])
    return float(campaign.get("cpm_usd") or 0.0)


def _paid_fraction(campaign: dict, total: float) -> float:
    """paid_out_percent is on a 0-100 scale (4.0 means 4 percent)."""
    if campaign.get("paid_out_percent") is not None:
        return float(campaign["paid_out_percent"]) / 100.0
    paid = float(campaign.get("paid_out_usd") or 0.0)
    if total > 0:
        return paid / total
    return 0.0


def check_campaign_pool_guard(campaign: dict, guard_cfg: dict | None) -> tuple[bool, str]:
    """Return whether a campaign still has a payable, low-denial reward pool.

    High denial rates are rejected even when the campaign is marked unlimited
    or still has budget left. whop-ai-shane-gillis (denial_rate 0.998) fails.
    """
    rules = guard_cfg or {}
    status = str(campaign.get("status") or "open").lower()
    if rules.get("skip_paused_or_closed", True) and status not in OPEN_STATUSES:
        return False, f"status is {status}"

    denial = campaign.get("denial_rate")
    max_denial = float(rules.get("max_denial_rate", 0.50))
    if denial is not None and float(denial) > max_denial:
        return False, (
            f"denial rate {float(denial) * 100:.1f}% exceeds {max_denial * 100:.0f}% cap"
        )

    if bool(campaign.get("unlimited_budget", False)):
        remaining = _remaining_pool(campaign)
        return True, f"OK unlimited (remaining=${remaining:.0f})"

    total = _total_pool(campaign)
    remaining = _remaining_pool(campaign)
    min_remaining = float(rules.get("min_remaining_budget_usd", 500.0))
    max_paid = float(rules.get("max_paid_out_fraction", 0.80))
    min_spots = int(rules.get("min_spots_remaining", 5))

    if remaining < min_remaining:
        return False, f"remaining pool ${remaining:.2f} < ${min_remaining:.2f} minimum"

    paid_fraction = _paid_fraction(campaign, total)
    if paid_fraction > max_paid:
        return False, (
            f"pool {paid_fraction * 100:.1f}% exhausted (> {max_paid * 100:.0f}% cap)"
        )

    spots = campaign.get("spots_remaining")
    spot_txt = ""
    if spots is not None:
        spot_count = int(spots)
        if spot_count < min_spots:
            return False, f"only {spot_count} spots remaining (< {min_spots})"
        spot_txt = f", spots={spot_count}"

    return True, (
        f"OK (remaining=${remaining:.0f}, paid={paid_fraction * 100:.1f}%{spot_txt})"
    )


def _is_direct_submission(campaign: dict) -> bool:
    if "hub" in str(campaign.get("type") or ""):
        return False
    if not campaign.get("submission_url"):
        return False
    if not (campaign.get("experience_id") or campaign.get("content_rewards_experience_id")):
        return False
    return True


def _routing(campaigns_data: dict, platform: str) -> dict:
    routing = (campaigns_data.get("platform_routing") or {}).get(platform) or {}
    return routing if isinstance(routing, dict) else {}


def bind_clip_to_campaign(campaign: dict, platform: str) -> dict:
    """Raise if this clip's platform is not explicitly paid by the campaign."""
    accepted = list(campaign.get("accepted_platforms") or [])
    if platform not in accepted:
        account = PLATFORM_ACCOUNTS.get(platform, platform)
        raise CampaignPlatformError(
            f"Refusing to pair a {platform} clip ({account}) with campaign "
            f"{campaign.get('id')!r}: accepted_platforms={accepted} does not include {platform!r}."
        )
    return campaign


def campaigns_for_platform(campaigns_data: dict, platform: str) -> list[dict]:
    """Pool-guard-passing direct campaigns that explicitly accept `platform`."""
    if platform not in SUPPORTED_PLATFORMS:
        raise CampaignPlatformError(
            f"Unsupported platform {platform!r}. Expected one of {SUPPORTED_PLATFORMS}."
        )
    guard_cfg = campaigns_data.get("pool_guard_rules") or {}
    allow_ids = _routing(campaigns_data, platform).get("campaign_ids")
    allowed = set(allow_ids) if isinstance(allow_ids, list) else None
    by_id: dict[str, dict] = {}
    ordered: list[dict] = []
    for campaign in campaigns_data.get("active_campaigns") or []:
        if not isinstance(campaign, dict):
            continue
        if not _is_direct_submission(campaign):
            continue
        ok, _reason = check_campaign_pool_guard(campaign, guard_cfg)
        if not ok:
            continue
        accepted = list(campaign.get("accepted_platforms") or [])
        if platform not in accepted:
            continue
        campaign_id = str(campaign.get("id") or "")
        if allowed is not None and campaign_id not in allowed:
            continue
        by_id[campaign_id] = campaign
        ordered.append(campaign)
    if isinstance(allow_ids, list):
        return [by_id[campaign_id] for campaign_id in allow_ids if campaign_id in by_id]
    return ordered


def select_campaign_for_platform(campaigns_data: dict, platform: str) -> dict:
    """Pick one campaign that passes Pool Guard and explicitly accepts `platform`.

    YouTube Shorts (Denzen) prefers the routed primary, FastLoanPilot.
    X (@chadriyo) prefers the healthiest routed campaign that lists "x".
    Pairing a clip with a campaign that omits the platform raises CampaignPlatformError.
    """
    if platform not in SUPPORTED_PLATFORMS:
        raise CampaignPlatformError(
            f"Unsupported platform {platform!r}. Expected one of {SUPPORTED_PLATFORMS}."
        )
    eligible = campaigns_for_platform(campaigns_data, platform)
    if not eligible:
        account = PLATFORM_ACCOUNTS.get(platform, platform)
        skipped = [
            str(campaign.get("id"))
            for campaign in campaigns_data.get("active_campaigns") or []
            if isinstance(campaign, dict) and platform not in (campaign.get("accepted_platforms") or [])
        ]
        sample = ", ".join(skipped[:6]) or "none"
        raise CampaignPlatformError(
            f"No Pool-Guard-passing campaign accepts {platform} ({account}). "
            f"Refusing to pair that clip with a campaign that does not list {platform!r} "
            f"(campaigns missing {platform!r}: {sample})."
        )
    primary_id = _routing(campaigns_data, platform).get("primary_campaign_id")
    chosen = None
    if primary_id:
        chosen = next((campaign for campaign in eligible if campaign.get("id") == primary_id), None)
    if chosen is None:
        chosen = max(eligible, key=lambda campaign: (_remaining_pool(campaign), _rate(campaign)))
    return bind_clip_to_campaign(chosen, platform)


def duration_fits(campaign: dict, duration_sec: float | None) -> tuple[bool, str]:
    rules = _rules(campaign)
    if duration_sec is None:
        return True, "duration not checked"
    duration = float(duration_sec)
    minimum = rules.get("min_duration_sec")
    maximum = rules.get("max_duration_sec")
    if minimum is not None and duration < float(minimum):
        return False, f"duration {duration:g}s < min {minimum}s"
    if maximum is not None and duration > float(maximum):
        return False, f"duration {duration:g}s > max {maximum}s"
    return True, "duration ok"


def compose_post_hook(hook: str, body: str = "") -> str:
    hook_text = hook.strip()
    body_text = body.strip()
    if hook_text and body_text:
        return f"{hook_text}\n\n{body_text}"
    return hook_text or body_text


def _extra_hashtags(caption: str, campaign: dict) -> list[str]:
    existing = caption.lower()
    extras: list[str] = []
    for tag in _rules(campaign).get("hashtags") or []:
        text = str(tag).strip()
        if text and text.lower() not in existing:
            extras.append(text)
    return extras


def build_platform_caption(
    hook: str,
    campaign: dict,
    platform: str,
    *,
    links_blocked: bool | None = None,
) -> str:
    """Hook + mandatory CTA + hashtags. X drops raw links when the channel blocks them."""
    cta = str(_rules(campaign).get("mandatory_caption") or "").strip()
    parts = [part for part in (hook.strip(), cta) if part]
    caption = "\n\n".join(parts)
    extras = _extra_hashtags(caption, campaign)
    if extras:
        caption = f"{caption}\n\n{' '.join(extras)}" if caption else " ".join(extras)
    if links_blocked is None:
        links_blocked = platform == "x"
    if links_blocked:
        caption = _LINK_RE.sub("", caption)
        caption = re.sub(r"[ \t]{2,}", " ", caption)
        caption = re.sub(r" *\n", "\n", caption)
        caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
    return caption.strip()


def x_links_blocked(campaigns_data: dict) -> bool:
    routing = _routing(campaigns_data, "x")
    if "links_blocked" in routing:
        return bool(routing["links_blocked"])
    channel = (campaigns_data.get("channels") or {}).get("x") or {}
    return bool(channel.get("links_blocked", True))


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "")
        .replace("%", "%%%%")
    )


def banner_overlay_spec(campaign: dict) -> dict:
    rules = _rules(campaign)
    return {
        "logo_overlay_required": bool(rules.get("logo_overlay_required", False)),
        "logo_text": str(rules.get("logo_text") or ""),
        "logo_subtext": str(rules.get("logo_subtext") or ""),
        "logo_path": str(campaign.get("logo_path") or ""),
        "min_duration_sec": rules.get("min_duration_sec"),
        "max_duration_sec": rules.get("max_duration_sec"),
    }


def overlay_plan(youtube_campaign: dict, x_campaign: dict) -> dict:
    """Platform-specific banners when the brands differ, one shared banner when they match."""
    youtube_spec = banner_overlay_spec(youtube_campaign)
    x_spec = banner_overlay_spec(x_campaign)
    same_brand = (
        youtube_spec["logo_text"] == x_spec["logo_text"]
        and youtube_spec["logo_subtext"] == x_spec["logo_subtext"]
        and youtube_spec["logo_path"] == x_spec["logo_path"]
    )
    mode = "dual_compatible" if same_brand else "platform_specific_variants"
    return {"mode": mode, "youtube_shorts": youtube_spec, "x": x_spec}


def ffmpeg_banner_filter(spec: dict, input_label: str, font_path: str) -> tuple[str, str]:
    """Draw logo_text / logo_subtext. Returns (filter graph, output label)."""
    logo_text = escape_drawtext(str(spec.get("logo_text") or ""))
    logo_subtext = escape_drawtext(str(spec.get("logo_subtext") or ""))
    label = input_label
    parts: list[str] = []
    if logo_text:
        parts.append(
            f"{label}drawtext=fontfile='{font_path}':text='{logo_text}':"
            "fontcolor=white:fontsize=54:borderw=4:bordercolor=black:"
            "box=1:boxcolor=black@0.72:boxborderw=16:"
            "x=(w-text_w)/2:y=h-280[vbrand]"
        )
        label = "[vbrand]"
    if logo_subtext:
        parts.append(
            f"{label}drawtext=fontfile='{font_path}':text='{logo_subtext}':"
            "fontcolor=white:fontsize=32:borderw=3:bordercolor=black:"
            "x=(w-text_w)/2:y=h-210[vsub]"
        )
        label = "[vsub]"
    if not parts:
        return "", input_label
    return ";".join(parts), label


def platform_account(campaigns_data: dict, platform: str) -> dict:
    routing = _routing(campaigns_data, platform)
    if platform == "x":
        channel = (campaigns_data.get("channels") or {}).get("x") or {}
        return {
            "handle": routing.get("handle") or channel.get("handle") or "@chadriyo",
            "username": routing.get("username") or channel.get("username") or "",
        }
    channel = (campaigns_data.get("channels") or {}).get("youtube") or {}
    return {
        "handle": routing.get("handle") or channel.get("handle") or "Denzen",
        "username": routing.get("username") or channel.get("username") or "@denzen0",
    }


def build_daily_manifest(campaigns_data: dict, *, generated_at: str | None = None) -> dict:
    """Ten slots. YouTube Shorts always uses a youtube_shorts payer. X always uses an x payer."""
    if len(DAILY_SLOTS) != DAILY_SLOT_COUNT:
        raise CampaignPlatformError(f"Expected {DAILY_SLOT_COUNT} daily slots")
    youtube_campaign = bind_clip_to_campaign(
        select_campaign_for_platform(campaigns_data, "youtube_shorts"),
        "youtube_shorts",
    )
    x_campaigns = [
        bind_clip_to_campaign(campaign, "x")
        for campaign in campaigns_for_platform(campaigns_data, "x")
    ]
    if not x_campaigns:
        raise CampaignPlatformError(
            "No Pool-Guard-passing campaign accepts x (@chadriyo). Refusing to build the manifest."
        )
    youtube_account = platform_account(campaigns_data, "youtube_shorts")
    x_account = platform_account(campaigns_data, "x")
    links_blocked = x_links_blocked(campaigns_data)
    slots: list[dict] = []
    for index, spec in enumerate(DAILY_SLOTS):
        x_campaign = x_campaigns[index % len(x_campaigns)]
        bind_clip_to_campaign(x_campaign, "x")
        bind_clip_to_campaign(youtube_campaign, "youtube_shorts")
        hook = str(spec["hook"])
        x_caption = build_platform_caption(hook, x_campaign, "x", links_blocked=links_blocked)
        youtube_caption = build_platform_caption(
            hook,
            youtube_campaign,
            "youtube_shorts",
            links_blocked=False,
        )
        if links_blocked and _LINK_RE.search(x_caption):
            raise CampaignPlatformError(
                f"Slot {spec['slot']} X caption still contains a link for {x_campaign.get('id')}"
            )
        slots.append(
            {
                "slot": spec["slot"],
                "time_ist": spec["time_ist"],
                "hook": hook,
                "youtube_campaign": youtube_campaign.get("id"),
                "youtube_campaign_title": youtube_campaign.get("title"),
                "youtube_rate_per_1k_views_usd": _rate(youtube_campaign),
                "youtube_remaining_pool_usd": _remaining_pool(youtube_campaign),
                "youtube_caption": youtube_caption,
                "youtube_submission_url": youtube_campaign.get("submission_url"),
                "youtube_handle": youtube_account["handle"],
                "youtube_username": youtube_account["username"],
                "x_campaign": x_campaign.get("id"),
                "x_campaign_title": x_campaign.get("title"),
                "x_rate_per_1k_views_usd": _rate(x_campaign),
                "x_remaining_pool_usd": _remaining_pool(x_campaign),
                "x_caption": x_caption,
                "x_submission_url": x_campaign.get("submission_url"),
                "x_handle": x_account["handle"],
                "overlay": overlay_plan(youtube_campaign, x_campaign),
            }
        )
    return {
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slot_count": len(slots),
        "guarantee": (
            "Every X (@chadriyo) slot is paid by a campaign whose accepted_platforms "
            "includes x. Every YouTube Shorts (Denzen / @denzen0) slot is paid by a "
            "campaign whose accepted_platforms includes youtube_shorts."
        ),
        "slots": slots,
    }


def _campaign_by_id(campaigns_data: dict, campaign_id: str) -> dict | None:
    for campaign in campaigns_data.get("active_campaigns") or []:
        if isinstance(campaign, dict) and campaign.get("id") == campaign_id:
            return campaign
    return None


def verify_manifest_data(manifest: dict, campaigns_data: dict) -> list[str]:
    """Return human-readable mapping violations. An empty list means the manifest is safe to post."""
    errors: list[str] = []
    slots = manifest.get("slots")
    if not isinstance(slots, list) or len(slots) != DAILY_SLOT_COUNT:
        errors.append(f"manifest must contain {DAILY_SLOT_COUNT} slots")
        return errors
    try:
        youtube_campaign = select_campaign_for_platform(campaigns_data, "youtube_shorts")
    except CampaignPlatformError as exc:
        errors.append(str(exc))
        return errors
    x_ids = [campaign.get("id") for campaign in campaigns_for_platform(campaigns_data, "x")]
    if not x_ids:
        errors.append("no X-paying campaign passed Pool Guard")
        return errors
    links_blocked = x_links_blocked(campaigns_data)
    seen_x: set[str] = set()
    for slot in slots:
        if not isinstance(slot, dict):
            errors.append("slot is not an object")
            continue
        slot_no = slot.get("slot")
        youtube_id = slot.get("youtube_campaign")
        x_id = slot.get("x_campaign")
        if youtube_id != youtube_campaign.get("id"):
            errors.append(
                f"slot {slot_no} youtube_campaign {youtube_id!r} != {youtube_campaign.get('id')!r}"
            )
        if youtube_id == YOUTUBE_PRIMARY_ID and "x" not in (
            youtube_campaign.get("accepted_platforms") or []
        ) and x_id == youtube_id:
            errors.append(f"slot {slot_no} pairs X with FastLoanPilot, which does not accept x")
        if x_id not in x_ids:
            errors.append(f"slot {slot_no} x_campaign {x_id!r} is not an active X-paying campaign")
            continue
        x_campaign = _campaign_by_id(campaigns_data, str(x_id))
        if x_campaign is None:
            errors.append(f"slot {slot_no} x_campaign {x_id!r} is missing from active_campaigns")
            continue
        seen_x.add(str(x_id))
        try:
            bind_clip_to_campaign(x_campaign, "x")
            bind_clip_to_campaign(youtube_campaign, "youtube_shorts")
        except CampaignPlatformError as exc:
            errors.append(f"slot {slot_no}: {exc}")
        if slot.get("youtube_submission_url") != youtube_campaign.get("submission_url"):
            errors.append(f"slot {slot_no} youtube_submission_url does not match the YouTube campaign")
        if slot.get("x_submission_url") != x_campaign.get("submission_url"):
            errors.append(f"slot {slot_no} x_submission_url does not match {x_id}")
        if float(slot.get("youtube_rate_per_1k_views_usd") or 0) != _rate(youtube_campaign):
            errors.append(f"slot {slot_no} youtube rate does not match the campaign")
        if float(slot.get("x_rate_per_1k_views_usd") or 0) != _rate(x_campaign):
            errors.append(f"slot {slot_no} x rate does not match the campaign")
        youtube_caption = str(slot.get("youtube_caption") or "")
        x_caption = str(slot.get("x_caption") or "")
        youtube_cta = str(_rules(youtube_campaign).get("mandatory_caption") or "")
        x_cta = str(_rules(x_campaign).get("mandatory_caption") or "")
        if youtube_cta and youtube_cta not in youtube_caption:
            errors.append(f"slot {slot_no} youtube_caption is missing the FastLoanPilot CTA")
        if x_cta and x_cta not in x_caption:
            errors.append(f"slot {slot_no} x_caption is missing the {x_id} CTA")
        hook = str(slot.get("hook") or "")
        if hook and hook not in x_caption:
            errors.append(f"slot {slot_no} x_caption is missing the hook")
        if hook and hook not in youtube_caption:
            errors.append(f"slot {slot_no} youtube_caption is missing the hook")
        if links_blocked and _LINK_RE.search(x_caption):
            errors.append(f"slot {slot_no} x_caption contains a link")
        if slot.get("x_submission_url") and str(slot.get("x_submission_url")) in x_caption:
            errors.append(f"slot {slot_no} x_caption includes the submission URL")
        overlay = slot.get("overlay") or {}
        x_overlay = overlay.get("x") or {}
        expected_logo = str(_rules(x_campaign).get("logo_text") or "")
        if expected_logo and x_overlay.get("logo_text") != expected_logo:
            errors.append(f"slot {slot_no} X banner logo_text != {expected_logo!r}")
        expected_sub = str(_rules(x_campaign).get("logo_subtext") or "")
        if expected_sub and x_overlay.get("logo_subtext") != expected_sub:
            errors.append(f"slot {slot_no} X banner logo_subtext does not match {x_id}")
        if overlay.get("mode") not in ("platform_specific_variants", "dual_compatible"):
            errors.append(f"slot {slot_no} overlay mode is missing")
        if slot.get("x_handle") != platform_account(campaigns_data, "x")["handle"]:
            errors.append(f"slot {slot_no} x_handle is not @chadriyo")
        if slot.get("youtube_handle") != platform_account(campaigns_data, "youtube_shorts")["handle"]:
            errors.append(f"slot {slot_no} youtube_handle is not Denzen")
    missing = [campaign_id for campaign_id in x_ids if campaign_id not in seen_x]
    if missing:
        errors.append("daily manifest never assigns X views to: " + ", ".join(missing))
    return errors


def write_manifest(path: Path | None = None, campaigns_data: dict | None = None) -> dict:
    data = campaigns_data or load_campaigns()
    manifest = build_daily_manifest(data)
    errors = verify_manifest_data(manifest, data)
    if errors:
        raise CampaignPlatformError("; ".join(errors))
    save_json(path or MANIFEST_PATH, manifest)
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Whop platform campaign mapping")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-manifest", help="Write whop/manifest.json for the 10 daily slots")
    sub.add_parser("verify-manifest", help="Fail if any slot pairs a platform with a campaign that does not pay it")
    args = parser.parse_args(argv)
    data = load_campaigns()
    if args.command == "build-manifest":
        manifest = write_manifest(campaigns_data=data)
        print(f"wrote {MANIFEST_PATH} ({manifest['slot_count']} slots)")
        return 0
    if args.command == "verify-manifest":
        if not MANIFEST_PATH.exists():
            print(f"missing {MANIFEST_PATH}", file=sys.stderr)
            return 1
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        errors = verify_manifest_data(manifest, data)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"manifest OK ({manifest.get('slot_count')} slots)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
