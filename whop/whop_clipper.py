#!/usr/bin/env python3
"""Keyless Ultra-HD/4K-Crisp Whop Content Rewards & Viral Auto-Clipper.

1. Enforces Whop Pool/Budget Guard (`check_campaign_pool_guard`) so we NEVER clip
   for empty, paused, high-denial-rate (for example Shane Gillis at 99.8%), or
   nearly exhausted (<$500 remaining or >80% paid out) Whop campaigns/bounties.
2. Maps each post to a campaign that explicitly pays that platform. YouTube
   Shorts (`Denzen` / `@denzen0`) uses Dishpilot and Mr. Pa. Instagram Reels
   (`@benreelr`) uses Rascals, Dishpilot, and Mr. Pa. X (`@chadriyo`) uses
   Dishpilot. FastLoanPilot and Nova are closed. `build-daily-40` (alias
   `build-daily-30`) selects 40 unseen clips, 10 per platform, stamped Dishpilot or
   Rascals, and, on the daily GitHub Actions run, commits the feed.
3. Focuses on high-retention lanes for YouTube Shorts (`Denzen` / `@denzen0`)
   and X (`@chadriyo`):
   - `anime`: 4K/HD anime fight & emotional edits
   - `movie`: iconic cinema & movie/TV scenes
   - `podcast`: funny stand-up (Shane Gillis, etc.) & high-retention viral challenges
4. Downloads the highest available bitrate/resolution via `yt-dlp`
   (`bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best`) or clean raw source.
5. Encodes Ultra-Crisp 1080x1920 (9:16 vertical for YouTube Shorts `Denzen` &
   X `@chadriyo`) using Lanczos scaling, CAS/unsharp 4K-style contrast-adaptive
   sharpening, CRF 17, the fast preset, 192k AAC audio, persistent scroll-stopping top hook
   banner, one subtle corner campaign badge, and no second center logo.
6. Zero API keys or secrets in code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import clipper as platform_map
except ImportError:
    from whop import clipper as platform_map

PLATFORM_ELIGIBLE_CAMPAIGNS = platform_map.PLATFORM_ELIGIBLE_CAMPAIGNS

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CAMPAIGNS_PATH = ROOT / "campaigns.json"
FEED_PATH = ROOT / "feed" / "whop_ready_feed.json"
DAILY_FEED_PATH = ROOT / "feed" / "daily_30_ready.json"
DAILY_30_SCHEDULE_PATH = ROOT / "daily_30_ready.json"
DAILY_40_SCHEDULE_PATH = ROOT / "daily_40_ready.json"
DAILY_40_FEED_PATH = ROOT / "feed" / "daily_40_ready.json"
DAILY_SEEN_PATH = ROOT / "state" / "seen_daily.json"
DAILY_RENDER_DIR = ROOT / "work" / "daily_render"
DAILY_RELEASE_PREFIX = "daily-"
DAILY_RELEASE_KEEP_DAYS = 2
DEFAULT_REPO_SLUG = "chatgpt820970/chad-media"
URLS_QUEUE_PATH = ROOT / "urls_queue.json"
INGEST_MEDIA_DIR = ROOT / "v"
MANIFEST_PATH = ROOT / "manifest.json"
BUFFER_GRAPHQL_URL = "https://api.buffer.com/graphql"
_BUFFER_MUTATION = (
    "mutation CreatePost($input: CreatePostInput!) { "
    "createPost(input: $input) { __typename } }"
)
SHORTS_FEED_PATH = REPO_ROOT / "shorts" / "feed" / "shorts_queue.json"
LOGOS_DIR = ROOT / "logos"
WORK_DIR = ROOT / "work"
MEDIA_DIR = WORK_DIR / "media"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[whop-clipper {stamp}] {msg}", flush=True)


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def campaign_passes_pool_guard(campaign: dict, rules: dict) -> tuple[bool, str]:
    """Pool guard plus the denial-rate cap. See clipper.check_campaign_pool_guard."""
    return platform_map.check_campaign_pool_guard(campaign, rules)


def select_healthy_campaigns(lane: str | None = None) -> list[dict]:
    cfg = load_json(CAMPAIGNS_PATH, {})
    rules = cfg.get("pool_guard_rules") or {}
    healthy: list[dict] = []
    for camp in cfg.get("active_campaigns") or []:
        ok, reason = campaign_passes_pool_guard(camp, rules)
        if not ok:
            log(f"SKIP campaign {camp.get('id')}: {reason}")
            continue
        if lane and lane not in (camp.get("allowed_lanes") or []):
            continue
        camp_copy = dict(camp)
        camp_copy["pool_guard_status"] = reason
        healthy.append(camp_copy)
    return healthy


def find_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/sand-box/google/Barlow Semi Condensed/BarlowSemiCondensed-Black.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return "Sans"


def download_hd_source(source_url: str, dest: Path) -> None:
    """Download highest quality HD/4K video stream using yt-dlp."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    ytdlp = shutil.which("yt-dlp") or "yt-dlp"
    cmd = [
        ytdlp,
        "-f",
        platform_map.YTDLP_FORMAT,
        "-S",
        platform_map.YTDLP_SORT,
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "30",
        "-o",
        str(dest),
        source_url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    if not dest.exists():
        matches = [p for p in dest.parent.glob(dest.stem + ".*") if p.is_file()]
        if matches:
            matches[0].replace(dest)
    if res.returncode != 0 or not dest.exists() or dest.stat().st_size < 100_000:
        if source_url.endswith(".mp4"):
            req = urllib.request.Request(source_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                dest.write_bytes(resp.read())
        else:
            raise RuntimeError(f"HD download failed for {source_url}: {(res.stderr or '')[-300:]}")


def probe_video(path: Path) -> dict:
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data = json.loads(res.stdout or "{}")
    width = height = 0
    vcodec = ""
    audio = False
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and not vcodec:
            vcodec = str(stream.get("codec_name") or "")
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        elif stream.get("codec_type") == "audio":
            audio = True
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    return {"width": width, "height": height, "vcodec": vcodec, "audio": audio, "duration": duration}


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "")
        .replace("%", "%%%%")
    )


def encode_ultra_crisp_whop(
    src: Path,
    dest: Path,
    hook_text: str = "",
    watermark_text: str = "",
    logo_path: Path | None = None,
    logo_text: str = "",
    logo_subtext: str = "",
) -> None:
    """Encode 1080x1920 9:16 vertical MP4 with Lanczos scaling + CAS/unsharp 4K-style crispness."""
    info = probe_video(src)
    width, height = int(info["width"]), int(info["height"])
    portrait = (height / max(width, 1)) >= 1.45
    font_path = find_font()

    # 4K-style crispness filter chain: Lanczos upscale + unsharp luma/chroma sharpening + contrast boost
    crisp_filter = "unsharp=5:5:0.85:3:3:0.4,eq=contrast=1.04:saturation=1.08"

    if portrait:
        base_chain = (
            f"[0:v]scale=1080:1920:flags=lanczos:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,{crisp_filter},setsar=1[vbase]"
        )
    else:
        base_chain = (
            f"[0:v]scale=1080:1920:flags=lanczos:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur=28:3,eq=brightness=-0.08[bg];"
            f"[0:v]scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,{crisp_filter}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vbase]"
        )

    current_label = "[vbase]"
    chains = [base_chain]

    # 1. Clean top hook. No full-width banner behind it.
    if hook_text:
        safe_hook = escape_drawtext(hook_text.upper()[:46])
        chains.append(
            f"{current_label}drawtext=fontfile='{font_path}':text='{safe_hook}':"
            f"fontcolor=white:fontsize=46:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=78[vhook]"
        )
        current_label = "[vhook]"

    # 2. At most one subtle corner badge. Never a second line and never a center stack.
    inputs = ["-i", str(src)]
    if logo_path and logo_path.exists():
        inputs.extend(["-i", str(logo_path)])
        chains.append(
            f"[1:v]scale=200:-1:flags=lanczos,format=rgba[logo];"
            f"{current_label}[logo]overlay=W-w-36:H-h-48[vlogo]"
        )
        current_label = "[vlogo]"
    elif logo_text or watermark_text:
        badge = logo_text or watermark_text
        banner_filter, current_label = platform_map.ffmpeg_banner_filter(
            {"logo_text": badge, "logo_count": 1, "logo_placement": "corner"},
            current_label,
            font_path,
        )
        if banner_filter:
            chains.append(banner_filter)

    filter_complex = ";".join(chains)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        current_label,
        "-map",
        "0:a:0",
        "-af",
        "dynaudnorm=f=150:g=15",
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-level:v",
        "4.2",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "fast",
        "-crf",
        "17",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg ultra-crisp encode failed: {(res.stderr or '')[-500:]}")


def _rejected_by_pool_guard(cfg: dict) -> list[dict]:
    rules = cfg.get("pool_guard_rules") or {}
    rejected: list[dict] = []
    for campaign in cfg.get("active_campaigns") or []:
        ok, reason = platform_map.check_campaign_pool_guard(campaign, rules)
        if ok:
            continue
        rejected.append(
            {
                "id": campaign.get("id"),
                "title": campaign.get("title"),
                "denial_rate": campaign.get("denial_rate"),
                "reason": reason,
            }
        )
    return rejected


def _apply_platform_mapping(clips: list[dict], cfg: dict) -> None:
    """Attach a YouTube payer and a separate X payer. Never pair @chadriyo with FastLoanPilot."""
    youtube = platform_map.bind_clip_to_campaign(
        platform_map.select_campaign_for_platform(cfg, "youtube_shorts"),
        "youtube_shorts",
    )
    x_campaigns = [
        platform_map.bind_clip_to_campaign(campaign, "x")
        for campaign in platform_map.campaigns_for_platform(cfg, "x")
    ]
    if not x_campaigns:
        raise platform_map.CampaignPlatformError(
            "No Pool-Guard-passing campaign accepts x (@chadriyo)."
        )
    links_blocked = platform_map.x_links_blocked(cfg)
    for index, clip in enumerate(clips):
        x_campaign = x_campaigns[index % len(x_campaigns)]
        preferred_id = str(clip.get("youtube_campaign") or "")
        preferred = platform_map._campaign_by_id(cfg, preferred_id) if preferred_id else None
        preferred_ok = False
        if preferred is not None:
            preferred_ok, _reason = platform_map.check_campaign_pool_guard(
                preferred, cfg.get("pool_guard_rules") or {}
            )
        if (
            preferred is not None
            and preferred_ok
            and "youtube_shorts" in (preferred.get("accepted_platforms") or [])
        ):
            try:
                youtube_for_clip = platform_map.bind_clip_to_campaign(preferred, "youtube_shorts")
            except platform_map.CampaignPlatformError:
                youtube_for_clip = youtube
        else:
            youtube_for_clip = youtube
        instagram_campaigns = [
            platform_map.bind_clip_to_campaign(campaign, "instagram_reels")
            for campaign in platform_map.campaigns_for_platform(cfg, "instagram_reels")
        ]
        instagram_campaign = instagram_campaigns[index % len(instagram_campaigns)] if instagram_campaigns else None
        tiktok_campaigns = [
            platform_map.bind_clip_to_campaign(campaign, "tiktok")
            for campaign in platform_map.campaigns_for_platform(cfg, "tiktok")
        ]
        tiktok_campaign = tiktok_campaigns[index % len(tiktok_campaigns)] if tiktok_campaigns else None
        hook = platform_map.compose_post_hook(
            str(clip.get("hook_text") or ""),
            str(clip.get("description") or ""),
        )
        youtube_caption = platform_map.build_platform_caption(
            hook,
            youtube_for_clip,
            "youtube_shorts",
            links_blocked=False,
        )
        x_caption = platform_map.build_platform_caption(
            hook,
            x_campaign,
            "x",
            links_blocked=links_blocked,
        )
        duration_ok, duration_reason = platform_map.duration_fits(youtube_for_clip, clip.get("duration"))
        x_seconds = clip.get("x_duration_sec", clip.get("duration"))
        x_duration_ok, x_duration_reason = platform_map.duration_fits(x_campaign, x_seconds)
        clip["campaign_id"] = youtube_for_clip.get("id")
        clip["whop_id"] = youtube_for_clip.get("product_id") or youtube_for_clip.get("whop_id")
        clip["youtube_campaign"] = youtube_for_clip.get("id")
        clip["youtube_caption"] = youtube_caption
        clip["youtube_submission_url"] = youtube_for_clip.get("submission_url")
        clip["caption_youtube"] = youtube_caption
        clip["youtube_duration_ok"] = duration_ok
        clip["youtube_duration_reason"] = duration_reason
        clip["x_campaign"] = x_campaign.get("id")
        clip["x_caption"] = x_caption
        clip["x_submission_url"] = x_campaign.get("submission_url")
        clip["caption_x"] = x_caption
        clip["x_duration_ok"] = x_duration_ok
        clip["x_duration_reason"] = x_duration_reason
        clip["overlay"] = {
            "mode": "dual_compatible",
            "youtube_shorts": platform_map.platform_overlay_spec(
                "youtube_shorts", youtube_for_clip.get("id")
            ),
            "x": platform_map.platform_overlay_spec("x", x_campaign.get("id")),
            "instagram_reels": platform_map.platform_overlay_spec(
                "instagram_reels",
                instagram_campaign.get("id") if instagram_campaign is not None else None,
            ),
            "tiktok": platform_map.platform_overlay_spec(
                "tiktok",
                tiktok_campaign.get("id") if tiktok_campaign is not None else None,
            ),
        }
        if len(
            {
                clip["overlay"][side]["logo_text"]
                for side in ("youtube_shorts", "x", "instagram_reels", "tiktok")
            }
        ) > 1:
            clip["overlay"]["mode"] = "platform_specific_variants"
        clip["x_duration_sec"] = x_seconds
        if not clip.get("x_mp4_url"):
            clip["x_mp4_url"] = None
        clip["x_render"] = clip["overlay"]["mode"]
        clip["logo_used"] = ""
        clip["on_screen_cta"] = platform_map.platform_watermark(
            "youtube_shorts", youtube_for_clip.get("id")
        )
        if instagram_campaign is not None:
            instagram_caption = platform_map.build_platform_caption(
                hook,
                instagram_campaign,
                "instagram_reels",
                links_blocked=False,
            )
            clip["instagram_campaign"] = instagram_campaign.get("id")
            clip["instagram_caption"] = instagram_caption
            clip["instagram_submission_url"] = instagram_campaign.get("submission_url")
            clip["instagram_handle"] = "@benreelr"
            clip["instagram_rate_per_1k_views_usd"] = platform_map._rate(instagram_campaign)
        if tiktok_campaign is not None:
            tiktok_caption = platform_map.build_platform_caption(
                hook,
                tiktok_campaign,
                "tiktok",
                links_blocked=False,
            )
            clip["tiktok_campaign"] = tiktok_campaign.get("id")
            clip["tiktok_caption"] = tiktok_caption
            clip["tiktok_submission_url"] = tiktok_campaign.get("submission_url")
            clip["tiktok_handle"] = "@bi67325"
            clip["tiktok_rate_per_1k_views_usd"] = platform_map._rate(tiktok_campaign)
        identity_errors = platform_map.verify_slot_campaign_identity(clip, cfg)
        if identity_errors:
            raise platform_map.CampaignPlatformError("; ".join(identity_errors))


def build_whop_ready_feed() -> dict:
    """Build whop_ready_feed.json combining Pool Guard status + ready HD clips."""
    cfg = load_json(CAMPAIGNS_PATH, {})
    rules = cfg.get("pool_guard_rules") or {}
    healthy = select_healthy_campaigns()

    ready_clips = [
        {
            "id": "flp-speedcube-lockin",
            "youtube_campaign": "dishpilot-clipping",
            "youtube_category": "indian_actor_reels",
            "title": "Diljit walks out and the whole stadium loses it 🔥🎤 #Dishpilot #Shorts",
            "hook_text": "WAIT FOR THE DROP",
            "description": "Diljit Dosanjh hits the stage and the stadium comes apart.",
            "lane": "indian_actor_reels",
            "duration": 12.5,
            "x_duration_sec": 30.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/yt-flp-1.mp4",
            "x_mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/x-clip-1.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-language-gauntlet",
            "youtube_campaign": "dishpilot-clipping",
            "youtube_category": "indian_actor_reels",
            "title": "When this beat drops nobody stays in their seat 🔥🎶 #Dishpilot #Shorts",
            "hook_text": "WHEN THE BEAT DROPS",
            "description": "Yo Yo Honey Singh locks the room in the second the beat hits.",
            "lane": "indian_actor_reels",
            "duration": 12.5,
            "x_duration_sec": 30.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/yt-flp-2.mp4",
            "x_mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/x-clip-2.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-concert-vibes",
            "youtube_campaign": "mr-pa-clipping",
            "youtube_category": "funny",
            "title": "He solved the cube before his opponent even blinked 🤯🧩 #shorts",
            "hook_text": "WATCH HIS HANDS",
            "description": "One look at his hands and the round is already over.",
            "lane": "funny",
            "duration": 12.5,
            "x_duration_sec": 30.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/yt-flp-3.mp4",
            "x_mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/x-clip-3.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-anime-sakuga",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "Show me a better anime fight scene than this 4K sakuga masterpiece 🔥⚔️ #shorts #anime",
            "hook_text": "SHOW ME A BETTER ANIME FIGHT SCENE",
            "caption_x": (
                "Show me a fight scene better than this in the history of anime 🔥⚔️ The animation budget went completely off the charts!\n\n"
                "Special deals: / Approval in 10 minutes Funding within 24 hours / Up to $5,000 / Just visit Fastloanpilot.com\n\n"
                "#anime #sakuga #viral #shorts"
            ),
            "caption_youtube": (
                "Show me a fight scene better than this in the history of anime 🔥⚔️ The animation budget went completely off the charts!\n\n"
                "Special deals:\n"
                "Approval in 10 minutes Funding within 24 hours\n"
                "Up to $5,000\n"
                "Just visit Fastloanpilot.com\n\n"
                "#shorts #anime #sakuga #viral #animation"
            ),
            "lane": "anime",
            "duration": 54.1,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-anime-sakuga.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-diljit-worldtour",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "When Diljit Dosanjh walks out and the entire stadium loses its mind 🔥🎤 #shorts #concert",
            "hook_text": "WAIT FOR THE STADIUM DROP AT 0.14",
            "caption_x": (
                "Wait for the crowd roar at 0:14 — Diljit Dosanjh World Tour energy is unmatched 🔥🎤🏟️\n\n"
                "Interest Rate just 4.95% / Up to $5,000 / Any credit score is accepted / Monthly Payment $80.54 / Not affect your credit history / Visit Fastloanpilot.com\n\n"
                "#diljitdosanjh #concert #music #viral #shorts"
            ),
            "caption_youtube": (
                "Wait for the crowd roar at 0:14 — Diljit Dosanjh World Tour energy is unmatched 🔥🎤🏟️\n\n"
                "Interest Rate just 4.95%\n"
                "Up to $5,000\n"
                "Any credit score is accepted\n"
                "Monthly Payment $80.54\n"
                "Not affect your credit history\n"
                "Visit Fastloanpilot.com\n\n"
                "#shorts #diljitdosanjh #concert #music #viral"
            ),
            "lane": "concert",
            "duration": 53.1,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-diljit-worldtour.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-khaby-lifehack",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "Khaby Lame tested the unbreakable glasses with a real hammer 😭👓🔨 #shorts #funny",
            "hook_text": "HE TESTED THE UNBREAKABLE GLASSES",
            "caption_x": (
                "Bro really brought out a hammer to test the unbreakable glasses 😭👓🔨 His facial expression says it all!\n\n"
                "Need money today? / Apply for a loan at Fastloanpilot.com\n\n"
                "#khabylame #funny #comedy #viral #shorts"
            ),
            "caption_youtube": (
                "Bro really brought out a hammer to test the unbreakable glasses 😭👓🔨 His facial expression says it all!\n\n"
                "Need money today? / Apply for a loan at Fastloanpilot.com\n\n"
                "#shorts #khabylame #funny #comedy #viral"
            ),
            "lane": "funny",
            "duration": 37.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-khaby-lifehack.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-honeysingh-vibe",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "When Yo Yo Honey Singh drops this classic beat live on stage 🔥🎶 #shorts #music",
            "hook_text": "WHEN THIS BEAT DROPS EVERYONE LOCKS IN",
            "caption_x": (
                "Pure nostalgia when Yo Yo Honey Singh drops this track — nobody can stay still 🔥🎶🕺\n\n"
                "If you need supercar / money / house / Try Fastloanpilot.com\n\n"
                "#yoyohoneysingh #music #celebrity #viral #shorts"
            ),
            "caption_youtube": (
                "Pure nostalgia when Yo Yo Honey Singh drops this track — nobody can stay still 🔥🎶🕺\n\n"
                "If you need supercar / money / house / Try Fastloanpilot.com\n\n"
                "#shorts #yoyohoneysingh #music #celebrity #viral"
            ),
            "lane": "celebrity",
            "duration": 60.1,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-honeysingh-vibe.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-tea-science",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "99% of people ruin their tea by making this 1 mistake 🍵🧠 #shorts #knowledge",
            "hook_text": "99 PERCENT OF PEOPLE RUIN THEIR TEA",
            "caption_x": (
                "99% of people over-steep their tea without realizing what it does to the flavor chemistry 🍵🧠 Watch a 50-year master explain the exact second to stop!\n\n"
                "Need cash to pay rent/bills/groceries / Check this out Fastloanpilot.com\n\n"
                "#knowledge #lifehacks #foodscience #viral #shorts"
            ),
            "caption_youtube": (
                "99% of people over-steep their tea without realizing what it does to the flavor chemistry 🍵🧠 Watch a 50-year master explain the exact second to stop!\n\n"
                "Need cash to pay rent/bills/groceries / Check this out Fastloanpilot.com\n\n"
                "#shorts #knowledge #lifehacks #foodscience #viral"
            ),
            "lane": "knowledge",
            "duration": 50.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-tea-science.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-nyc-doorman",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "This dog refuses to walk past a building he doesn't even live in 🥺🐶 #shorts #wholesome",
            "hook_text": "THIS DOG REFUSES TO WALK PAST HIM",
            "caption_x": (
                "Only in New York does a dog have a best friend doorman at a building he doesn't even live in 🥺🐶❤️ Wait for the tail wag!\n\n"
                "In a tight spot? / Get up to a $5,000 loan today / All credit scores / No banks involved / No paperwork / Check out Fastloanpilot.com\n\n"
                "#wholesome #dogs #nyc #viral #shorts"
            ),
            "caption_youtube": (
                "Only in New York does a dog have a best friend doorman at a building he doesn't even live in 🥺🐶❤️ Wait for the tail wag!\n\n"
                "In a tight spot? / Get up to a $5,000 loan today / All credit scores / No banks involved / No paperwork / Check out Fastloanpilot.com\n\n"
                "#shorts #wholesome #dogs #nyc #viral"
            ),
            "lane": "movie",
            "duration": 34.8,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-nyc-doorman.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-president-speech",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "If bro actually ran for President with this speech he'd win 100% of the vote 😂🎤 #shorts #funny",
            "hook_text": "IF BRO RAN FOR PRESIDENT HED WIN",
            "caption_x": (
                "If bro ran for President with this platform he'd win in a landslide 😂🎤 Wait for his 3rd promise!\n\n"
                "Up to $5,000 / Apply online in minutes / Get funds within 24 hours after approval / Check Fastloanpilot.com if you need a loan asap\n\n"
                "#comedy #funny #viral #shorts"
            ),
            "caption_youtube": (
                "If bro ran for President with this platform he'd win in a landslide 😂🎤 Wait for his 3rd promise!\n\n"
                "Up to $5,000 / Apply online in minutes / Get funds within 24 hours after approval / Check Fastloanpilot.com if you need a loan asap\n\n"
                "#shorts #comedy #funny #viral"
            ),
            "lane": "funny",
            "duration": 66.3,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-president-speech.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
    ]
    _apply_platform_mapping(ready_clips, cfg)
    rejected = _rejected_by_pool_guard(cfg)

    feed_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "whop_user": cfg.get("whop_user", "@bi60"),
        "quality_profile": "1080x1920_lanczos_unsharp_crf16_fast",
        "platform_mapping": (
            "Every X, YouTube, Instagram, and TikTok item uses an active campaign "
            "permitted by PLATFORM_ELIGIBLE_CAMPAIGNS, with its exact badge and hashtag"
        ),
        "platform_eligible_campaigns": {
            campaign_id: list(platforms)
            for campaign_id, platforms in PLATFORM_ELIGIBLE_CAMPAIGNS.items()
        },
        "pool_guard_rules": rules,
        "healthy_campaigns_count": len(healthy),
        "healthy_campaigns": healthy,
        "rejected_by_pool_guard": rejected,
        "skipped_campaigns": cfg.get("skipped_low_pool_or_baited_campaigns") or [],
        "ready_clips_count": len(ready_clips),
        "items": ready_clips,
    }
    root_feed_path = ROOT / "whop_ready_feed.json"
    save_json(FEED_PATH, feed_data)
    save_json(root_feed_path, feed_data)
    log(
        f"wrote {FEED_PATH} and {root_feed_path} with "
        f"{len(healthy)} healthy campaigns and {len(ready_clips)} ready clips"
    )
    return feed_data


def should_publish_daily(env: dict | None = None) -> bool:
    """Commit the daily files only from the scheduled or dispatched main run.

    Unit tests and pull-request jobs leave the files uncommitted. Set
    WHOP_SKIP_PUBLISH=1 to force that even on Actions. Set WHOP_PUBLISH_DAILY=1
    on main (or with no ref) to publish outside Actions.
    """
    current = os.environ if env is None else env
    if str(current.get("WHOP_SKIP_PUBLISH") or "") == "1":
        return False
    on_actions = str(current.get("GITHUB_ACTIONS") or "") == "true"
    forced = str(current.get("WHOP_PUBLISH_DAILY") or "") == "1"
    if not on_actions and not forced:
        return False
    event = str(current.get("GITHUB_EVENT_NAME") or "")
    ref = str(current.get("GITHUB_REF_NAME") or "")
    if on_actions and event not in ("schedule", "workflow_dispatch"):
        return False
    if ref and ref != "main":
        return False
    return True


def _post_buffer(token: str, body: dict) -> tuple[bool, str]:
    payload = json.dumps({"query": _BUFFER_MUTATION, "variables": {"input": body}}).encode("utf-8")
    request = urllib.request.Request(
        BUFFER_GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        exc.read()
        log(f"buffer HTTP {exc.code} for channel {body.get('channelId')}")
        return False, ""
    except Exception as exc:
        log(f"buffer request failed for channel {body.get('channelId')}: {exc}")
        return False, ""
    if status < 200 or status >= 300:
        return False, ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False, ""
    if parsed.get("errors"):
        log(f"buffer rejected channel {body.get('channelId')}")
        return False, ""
    created = (parsed.get("data") or {}).get("createPost")
    if not isinstance(created, dict):
        return False, ""
    return True, str(created.get("id") or "")


def schedule_unscheduled_items(feed: dict, token: str, poster=None) -> int:
    """Best-effort Buffer scheduling. Failures stay pending and do not raise."""
    if not token.strip():
        return 0
    cfg = load_json(CAMPAIGNS_PATH, {})
    scheduled = 0
    for item in feed.get("items") or []:
        if not isinstance(item, dict) or item.get("buffer_status") == "scheduled":
            continue
        if item.get("render_status") in ("failed", "rendered_local"):
            log(f"skipping {item.get('clip_id')}: the stamped file is not hosted yet, so it is not sent to Buffer")
            continue
        platform_map.assert_feed_item_campaign_identity(item, cfg)
        platform_map.assert_feed_item_community_guidelines_safe(item)
        body = platform_map.buffer_create_post_body(item)
        try:
            ok, post_id = poster(body) if poster is not None else _post_buffer(token, body)
        except Exception as exc:
            log(f"buffer schedule failed for {item.get('clip_id')}: {exc}")
            item["buffer_status"] = "pending"
            continue
        if ok:
            item["buffer_status"] = "scheduled"
            if post_id:
                item["buffer_post_id"] = post_id
            scheduled += 1
        else:
            item["buffer_status"] = "pending"
    return scheduled


def publish_daily_files(paths: list[Path], env: dict | None = None) -> bool:
    """Commit the feed and seen log, then push them to main."""
    if not should_publish_daily(env):
        log("daily files left uncommitted for the morning Buffer run")
        return False
    rels = [str(path.relative_to(REPO_ROOT)) for path in paths]
    subprocess.run(["git", "add", "-A", "--", *rels], cwd=REPO_ROOT, check=True)
    quiet = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
    if quiet.returncode == 0:
        log("no daily feed change to commit")
        return False
    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "chore(whop): publish daily 40 clip feed"],
        cwd=REPO_ROOT,
        check=True,
    )
    for attempt in range(1, 5):
        pull = subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO_ROOT)
        push = (
            subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=REPO_ROOT)
            if pull.returncode == 0
            else None
        )
        if pull.returncode == 0 and push is not None and push.returncode == 0:
            log("pushed daily feed and seen log to main")
            return True
        log(f"push to main failed, retrying ({attempt})")
        time.sleep(5)
    raise RuntimeError("could not push the daily feed to main")


def _split_urls(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\s,]+", text or "") if part.strip().startswith("http")]


def load_queue_category() -> str:
    data = load_json(URLS_QUEUE_PATH, {})
    return str(data.get("category") or "glamorous_girls")


def load_queue_urls() -> tuple[list[str], str, str]:
    data = load_json(URLS_QUEUE_PATH, {"urls": [], "campaign": "dishpilot", "platforms": "all"})
    urls = [str(url).strip() for url in data.get("urls") or [] if str(url).strip()]
    return urls, str(data.get("campaign") or "dishpilot"), str(data.get("platforms") or "all")


def merge_ingested_items(existing: dict, feed: dict) -> dict:
    cfg = load_json(CAMPAIGNS_PATH, {})
    for item in feed.get("items") or []:
        if not isinstance(item, dict):
            raise platform_map.CampaignPlatformError("daily feed contains a non-object item")
        platform_map.assert_feed_item_campaign_identity(item, cfg)
        platform_map.assert_feed_item_community_guidelines_safe(item)
    if existing.get("date") != feed.get("date"):
        return feed
    have = {item.get("clip_id") for item in feed.get("items") or []}
    extras = [
        item
        for item in existing.get("items") or []
        if isinstance(item, dict) and item.get("source") == "ingest" and item.get("clip_id") not in have
    ]
    for item in extras:
        platform_map.assert_feed_item_campaign_identity(item, cfg)
        platform_map.assert_feed_item_community_guidelines_safe(item)
    if not extras:
        return feed
    merged = dict(feed)
    merged["items"] = list(feed.get("items") or []) + extras
    merged["count"] = len(merged["items"])
    return merged


def append_items_to_daily_feed(items: list[dict], today) -> dict:
    cfg = load_json(CAMPAIGNS_PATH, {})
    feed = load_json(DAILY_FEED_PATH, {})
    if feed.get("date") != today.isoformat():
        feed = {
            "generated_at": today.isoformat() + "T00:15:00Z",
            "date": today.isoformat(),
            "count": 0,
            "items": [],
        }
    for item in feed.get("items") or []:
        if not isinstance(item, dict):
            raise platform_map.CampaignPlatformError("daily feed contains a non-object item")
        platform_map.assert_feed_item_campaign_identity(item, cfg)
        platform_map.assert_feed_item_community_guidelines_safe(item)
    for item in items:
        platform_map.assert_feed_item_campaign_identity(item, cfg)
        platform_map.assert_feed_item_community_guidelines_safe(item)
    have = {item.get("clip_id") for item in feed.get("items") or []}
    for item in items:
        if item.get("clip_id") not in have:
            feed.setdefault("items", []).append(item)
            have.add(item.get("clip_id"))
    feed["count"] = len(feed["items"])
    feed["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_json(DAILY_FEED_PATH, feed)
    seen = load_json(DAILY_SEEN_PATH, {"ids": [], "uses": {}})
    ids = [str(clip_id) for clip_id in seen.get("ids") or []]
    uses = {str(key): str(value) for key, value in (seen.get("uses") or {}).items()}
    day_text = today.isoformat()
    for item in items:
        clip_id = str(item.get("clip_id") or "")
        if not clip_id:
            continue
        if clip_id not in uses:
            ids.append(clip_id)
        uses[clip_id] = day_text
    save_json(DAILY_SEEN_PATH, {"ids": ids, "uses": uses})
    return feed


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _download_mp4(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        dest.write_bytes(response.read())


def probe_source_metadata(path: Path) -> dict:
    """Read container/stream tags used by the four-platform safety gate."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags:stream_tags",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _gh(args: list[str], runner=None, check: bool = True):
    run = runner or subprocess.run
    return run(["gh", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check)


def daily_release_url(run_date, filename: str, env: dict | None = None) -> str:
    repo = (os.environ if env is None else env).get("GITHUB_REPOSITORY") or DEFAULT_REPO_SLUG
    tag = f"{DAILY_RELEASE_PREFIX}{run_date.isoformat()}"
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def upload_daily_renders(pairs: list[tuple[dict, Path]], run_date, runner=None, env: dict | None = None) -> int:
    """Host the rendered clips as assets on a `daily-YYYY-MM-DD` GitHub release.

    Rendered video stays out of git so 40 clips a day do not bloat the repository.
    """
    if not pairs:
        return 0
    tag = f"{DAILY_RELEASE_PREFIX}{run_date.isoformat()}"
    exists = _gh(["release", "view", tag], runner, check=False)
    if exists.returncode != 0:
        _gh(
            [
                "release",
                "create",
                tag,
                "--title",
                f"Daily clips {run_date.isoformat()}",
                "--notes",
                "Rendered daily clips for the four Buffer channels.",
            ],
            runner,
        )
    uploaded = 0
    for index in range(0, len(pairs), 10):
        chunk = pairs[index : index + 10]
        try:
            _gh(["release", "upload", tag, *[str(path) for _item, path in chunk], "--clobber"], runner)
        except subprocess.CalledProcessError:
            log(f"release upload failed for {len(chunk)} clips; they stay unscheduled")
            continue
        for item, path in chunk:
            item["video_url"] = daily_release_url(run_date, path.name, env)
            item["render_status"] = "rendered"
            uploaded += 1
    return uploaded


def prune_daily_releases(today, keep_days: int = DAILY_RELEASE_KEEP_DAYS, runner=None) -> list[str]:
    """Delete `daily-*` releases older than `keep_days`."""
    listing = _gh(["release", "list", "--limit", "60", "--json", "tagName"], runner, check=False)
    if listing.returncode != 0:
        return []
    try:
        tags = [str(row.get("tagName")) for row in json.loads(listing.stdout or "[]")]
    except json.JSONDecodeError:
        return []
    removed: list[str] = []
    for tag in tags:
        if not tag.startswith(DAILY_RELEASE_PREFIX):
            continue
        try:
            day = datetime.strptime(tag[len(DAILY_RELEASE_PREFIX) :], "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - day).days >= keep_days:
            _gh(["release", "delete", tag, "--cleanup-tag", "--yes"], runner, check=False)
            removed.append(tag)
    return removed


def render_daily_items(
    feed: dict,
    run_date,
    *,
    download_file=None,
    run_ffmpeg=None,
    fallback_download=None,
) -> list[tuple[dict, Path]]:
    """Re-encode each daily clip in 1080x1920 CRF 16 with one campaign name top-left.

    The source is fetched directly, falling back to yt-dlp. Clips whose mark is already
    burned in (`pre_stamped`) are left alone so they never carry two names. Any clip that
    cannot be rendered is flagged `render_status: failed`. Rendered clips are
    `rendered_local` until `upload_daily_renders` hosts them.
    """
    download = download_file or _download_mp4
    fallback = fallback_download or download_hd_source
    encode = run_ffmpeg or (lambda command: subprocess.run(command, check=True, timeout=600))
    font = find_font()
    day_dir = DAILY_RENDER_DIR / run_date.isoformat()
    rendered: list[tuple[dict, Path]] = []
    for item in feed.get("items") or []:
        if not isinstance(item, dict) or item.get("source") == "ingest":
            continue
        if item.get("pre_stamped") or item.get("render_status") == "rendered":
            continue
        source = str(item.get("source_video_url") or item["video_url"])
        clip_id = str(item["clip_id"])
        raw_path = WORK_DIR / "daily" / f"{clip_id}.mp4"
        dest = day_dir / f"{item['platform']}-{int(item['slot']):02d}-{clip_id}.mp4"
        try:
            if not raw_path.exists():
                try:
                    download(source, raw_path)
                except Exception:
                    fallback(source, raw_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            command = platform_map.ingest_ffmpeg_command(
                raw_path, dest, str(item["platform"]), str(item["watermark_text"]), font
            )
            encode(command)
        except Exception as exc:
            log(f"render failed for {clip_id} on {item['platform']}: {type(exc).__name__}")
            item["render_status"] = "failed"
            continue
        item["source_video_url"] = source
        item["render_status"] = "rendered_local"
        rendered.append((item, dest))
    return rendered


def write_daily_schedules(feed: dict, cfg: dict | None = None) -> list[Path]:
    """Write the 40-a-day schedule (10 slots per platform) to every ready-file location."""
    schedule = platform_map.build_daily_40_schedule(feed, cfg)
    paths = [DAILY_40_SCHEDULE_PATH, DAILY_30_SCHEDULE_PATH, DAILY_40_FEED_PATH]
    for path in paths:
        save_json(path, schedule)
    log(f"wrote {', '.join(str(path) for path in paths)} (total_posts={schedule['total_posts']})")
    return paths


def ingest_urls_command(
    urls: list[str],
    campaign: str,
    platforms: str,
    *,
    encode: bool = True,
    fetch_json=None,
    download_file=None,
    category: str = "",
) -> list[dict]:
    """Download each X/TikTok URL, stamp one campaign, and queue it on Buffer channels.

    Sources are fetched at the highest resolution available and encoded with
    lanczos scaling, a light sharpen, and CRF 16. `category` tags each post
    (default `glamorous_girls`) so priority categories sort to the top.
    """
    queue_urls, queue_campaign, queue_platforms = load_queue_urls()
    chosen_urls = list(urls)
    if not chosen_urls:
        chosen_urls = list(queue_urls)
        campaign = campaign or queue_campaign
        platforms = platforms or queue_platforms
        category = category or load_queue_category()
    category = category or "glamorous_girls"
    if not chosen_urls:
        log("no X or TikTok URLs to ingest")
        return []
    cfg = load_json(CAMPAIGNS_PATH, {})
    today = datetime.now(timezone.utc).date()
    fetch = fetch_json or _fetch_json
    download = download_file or _download_mp4
    font = find_font()
    created: list[dict] = []
    rendered: list[Path] = []
    for index, source_url in enumerate(chosen_urls):
        parsed = platform_map.parse_source_url(source_url)
        source_metadata: dict = {}
        clean_url = platform_map.resolve_clean_mp4_url(source_url, fetch, source_metadata)
        raw_path = ROOT / "work" / "ingest" / f"{parsed['id']}.mp4"
        if clean_url:
            download(clean_url, raw_path)
        else:
            download_hd_source(source_url, raw_path)
        source_metadata["container"] = probe_source_metadata(raw_path)
        platform_map.assert_community_guidelines_safe(
            title="Watch this",
            hook="Watch this",
            tags=(category,),
            source_metadata=source_metadata,
            context=f"downloaded source {parsed['id']}",
        )
        hosted_by_platform: dict[str, str] = {}
        campaign_id = platform_map.campaign_id_from_alias(campaign)
        selected = platform_map.platforms_for_campaign(
            cfg, campaign_id, platform_map.parse_platform_list(platforms)
        )
        watermark = platform_map.platform_watermark(selected[0], campaign_id)
        for platform in selected:
            filename = f"ingest-{parsed['id']}-{platform}.mp4"
            dest = INGEST_MEDIA_DIR / filename
            if encode:
                command = platform_map.ingest_ffmpeg_command(raw_path, dest, platform, watermark, font)
                subprocess.run(command, check=True, timeout=600)
                rendered.append(dest)
            hosted_by_platform[platform] = platform_map.RAW_MEDIA_BASE + dest.relative_to(REPO_ROOT).as_posix()
        # One shared pages URL per platform file. Build items one platform at a time
        # so each item points at its own encoded file.
        for platform, video_url in hosted_by_platform.items():
            created.extend(
                platform_map.build_ingest_items(
                    cfg,
                    source_url=source_url,
                    source_id=str(parsed["id"]),
                    video_url=video_url,
                    campaign_alias=campaign,
                    platforms=platform,
                    hook="Watch this",
                    today=today,
                    slot=(index % platform_map.DAILY_30_PER_PLATFORM) + 1,
                    category=category,
                    source_metadata=source_metadata,
                )
            )
    if not created:
        return []
    append_items_to_daily_feed(created, today)
    processed_queue = [url for url in queue_urls if url in chosen_urls]
    if processed_queue:
        queued = load_json(URLS_QUEUE_PATH, {})
        queued["urls"] = [url for url in queue_urls if url not in chosen_urls]
        queued.setdefault("campaign", "dishpilot")
        queued.setdefault("platforms", "all")
        queued.setdefault("category", category)
        save_json(URLS_QUEUE_PATH, queued)
    publish_paths = [DAILY_FEED_PATH, DAILY_SEEN_PATH, URLS_QUEUE_PATH, *rendered]
    try:
        publish_paths.extend(write_daily_schedules(load_json(DAILY_FEED_PATH, {}), cfg))
    except platform_map.CampaignPlatformError as exc:
        log(f"daily 40 schedule not rewritten yet: {exc}")
    existing_paths = [path for path in publish_paths if path.exists()]
    publish_daily_files(existing_paths)
    token = os.environ.get("BUFFER_API_TOKEN", "")
    if token.strip():
        feed = load_json(DAILY_FEED_PATH, {})
        schedule_unscheduled_items(feed, token)
        save_json(DAILY_FEED_PATH, feed)
        publish_daily_files([DAILY_FEED_PATH])
    else:
        log("BUFFER_API_TOKEN unset; ingested slots stay pending")
    log(f"ingested {len(created)} platform posts from {len(chosen_urls)} links")
    return created


def _render_enabled(env: dict | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get("WHOP_SKIP_RENDER") != "1" and shutil.which("ffmpeg") is not None


def build_daily_30_command() -> dict:
    """Select today's 40 clips (10 per platform), stamp them, host them, then schedule Buffer.

    Stamped files are uploaded to a daily GitHub release before Buffer is called so the
    URLs resolve.
    """
    cfg = load_json(CAMPAIGNS_PATH, {})
    today = datetime.now(timezone.utc).date()
    existing = load_json(DAILY_FEED_PATH, {})
    seen = load_json(DAILY_SEEN_PATH, {"ids": [], "uses": {}})
    if platform_map.daily_feed_is_current(existing, today):
        feed = existing
        log(f"keeping today's {feed.get('count')} clips in {DAILY_FEED_PATH}")
    else:
        feed, seen = platform_map.build_daily_30(cfg, seen, today=today)
        feed = merge_ingested_items(existing, feed)
        save_json(DAILY_FEED_PATH, feed)
        save_json(DAILY_SEEN_PATH, seen)
        log(f"wrote {DAILY_FEED_PATH} ({feed['count']} clips) and {DAILY_SEEN_PATH}")
    hosting = should_publish_daily() and shutil.which("gh") is not None
    if hosting:
        removed = prune_daily_releases(today)
        if removed:
            log(f"removed old daily releases: {', '.join(removed)}")
    if _render_enabled():
        rendered = render_daily_items(feed, today)
        log(f"rendered {len(rendered)} clips at CRF {platform_map.INGEST_CRF} with one campaign mark")
        if hosting:
            hosted = upload_daily_renders(rendered, today)
            log(f"hosted {hosted} of {len(rendered)} rendered clips on the daily release")
        else:
            log("not publishing from here; rendered clips stay local and are not scheduled")
    else:
        log("ffmpeg unavailable or WHOP_SKIP_RENDER=1; leaving source URLs unrendered")
    save_json(DAILY_FEED_PATH, feed)
    schedule_paths = write_daily_schedules(feed, cfg)
    publish_daily_files([DAILY_FEED_PATH, DAILY_SEEN_PATH, *schedule_paths])
    token = os.environ.get("BUFFER_API_TOKEN", "")
    if token.strip():
        scheduled = schedule_unscheduled_items(feed, token)
        save_json(DAILY_FEED_PATH, feed)
        write_daily_schedules(feed, cfg)
        log(f"buffer scheduled {scheduled} pending slots")
        publish_daily_files([DAILY_FEED_PATH, *schedule_paths])
    else:
        log("BUFFER_API_TOKEN unset; leaving slots pending for the morning Buffer run")
    return feed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Whop Ultra-Crisp Auto-Clipper & Pool Guard")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-pools", help="Run Pool/Budget Guard and print active vs skipped Whop campaigns")
    sub.add_parser("build-feed", help="Generate whop/feed/whop_ready_feed.json with Pool Guard checks")
    for daily_name in ("build-daily-40", "build-daily-30"):
        sub.add_parser(
            daily_name,
            help=(
                "Select 10 unseen clips for each of the 4 Buffer channels (40 a day) and write "
                "whop/feed/daily_30_ready.json, whop/feed/daily_40_ready.json, whop/daily_30_ready.json, "
                "and whop/daily_40_ready.json"
            ),
        )
    ingest_p = sub.add_parser(
        "ingest-urls",
        help="Download X or TikTok links, stamp one campaign watermark, and queue the posts",
    )
    ingest_p.add_argument("--urls", default="", help="Comma or space separated X or TikTok URLs")
    ingest_p.add_argument("--campaign", default="", help="dishpilot, rascals, or mrpa")
    ingest_p.add_argument("--platforms", default="", help="all or a comma-separated platform list")
    ingest_p.add_argument(
        "--category",
        default="",
        help="Category tag: " + ", ".join(platform_map.INGEST_CATEGORIES),
    )
    ingest_p.add_argument(
        "--from-queue",
        action="store_true",
        help="Also read whop/urls_queue.json",
    )

    clip_p = sub.add_parser("clip", help="Download and encode a single source URL in Ultra-Crisp 9:16 HD")
    clip_p.add_argument("--url", required=True, help="Source video URL or local path")
    clip_p.add_argument("--out", required=True, help="Output MP4 path")
    clip_p.add_argument("--hook", default="", help="Top scroll-stopping hook banner text")
    clip_p.add_argument("--watermark", default="", help="Bottom centered CTA domain text")
    clip_p.add_argument("--logo", default="", help="Optional PNG logo path to overlay")
    clip_p.add_argument("--logo-text", default="", help="Campaign banner text (logo_text)")
    clip_p.add_argument("--logo-subtext", default="", help="Campaign banner subtext (logo_subtext)")

    args = parser.parse_args(argv)
    if args.command == "check-pools":
        healthy = select_healthy_campaigns()
        print(json.dumps({"healthy_count": len(healthy), "healthy_campaigns": healthy}, indent=2))
        return 0
    if args.command == "build-feed":
        build_whop_ready_feed()
        return 0
    if args.command in ("build-daily-30", "build-daily-40"):
        build_daily_30_command()
        return 0
    if args.command == "ingest-urls":
        urls = _split_urls(args.urls)
        urls.extend(_split_urls(os.environ.get("SOURCE_URLS", "")))
        campaign = args.campaign or os.environ.get("INGEST_CAMPAIGN", "") or "dishpilot"
        platforms = args.platforms or os.environ.get("INGEST_PLATFORMS", "") or "all"
        category = args.category or os.environ.get("INGEST_CATEGORY", "")
        if args.from_queue or not urls:
            queued, queue_campaign, queue_platforms = load_queue_urls()
            for url in queued:
                if url not in urls:
                    urls.append(url)
            if not args.campaign and not os.environ.get("INGEST_CAMPAIGN"):
                campaign = queue_campaign or campaign
            if not args.platforms and not os.environ.get("INGEST_PLATFORMS"):
                platforms = queue_platforms or platforms
            if not category:
                category = load_queue_category()
        ingest_urls_command(urls, campaign, platforms, category=category)
        return 0
    if args.command == "clip":
        src_path = Path(args.url)
        tmp_src = None
        if not src_path.exists():
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            tmp_src = MEDIA_DIR / "temp_raw.mp4"
            download_hd_source(args.url, tmp_src)
            src_path = tmp_src
        logo_p = Path(args.logo) if args.logo else None
        encode_ultra_crisp_whop(
            src_path,
            Path(args.out),
            hook_text=args.hook,
            watermark_text=args.watermark,
            logo_path=logo_p,
            logo_text=args.logo_text,
            logo_subtext=args.logo_subtext,
        )
        if tmp_src and tmp_src.exists():
            tmp_src.unlink(missing_ok=True)
        log(f"encoded ultra-crisp MP4 -> {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
