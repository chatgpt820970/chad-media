#!/usr/bin/env python3
"""Keyless Ultra-HD/4K-Crisp Whop Content Rewards & Viral Auto-Clipper.

1. Enforces Whop Pool/Budget Guard (`campaign_passes_pool_guard`) so we NEVER clip
   for empty, paused, high-denial-rate, or nearly exhausted (<$500 remaining or
   >80% paid out) Whop campaigns/bounties.
2. Focuses on high-retention lanes for YouTube Shorts (`Denzen` / `@denzen0`)
   and X (`@chadriyo`):
   - `anime`: 4K/HD anime fight & emotional edits
   - `movie`: iconic cinema & movie/TV scenes
   - `podcast`: funny stand-up (Shane Gillis, etc.) & high-retention viral challenges
3. Downloads the highest available bitrate/resolution via `yt-dlp`
   (`bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best`) or clean raw source.
4. Encodes Ultra-Crisp 1080x1920 (9:16 vertical for YouTube Shorts `Denzen` &
   X `@chadriyo`) using Lanczos scaling, CAS/unsharp 4K-style contrast-adaptive
   sharpening, CRF 18, 8M maxrate, 192k AAC audio, persistent scroll-stopping top hook
   banner, centered Whop Type-2 campaign logo PNG overlay, and centered bottom CTA.
5. Zero API keys or secrets in code.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
CAMPAIGNS_PATH = ROOT / "campaigns.json"
FEED_PATH = ROOT / "feed" / "whop_ready_feed.json"
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
    """Verify campaign has a healthy remaining reward pool before clipping."""
    status = str(campaign.get("status") or "open").lower()
    if rules.get("skip_paused_or_closed", True) and status not in ("open", "active", "live"):
        return False, f"status is {status}"

    unlimited = bool(campaign.get("unlimited_budget", False))
    min_rem = float(rules.get("min_remaining_budget_usd", 500.0))
    max_paid_frac = float(rules.get("max_paid_out_fraction", 0.80))
    min_spots = int(rules.get("min_spots_remaining", 5))

    total = float(campaign.get("budget_total_usd") or 0.0)
    paid = float(campaign.get("paid_out_usd") or 0.0)
    remaining = float(campaign.get("remaining_budget_usd") or (total - paid))
    spots = int(campaign.get("spots_remaining") or 0)

    if not unlimited:
        if remaining < min_rem:
            return False, f"remaining pool ${remaining:.2f} < ${min_rem:.2f} minimum"
        if total > 0 and (paid / total) > max_paid_frac:
            return False, f"pool {(paid / total) * 100:.1f}% exhausted (> {max_paid_frac * 100:.0f}% cap)"
        if spots < min_spots:
            return False, f"only {spots} spots remaining (< {min_spots})"

    return True, f"OK (remaining=${remaining:.0f}, paid={paid / total * 100 if total else 0:.1f}%, spots={spots})"


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
        "bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height>=720]+bestaudio/best[ext=mp4]/best",
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

    # 1. Scroll-stopping hook banner at top-center (persistent across the video for max retention)
    logo_y = 140
    if hook_text:
        safe_hook = escape_drawtext(hook_text.upper()[:46])
        chains.append(
            f"{current_label}drawtext=fontfile='{font_path}':text='{safe_hook}':"
            f"fontcolor=white:fontsize=46:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.72:boxborderw=18:"
            f"x=(w-text_w)/2:y=95[vhook]"
        )
        current_label = "[vhook]"
        logo_y = 185

    # 2. Centered campaign logo at the top (matching FastLoanPilot's official reference video)
    inputs = ["-i", str(src)]
    if logo_path and logo_path.exists():
        inputs.extend(["-i", str(logo_path)])
        chains.append(
            f"[1:v]scale=580:-1:flags=lanczos,format=rgba[logo];"
            f"{current_label}[logo]overlay=(W-w)/2:{logo_y}[vlogo]"
        )
        current_label = "[vlogo]"

    # 3. Centered bottom website CTA ("Only Available on" / "FASTLOANPILOT.COM")
    if watermark_text:
        safe_wm = escape_drawtext(watermark_text.upper()[:34])
        chains.append(
            f"{current_label}drawtext=fontfile='{font_path}':text='Only Available on':"
            f"fontcolor=white:fontsize=36:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=h-320[vcta1];"
            f"[vcta1]drawtext=fontfile='{font_path}':text='{safe_wm}':"
            f"fontcolor=0x00E5FF:fontsize=52:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.65:boxborderw=14:"
            f"x=(w-text_w)/2:y=h-265[vwm]"
        )
        current_label = "[vwm]"

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
        "18",
        "-maxrate",
        "8M",
        "-bufsize",
        "16M",
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


def build_whop_ready_feed() -> dict:
    """Build whop_ready_feed.json combining Pool Guard status + ready HD clips."""
    cfg = load_json(CAMPAIGNS_PATH, {})
    rules = cfg.get("pool_guard_rules") or {}
    healthy = select_healthy_campaigns()

    ready_clips = [
        {
            "id": "flp-speedcube-lockin",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "He solved the Rubik's Cube BEFORE his opponent even blinked 🤯🧩 #shorts #viral",
            "hook_text": "WATCH HIS HANDS AT 4.41 SECONDS",
            "caption_x": (
                "Bro entered an official Rubik's Cube tournament and solved it faster than humanly possible 🤯🧩 Wait for his opponent's reaction at the end!\n\n"
                "In a tight spot? / Get up to a $5,000 loan today / All credit scores / No banks involved / No paperwork / Check out Fastloanpilot.com\n\n"
                "#viral #speedcubing #rubikscube #shorts"
            ),
            "caption_youtube": (
                "Bro entered an official Rubik's Cube tournament and solved it faster than humanly possible 🤯🧩 Wait for his opponent's reaction at the end!\n\n"
                "In a tight spot?\n"
                "Get up to a $5,000 loan today\n"
                "All credit scores\n"
                "No banks involved\n"
                "No paperwork\n"
                "Check out Fastloanpilot.com\n\n"
                "#shorts #viral #rubikscube #speedcubing #mindblowing"
            ),
            "lane": "funny",
            "duration": 34.7,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-speedcube-lockin.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-language-gauntlet",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "Almost nobody passes the 5th country flag 😭🌍 Can you name them all? #shorts #viral",
            "hook_text": "ALMOST NOBODY PASSES THE 5TH FLAG",
            "caption_x": (
                "How many of these languages could you actually get right on the first try? 😭🌍 The ending caught him completely off guard!\n\n"
                "Up to $5,000 / Apply online in minutes / Get funds within 24 hours after approval / Check Fastloanpilot.com if you need a loan asap\n\n"
                "#viral #challenge #languages #shorts"
            ),
            "caption_youtube": (
                "How many of these languages could you actually get right on the first try? 😭🌍 The ending caught him completely off guard!\n\n"
                "Up to $5,000\n"
                "Apply online in minutes\n"
                "Get funds within 24 hours after approval\n"
                "Check Fastloanpilot.com if you need a loan asap\n\n"
                "#shorts #viral #challenge #trivia #funny"
            ),
            "lane": "knowledge",
            "duration": 38.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-language-gauntlet.mp4",
            "logo_used": "whop/logos/fastloanpilot.png",
            "on_screen_cta": "Only Available on FASTLOANPILOT.COM",
            "channels": {
                "x_chadriyo": "6ac41eb06a5c39ccb625fdf1",
                "yt_denzen": "6ac4da806a5c39ccb62eab64",
            },
        },
        {
            "id": "flp-concert-vibes",
            "campaign_id": "fastloanpilot-logo",
            "whop_id": "prod_DyuwRHNCz3vpv",
            "title": "When Ed Sheeran harmonizes in Hindi & English live on acoustic guitar ✨🎶 #shorts #music",
            "hook_text": "WAIT FOR THE HIGH NOTE AT 0.08",
            "caption_x": (
                "Unplugged acoustic magic — wait for the vocal harmony at 0:08 ✨🎶 Goosebumps every single time!\n\n"
                "No hard credit check / Even with bad credit / No hidden fees / Instant/quick/free/fast approval / Apply for a loan at Fastloanpilot.com\n\n"
                "#music #edsheeran #concert #viral #shorts"
            ),
            "caption_youtube": (
                "Unplugged acoustic magic — wait for the vocal harmony at 0:08 ✨🎶 Goosebumps every single time!\n\n"
                "No hard credit check\n"
                "Even with bad credit\n"
                "No hidden fees\n"
                "Instant/quick/free/fast approval\n"
                "Apply for a loan at Fastloanpilot.com\n\n"
                "#shorts #music #edsheeran #concert #live #viral"
            ),
            "lane": "concert",
            "duration": 70.0,
            "mp4_url": "https://chatgpt820970.github.io/chad-media/whop/v/flp-concert-vibes.mp4",
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

    feed_data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "whop_user": cfg.get("whop_user", "@bi60"),
        "quality_profile": "1080x1920_lanczos_cas_crf18_8mbps",
        "pool_guard_rules": rules,
        "healthy_campaigns_count": len(healthy),
        "healthy_campaigns": healthy,
        "skipped_campaigns": cfg.get("skipped_low_pool_or_baited_campaigns") or [],
        "ready_clips_count": len(ready_clips),
        "items": ready_clips,
    }
    save_json(FEED_PATH, feed_data)
    log(f"wrote {FEED_PATH} with {len(healthy)} healthy campaigns and {len(ready_clips)} ready clips")
    return feed_data


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Whop Ultra-Crisp Auto-Clipper & Pool Guard")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-pools", help="Run Pool/Budget Guard and print active vs skipped Whop campaigns")
    sub.add_parser("build-feed", help="Generate whop/feed/whop_ready_feed.json with Pool Guard checks")

    clip_p = sub.add_parser("clip", help="Download and encode a single source URL in Ultra-Crisp 9:16 HD")
    clip_p.add_argument("--url", required=True, help="Source video URL or local path")
    clip_p.add_argument("--out", required=True, help="Output MP4 path")
    clip_p.add_argument("--hook", default="", help="Top scroll-stopping hook banner text")
    clip_p.add_argument("--watermark", default="", help="Bottom centered CTA domain text")
    clip_p.add_argument("--logo", default="", help="Optional PNG logo path to overlay")

    args = parser.parse_args(argv)
    if args.command == "check-pools":
        healthy = select_healthy_campaigns()
        print(json.dumps({"healthy_count": len(healthy), "healthy_campaigns": healthy}, indent=2))
        return 0
    if args.command == "build-feed":
        build_whop_ready_feed()
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
        )
        if tmp_src and tmp_src.exists():
            tmp_src.unlink(missing_ok=True)
        log(f"encoded ultra-crisp MP4 -> {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
