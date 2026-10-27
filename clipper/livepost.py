"""Post one due clip per platform through Buffer's GraphQL API.

The dry-run still builds example.invalid payloads and never calls this.
A live run only sends an https URL for a file fetch-media actually downloaded.
The same source is posted at most once per platform. A Buffer or account
error pauses that platform. A video URL we cannot fetch is skipped instead.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from clipper.alerts import alert
from clipper.buffer_api import BufferError, ErrorBudget, build_post_input, create_post, due_stamp
from clipper.engine import (
    build_caption,
    content_plan_caption,
    normalize_caption,
    rotated_hashtags,
    variant_for,
)
from clipper.fetchsrc import fetch_sui_clip
from clipper.guard import blocked_reason, prune_topics, remember_topics
from clipper.loadconfig import posting_platforms
from clipper.mediahost import MediaURLError, is_media_url_error, pages_video_url, publish_live_pages, verify_public_mp4
from clipper.render import apply_filters, ffmpeg_available, logo_filter, x_overlay_graph, x_overlay_text
from clipper.run import _channel_id
from clipper.safety import classify
from clipper.strikes import (
    FAILED_STATUSES,
    alert_text,
    blocked_identities,
    empty_alerts,
    is_paused,
    pause_platform,
    record_media_url,
    record_safety,
)
from clipper.submissions import empty_pending, load_json, record_sent_posts, save_json

HORIZON_MINUTES = 120
GRACE_MINUTES = 20
CLIP_SECONDS = 40.0
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
_USE_DEFAULT_FETCHER = object()


def due_slots(
    now: datetime,
    clock_times: list[str],
    timezone_name: str,
    *,
    horizon_minutes: int = HORIZON_MINUTES,
    grace_minutes: int = GRACE_MINUTES,
) -> list[datetime]:
    """Peak slots from `grace` minutes ago through `horizon` minutes ahead.

    The live cron is every two hours. Slot spacing is about 2h15, so a two-hour
    window contains one slot. The grace covers a runner that starts just after
    the clock time. Slots inside the quiet gap fall outside the window.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    found: list[datetime] = []
    for value in clock_times:
        hour, minute = (int(part) for part in value.split(":"))
        for shift in (-1, 0, 1):
            day = (local + timedelta(days=shift)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            found.append(day.astimezone(timezone.utc))
    window_start = now - timedelta(minutes=grace_minutes)
    window_end = now + timedelta(minutes=horizon_minutes)
    chosen = [slot for slot in found if window_start <= slot <= window_end]
    return sorted(set(chosen))


def slot_key(platform: str, slot: datetime) -> str:
    return f"{platform}:{due_stamp(slot)}"


def source_key(platform: str, identity: str) -> str:
    return f"{platform}:{identity}"


def empty_ledger() -> dict:
    return {"version": 1, "posted": [], "sources": [], "captions": [], "topics": []}


def prune_ledger(ledger: dict, now: datetime) -> dict:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=3)
    kept = []
    for row in ledger.get("posted") or []:
        raw = str(row.get("due_at") or "")
        if not raw:
            continue
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment >= cutoff:
            kept.append(row)
    ledger["posted"] = kept
    ledger["sources"] = list(ledger.get("sources") or [])
    ledger["captions"] = list(ledger.get("captions") or [])
    prune_topics(ledger, now, 7)
    ledger["version"] = 1
    return ledger


def ledger_has(ledger: dict, platform: str, slot: datetime) -> bool:
    key = slot_key(platform, slot)
    return any(row.get("key") == key for row in ledger.get("posted") or [])


def posted_identities(ledger: dict, platform: str) -> set[str]:
    prefix = f"{platform}:"
    found: set[str] = set()
    for key in ledger.get("sources") or []:
        text = str(key)
        if text.startswith(prefix):
            found.add(text[len(prefix):])
    return found


def prefetch_exclude_ids(ledger: dict, platforms: list[str]) -> set[str]:
    """Drive ids already posted on every listed platform.

    A file posted only to YouTube can still be the TikTok cut. Skip a download
    only when every platform in the list has already used that id.
    """
    groups = [posted_identities(ledger, platform) for platform in platforms]
    if not groups:
        return set()
    return set.intersection(*groups)


def public_video_url(url: str) -> str:
    """Buffer can fetch only a public https file. Dry-run hosts are refused."""
    text = (url or "").strip()
    if "example.invalid" in text or not text.startswith("https://"):
        raise BufferError("refusing a video URL Buffer cannot fetch")
    if text.startswith("https://example.invalid"):
        raise BufferError("refusing a video URL Buffer cannot fetch")
    return text


def release_download_url(repo: str, tag: str, name: str) -> str:
    return public_video_url(f"https://github.com/{repo}/releases/download/{tag}/{name}")


def upload_release_asset(path: Path, name: str, *, repo: str, tag: str, token: str) -> str:
    """Publish one mp4 on the clip-media release and return its public URL."""
    if not repo or not token:
        raise RuntimeError("GH_TOKEN and GITHUB_REPOSITORY are required to publish media")
    if "/" in name or name.startswith(".") or "example.invalid" in name:
        raise RuntimeError("refusing release asset name")
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    view = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if view.returncode != 0:
        created = subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                repo,
                "--prerelease",
                "--title",
                "Clip media",
                "--notes",
                "Live clips for Buffer. Buffer is not called from the release itself.",
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "gh release create failed")[-400:]
            raise RuntimeError(detail)
    staged = Path(path).with_name(name)
    if staged != Path(path):
        shutil.copyfile(path, staged)
    uploaded = subprocess.run(
        ["gh", "release", "upload", tag, str(staged), "--repo", repo, "--clobber"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if uploaded.returncode != 0:
        detail = (uploaded.stderr or uploaded.stdout or "gh release upload failed")[-400:]
        raise RuntimeError(detail)
    return release_download_url(repo, tag, name)


def _campaign(bundle: dict, campaign_id: str) -> dict | None:
    for row in bundle["campaigns"]["campaigns"]:
        if row["id"] == campaign_id:
            return row
    return None


def _remember(ledger: dict, platform: str, identity: str, caption: str, slot: datetime, post: dict, video_url: str, campaign_id: str | None) -> None:
    key = source_key(platform, identity)
    if key not in ledger["sources"]:
        ledger["sources"].append(key)
    normalized = normalize_caption(caption)
    if not any(row.get("source") == identity and row.get("text") == normalized for row in ledger["captions"]):
        ledger["captions"].append({"platform": platform, "source": identity, "text": normalized})
    ledger["posted"].append(
        {
            "key": slot_key(platform, slot),
            "platform": platform,
            "due_at": due_stamp(slot),
            "source_key": key,
            "buffer_post_id": post.get("id") or "",
            "video_url": video_url,
            "campaign_id": campaign_id,
            "status": post.get("status") or "",
        }
    )


def _call_fetcher(fetcher, exclude: set[str]) -> dict | None:
    if fetcher is None:
        return None
    try:
        fetched = fetcher(set(exclude))
    except Exception as exc:
        print(f"Sui download skipped: {exc.__class__.__name__}: {exc}")
        return None
    if not isinstance(fetched, dict) or fetched.get("status") == "exhausted" or not fetched.get("path"):
        return None
    return fetched


def _dhan_videos(media_dir: Path) -> list[Path]:
    folder = media_dir / "dhan"
    if not folder.is_dir():
        return []
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in _VIDEO_SUFFIXES
    ]


def _gather_candidates(
    platform: str,
    bundle: dict,
    manifest: dict,
    media_dir: Path,
    ledger: dict,
    alerts: dict,
    fetcher,
) -> list[tuple]:
    """Sui file, then an approved Dhan file, then the content plan. Used files are skipped."""
    used = set(ledger.get("sources") or [])
    blocked = blocked_identities(alerts)
    items: list[tuple] = []

    def add(kind: str, path: Path, identity: str, campaign: dict | None, title: str = "") -> bool:
        if not identity or not path.is_file():
            return False
        if identity in blocked or source_key(platform, identity) in used:
            return False
        if kind == "content_plan":
            needle = f":{identity}"
            if any(str(key) == identity or str(key).endswith(needle) for key in used):
                return False
        items.append((kind, path, identity, campaign, title or path.stem))
        return True

    sui = _campaign(bundle, "sui-basecamp") or {}
    if sui.get("active") and float((sui.get("budget_snapshot") or {}).get("remaining_usd", 1)) > 0 and platform in (sui.get("platforms") or []):
        meta = manifest.get("sui") or {}
        path = Path(str(meta.get("path") or ""))
        if not path.is_file():
            short = media_dir / "sui-basecamp-short.mp4"
            path = short if short.is_file() else path
        identity = str(meta.get("file_id") or (path.stem if path.is_file() else ""))
        if not add("sui", path, identity, sui):
            exclude = posted_identities(ledger, platform) | blocked
            fetched = _call_fetcher(fetcher, exclude)
            if fetched:
                add(
                    "sui",
                    Path(str(fetched.get("path") or "")),
                    str(fetched.get("file_id") or Path(str(fetched.get("path") or "")).stem),
                    sui,
                )

    dhan = _campaign(bundle, "dhan-aziz") or {}
    approved = {str(item) for item in (bundle.get("approvals") or {}).get("approved") or []}
    if dhan.get("active") and float((dhan.get("budget_snapshot") or {}).get("remaining_usd", 1)) > 0 and platform in (dhan.get("platforms") or []) and approved:
        for path in _dhan_videos(media_dir):
            if path.stem not in approved and path.name not in approved:
                continue
            add("dhan", path, path.stem, dhan)

    shared = {platform, "youtube", "tiktok"} if platform == "x" else {platform}
    for row in manifest.get("content_plan") or []:
        if row.get("platform") not in shared or row.get("status") != "downloaded":
            continue
        path = Path(str(row.get("path") or ""))
        add("content_plan", path, path.stem, None, str(row.get("title") or path.stem))
    return items


CAPTION_POOL = (
    "That suit fits tighter than my budget 💀",
    "Me planning the perfect date vs her saying \"I'm tired\" 💀",
    "He said one more episode. It's 4am 😭",
    "Bro fell in love in 3 seconds and I respect it 💀",
    "Her: you're not even listening. Me during this scene 😭",
    "This is my villain origin story and I'm not okay 💀",
    "Rent is due but this scene is worth it 😭",
    "POV: you said you'd sleep early 💀",
    "Main character energy on a side character budget 😭",
    "Nobody told me the plot would hit this hard 💀",
    "My standards after watching this one 😭",
    "Told myself I'm over it. Then this played 💀",
    "She looked at him once and he forgot his own name 💀",
    "My ex walking past like this scene 😭",
    "Bro's confidence is higher than my salary 💀",
    "That look should be illegal 😭",
    "Me pretending I'm not attached to fictional people 💀",
    "He fixed his hair and the whole room lost it 😭",
    "This hit harder than my last relationship 💀",
    "Rizz level: unpaid internship 😭",
    "She said \"we need to talk\" and he did THIS 💀",
    "My mom calling while I'm watching this part 😭",
    "Single for a reason and this is the reason 💀",
    "When the side chick gets more screen time 😭",
    "He's not the villain, he just has taste 💀",
    "My wallet watching me buy merch after this 😭",
    "Bro said \"trust me\" and the plot said no 💀",
    "This scene owes me emotional damages 😭",
    "Me acting cool after replaying this 12 times 💀",
    "Her glow up came with a warning label 😭",
    "Red flag but make it fashionable 💀",
    "He walked in and my standards walked out 😭",
    "That's not a smile, that's a threat 💀",
    "Brain: sleep. Me: one more clip 😭",
    "If looks could kill this whole cast is in jail 💀",
    "He really said \"I got this\" with zero plan 😭",
    "My crush texting back like this scene 💀",
    "She didn't even try and still won 😭",
    "The audacity of this man 💀",
    "Delulu is the solulu and this proves it 😭",
    "I'd fumble this so fast it's embarrassing 💀",
    "When the background character steals the show 😭",
    "That outfit cost more than my whole personality 💀",
    "Bro said \"I'm fine\" and then did this 😭",
    "This is why I have trust issues 💀",
    "Watching this instead of studying again 😭",
    "Not him being down bad on main 💀",
    "She knew exactly what she was doing 😭",
    "Built different, broke the same as me 💀",
    "This aged like milk and I'm still watching 😭",
)


def _pool_caption(platform: str, clip: dict, char_limit: int, limits: dict, used: set[str], tags: list[str], salt: str) -> str:
    start = int(hashlib.sha256(salt.encode("utf-8")).hexdigest()[:8], 16)
    for index in range(len(CAPTION_POOL)):
        line = CAPTION_POOL[(start + index) % len(CAPTION_POOL)]
        trial = content_plan_caption({**clip, "title": line}, platform, char_limit, limits=limits, used=None, extra_hashtags=tags)
        if normalize_caption(trial) not in used:
            used.add(normalize_caption(trial))
            return trial
    raise RuntimeError(f"caption pool exhausted for {platform}; add lines to CAPTION_POOL")


def _caption_for(
    kind: str,
    platform: str,
    source: Path,
    identity: str,
    bundle: dict,
    limits: dict,
    used: set[str],
    salt: str,
    campaign: dict | None,
    clip_title: str = "",
) -> tuple[dict | None, str]:
    char_limit = int(bundle["pipeline"]["x_char_limit"])
    title = clip_title or source.stem
    if platform == "x" and kind not in {"sui", "dhan"}:
        title = x_overlay_text(title, salt)
    clip = {"source_id": identity, "title": title}
    if kind in {"sui", "dhan"}:
        if campaign is None:
            raise RuntimeError(f"{kind} campaign is missing")
        disclosure = ((campaign.get("caption") or {}).get("disclosure") or "").strip()
        reserved = 1 if disclosure.startswith("#") else 0
        tags = [] if kind == "sui" else rotated_hashtags(limits, salt, reserved=reserved)
        campaign = copy.deepcopy(campaign)
        existing = list(campaign["caption"].get("hashtags") or [])
        for tag in tags:
            if tag not in existing:
                existing.append(tag)
        campaign["caption"]["hashtags"] = existing
        caption = build_caption(campaign, clip, platform, char_limit, limits=limits, used=used)
        if kind == "sui" and "x.com/SuiNetwork" not in caption:
            raise RuntimeError("Sui caption is missing x.com/SuiNetwork")
        if kind == "dhan" and "@DHANAZIZ" not in caption:
            raise RuntimeError("Dhan Aziz caption is missing @DHANAZIZ")
        return campaign, caption
    tags = rotated_hashtags(limits, salt, reserved=0)
    caption = _pool_caption(platform, clip, char_limit, limits, used, tags, salt)
    if caption.startswith("#Ad") or "x.com/SuiNetwork" in caption:
        raise RuntimeError("content-plan caption picked up a campaign line")
    return None, caption


def render_variant(source: Path, dest: Path, platform: str, campaign: dict | None) -> None:
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed")
    variant = variant_for(platform)
    rules = campaign if campaign and (campaign.get("logo") or {}).get("required") else {"logo": {"required": False}}
    graph, uses_logo = logo_filter(rules, 720, 1280, float(variant["crop_x"]))
    if "drawtext" in graph:
        raise RuntimeError("live render must not stamp text")
    logo_path = Path(str(rules["logo"]["file"])) if uses_logo else None
    apply_filters(source, dest, graph, logo_path, CLIP_SECONDS, float(variant["start_sec"]))


def _asset_name(platform: str, slot: datetime) -> str:
    stamp = due_stamp(slot).replace("-", "").replace(":", "")
    return f"live-{platform}-{stamp}.mp4"


def live_payload(package: dict, channel_id: str, video_url: str, now: datetime) -> dict:
    """GraphQL createPost input. A slot that just passed is shareNow, not a past dueAt."""
    url = public_video_url(video_url)
    payload = build_post_input(package, channel_id, url)
    due = datetime.fromisoformat(str(package["due_at"]).replace("Z", "+00:00"))
    if due <= now.astimezone(timezone.utc) + timedelta(seconds=30):
        payload["mode"] = "shareNow"
        payload.pop("dueAt", None)
    if "example.invalid" in json.dumps(payload):
        raise BufferError("refusing a video URL Buffer cannot fetch")
    return payload


def _next_slot(platform: str, bundle: dict, now: datetime, ledger: dict) -> datetime | None:
    schedule = bundle["pipeline"]["schedule"][platform]
    slots = due_slots(now, schedule["slots"], schedule["timezone"])
    for slot in slots:
        if not ledger_has(ledger, platform, slot):
            return slot
    return None


def _download_next_sui(bundle: dict, media_dir: Path, exclude: set[str]) -> dict | None:
    sui = _campaign(bundle, "sui-basecamp") or {}
    url = str((sui.get("source") or {}).get("url") or "")
    if not url:
        return None
    return fetch_sui_clip(url, media_dir, exclude_ids=exclude)


def _used_captions(ledger: dict, identity: str) -> set[str]:
    return {
        str(row.get("text") or "")
        for row in ledger.get("captions") or []
        if row.get("source") != identity and row.get("text")
    }


def execute_live(
    bundle: dict,
    *,
    now: datetime,
    env: dict,
    media_dir: Path,
    output_dir: Path,
    uploader=None,
    poster=None,
    fetcher=_USE_DEFAULT_FETCHER,
    ledger_path: Path | None = None,
    pending_path: Path | None = None,
    alerts_path: Path | None = None,
    publisher=None,
    url_check=None,
) -> int:
    """Send at most one post per enabled platform for the slot due in this window."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    token = (env.get("BUFFER_API_TOKEN") or "").strip()
    if not token:
        print("Refusing a live post. BUFFER_API_TOKEN is empty.", flush=True)
        return 2
    api = bundle["pipeline"]["buffer_api_url"]
    if api.rstrip("/") != "https://api.buffer.com":
        print(f"Refusing a live post. Buffer API must be GraphQL at https://api.buffer.com, got {api}.", flush=True)
        return 2
    manifest_path = media_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"sui": {}, "content_plan": []}
    repo = (env.get("GITHUB_REPOSITORY") or "").strip()
    tag = str(bundle["pipeline"].get("release_tag") or "clip-media")
    gh_token = (env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or "").strip()
    own_uploader = uploader is None
    if own_uploader:
        def uploader(path, name, _repo=repo, _tag=tag, _token=gh_token):
            upload_release_asset(path, name, repo=_repo, tag=_tag, token=_token)
            return pages_video_url(_repo, name)

        if publisher is None:
            publisher = publish_live_pages
        if url_check is None:
            url_check = verify_public_mp4
    else:
        publisher = None
        if url_check is None:
            url_check = lambda url: None
    poster = poster or (lambda post_input: create_post(api, token, post_input))
    retry_check = url_check is verify_public_mp4
    if fetcher is _USE_DEFAULT_FETCHER:
        fetcher = lambda exclude: _download_next_sui(bundle, media_dir, exclude)

    root: Path = bundle["root"]
    ledger_path = ledger_path or (root / "state" / "live.json")
    ledger = prune_ledger(load_json(ledger_path, empty_ledger()), now)
    pending_path = pending_path or (root / bundle["pipeline"]["submissions"]["pending_path"])
    pending = load_json(
        pending_path,
        empty_pending(bundle["pipeline"]["submissions"]["raw_url"], int(bundle["pipeline"]["submit_within_minutes"])),
    )
    alerts_path = alerts_path or (root / "state" / "platform_alerts.json")
    alerts = load_json(alerts_path, empty_alerts())
    minutes = int(bundle["pipeline"]["submit_within_minutes"])
    limit = int((bundle["pipeline"].get("rate_limit") or {}).get("max_errors_before_stop") or 2)
    budget = ErrorBudget(limit)
    output_dir.mkdir(parents=True, exist_ok=True)
    variants = output_dir / "variants"
    sent_rows: list[dict] = []
    failures = 0
    posted = 0
    ready: list[dict] = []

    def persist() -> None:
        save_json(ledger_path, ledger)
        save_json(pending_path, pending)
        save_json(alerts_path, alerts)

    def skip_media(platform_name: str, reason: str) -> None:
        nonlocal failures
        failures += 1
        if record_media_url(alerts, platform_name, reason, when=now):
            message = f"{platform_name} skipped this slot: {reason}. The platform is not paused."
            print(message)
            alert(message)
        else:
            print(f"{platform_name} skipped this slot: {reason}. The platform is not paused.")
        persist()

    for platform in posting_platforms(bundle):
        if is_paused(alerts, platform) or platform in budget.halted:
            print(f"{platform}: paused, not posting")
            continue
        slot = _next_slot(platform, bundle, now, ledger)
        if slot is None:
            print(f"{platform}: no peak slot in this window")
            continue
        limits = (bundle["pipeline"].get("platform_limits") or {}).get(platform) or {}
        chosen = None
        campaign = None
        caption = ""
        kind = ""
        source = Path()
        identity = ""
        clip_title = ""
        unsafe = False
        cooled = False
        for candidate in _gather_candidates(platform, bundle, manifest, media_dir, ledger, alerts, fetcher):
            kind, source, identity, campaign_hint = candidate[:4]
            clip_title = candidate[4] if len(candidate) > 4 else source.stem
            try:
                campaign, caption = _caption_for(
                    kind,
                    platform,
                    source,
                    identity,
                    bundle,
                    limits,
                    _used_captions(ledger, identity),
                    identity,
                    campaign_hint,
                    clip_title,
                )
            except Exception as exc:
                failures += 1
                message = f"live caption {platform} failed: {exc.__class__.__name__}: {exc}"
                print(message)
                alert(message)
                chosen = None
                break
            guard_ids = (identity,) if str(identity).isdigit() else ()
            blocked = blocked_reason(
                root,
                f"{source.name}\n{source.stem}\n{clip_title}\n{identity}\n{caption}",
                ledger,
                now,
                guard_ids,
            )
            if blocked:
                cooled = True
                print(f"{platform} skipped {source_key(platform, identity)}: {blocked}. The platform is not paused.")
                continue
            hit = classify(f"{source.name}\n{source.stem}\n{caption}")
            if hit:
                unsafe = True
                key = source_key(platform, identity)
                if record_safety(alerts, platform, key, hit, when=now):
                    message = f"{platform} skipped {key}: safety category {hit}. The platform is not paused."
                    print(message)
                    alert(message)
                persist()
                continue
            chosen = candidate
            break
        if chosen is None:
            if unsafe:
                print(f"{platform}: remaining files were blocked by the safety filter, not posting")
            elif cooled:
                print(f"{platform}: remaining files are in the topic cooldown, not posting")
            else:
                print(f"{platform}: no unused safe file, not posting")
            continue
        kind, source, identity, _hint = chosen[:4]
        clip_title = chosen[4] if len(chosen) > 4 else source.stem
        try:
            upload_path = source
            if platform == "x" and kind != "sui":
                from clipper.publicsrc import real_video

                if real_video(source):
                    dest = variants / f"{platform}.mp4"
                    line = x_overlay_text("", source_key(platform, identity))
                    apply_filters(source, dest, x_overlay_graph(line), None, 15.0, 0.0)
                    upload_path = dest
            elif kind == "sui":
                dest = variants / f"{platform}.mp4"
                render_variant(source, dest, platform, campaign)
                upload_path = dest
            video_url = public_video_url(uploader(upload_path, _asset_name(platform, slot)))
            channel = _channel_id(bundle["channels"], campaign or {"account_mode": "shared"}, platform, env)
            if not channel:
                raise RuntimeError(f"no Buffer channel id for {platform}")
            package = {
                "platform": platform,
                "status": "scheduled",
                "caption": caption,
                "due_at": due_stamp(slot),
                "campaign_id": (campaign or {}).get("id"),
                "source_id": identity,
                "content_rewards_campaign_id": (campaign or {}).get("content_rewards_campaign_id") or "",
                "submit_page": (campaign or {}).get("submit_page") or "",
            }
            payload = live_payload(package, channel, video_url, now)
        except MediaURLError as exc:
            skip_media(platform, str(exc))
            continue
        except Exception as exc:
            failures += 1
            message = f"live post {platform} failed before Buffer: {exc.__class__.__name__}: {exc}"
            print(message)
            alert(message)
            continue
        ready.append(
            {
                "platform": platform,
                "slot": slot,
                "identity": identity,
                "caption": caption,
                "video_url": video_url,
                "package": package,
                "payload": payload,
                "source": source,
            }
        )

    if ready and publisher is not None:
        try:
            publisher(repo, gh_token)
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"
            for item in ready:
                skip_media(item["platform"], detail)
            ready = []

    def verify(url: str) -> None:
        attempts = 4 if retry_check else 1
        last: Exception | None = None
        for index in range(attempts):
            try:
                url_check(url)
                return
            except MediaURLError as exc:
                last = exc
                if index + 1 < attempts:
                    time.sleep(15)
        if last is not None:
            raise last

    for item in ready:
        platform = item["platform"]
        if is_paused(alerts, platform) or platform in budget.halted:
            print(f"{platform}: paused, not posting")
            continue
        identity = item["identity"]
        caption = item["caption"]
        video_url = item["video_url"]
        package = item["package"]
        source = item["source"]
        slot = item["slot"]
        try:
            verify(video_url)
        except MediaURLError as exc:
            skip_media(platform, str(exc))
            continue
        try:
            post = poster(item["payload"])
        except Exception as exc:
            if is_media_url_error(str(exc)) or isinstance(exc, MediaURLError):
                skip_media(platform, f"{exc.__class__.__name__}: {exc}")
                continue
            failures += 1
            reason = f"{exc.__class__.__name__}: {exc}"
            pause_platform(alerts, platform, reason, "buffer_error", when=now)
            message = alert_text(platform, "buffer_error", reason)
            print(message)
            alert(message)
            budget.record(platform, str(exc))
            persist()
            continue
        status = str(post.get("status") or "").strip().lower()
        if status in FAILED_STATUSES:
            failures += 1
            kind_name = "removed" if status in {"removed", "deleted"} else "buffer_error"
            reason = f"Buffer reported status {status}"
            post_id = str(post.get("id") or "")
            pause_platform(alerts, platform, reason, kind_name, buffer_post_id=post_id, when=now)
            message = alert_text(platform, kind_name, reason, post_id)
            print(message)
            alert(message)
            persist()
            continue
        posted += 1
        _remember(ledger, platform, identity, caption, slot, post, video_url, package["campaign_id"])
        remember_topics(ledger, f"{source.stem}\n{identity}\n{caption}", slot)
        text = post.get("text") or caption
        enriched = dict(post)
        enriched["text"] = text
        if enriched.get("status") == "sent":
            record_sent_posts(pending, [enriched], platform, bundle["campaigns"]["campaigns"], minutes)
        sent_rows.append(
            {
                "platform": platform,
                "due_at": package["due_at"],
                "video_url": video_url,
                "buffer_post_id": post.get("id") or "",
                "status": post.get("status") or "",
                "campaign_id": package["campaign_id"],
                "source_key": source_key(platform, identity),
            }
        )
        print(f"posted {platform} {package['due_at']} {post.get('id') or ''}")
        persist()

    persist()
    (output_dir / "live.json").write_text(
        json.dumps({"posted": sent_rows, "failures": failures}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"live posted {posted} failure {failures}")
    return 1 if failures else 0
