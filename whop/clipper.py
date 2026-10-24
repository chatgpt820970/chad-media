#!/usr/bin/env python3
"""Platform-specific Whop campaign mapping.

X (@chadriyo) posts to Dishpilot. Instagram Reels (@benreelr) posts to Rascals
and Dishpilot. YouTube Shorts (Denzen) posts to Dishpilot and Mr. Pa. FastLoanPilot
and Nova are closed: Nova's legacy app app_JsryI0fVLVpeYi only returns "ok".
Pool Guard rejects bounties whose denial rate is above the configured cap
(Shane Gillis is 99.8%).

Visual rule: one subtle top-left corner mark. Never two logos, and never a logo
stacked in the middle of the frame. X and YouTube read "dishpilot". Instagram
reads "rascals · dishpilot".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAMPAIGNS_PATH = ROOT / "campaigns.json"
MANIFEST_PATH = ROOT / "manifest.json"
FEED_JSON_PATH = ROOT / "feed.json"
MEDIA_DIR = ROOT / "v"
PAGES_MEDIA_BASE = "https://chatgpt820970.github.io/chad-media/whop/v/"
BOLD_FONT = "/usr/share/fonts/truetype/macos/Inter-Bold.ttf"

SUPPORTED_PLATFORMS = ("x", "youtube_shorts", "instagram_reels")
PLATFORM_ACCOUNTS = {
    "x": "@chadriyo",
    "youtube_shorts": "Denzen (@denzen0)",
    "instagram_reels": "@benreelr",
}
X_CAMPAIGN_IDS = ("dishpilot-clipping",)
YOUTUBE_PRIMARY_ID = "dishpilot-clipping"
MR_PA_ID = "mr-pa-clipping"
RASCALS_ID = "rascals-theme-pages"
DISHPILOT_ID = "dishpilot-clipping"
CLOSED_CAMPAIGN_IDS = ("fast-loan-pilot", "nova-x-clipping")
# Full HD encode. No bitrate cap: CRF 17 with the fast preset.
ENCODE_PRESET = "fast"
ENCODE_CRF = "17"
DAILY_SLOT_COUNT = 10
OPEN_STATUSES = ("open", "active", "live")
INDIAN_CATEGORIES = ("indian_actor_reels", "indian_movie_scenes")
X_VIRAL_CATEGORIES = ("funny", "ai", "knowledge", "movie", "anime", "celebrity", "concert")
# Locked @chadriyo mix. Every category appears at least once across the 10 slots.
X_SLOT_CATEGORIES = (
    "funny",
    "ai",
    "knowledge",
    "movie",
    "anime",
    "celebrity",
    "concert",
    "funny",
    "knowledge",
    "movie",
)
# 8 of 10 Denzen slots are Indian actor / cinema lanes on Dishpilot ($2/1K).
# The other 2 stay global on Mr. Pa ($1.25/1K).
YOUTUBE_SLOT_PLAN = (
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-1.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-2.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-1.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-2.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-1.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-2.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-1.mp4"},
    {"campaign_id": DISHPILOT_ID, "category": "indian_actor_reels", "file": "yt-flp-2.mp4"},
    {"campaign_id": MR_PA_ID, "category": "funny", "file": "yt-flp-3.mp4"},
    {"campaign_id": MR_PA_ID, "category": "movie", "file": "yt-flp-3.mp4"},
)
# Rascals ($3/1K) leads. Dishpilot ($2/1K) fills the alternating Reels slots.
INSTAGRAM_SLOT_PLAN = tuple(
    {"campaign_id": RASCALS_ID if index % 2 == 0 else DISHPILOT_ID}
    for index in range(DAILY_SLOT_COUNT)
)

# Ten posting slots. X campaigns rotate in routing order so each paying
# campaign receives @chadriyo views. YouTube Shorts follows YOUTUBE_SLOT_PLAN.
DAILY_SLOTS = (
    {"slot": 1, "time_ist": "08:00", "hook": "Wait for the stadium drop"},
    {"slot": 2, "time_ist": "09:30", "hook": "Nobody stayed in their seat"},
    {"slot": 3, "time_ist": "11:00", "hook": "The whole crowd knew this line"},
    {"slot": 4, "time_ist": "12:30", "hook": "He walked out and the roof came off"},
    {"slot": 5, "time_ist": "14:00", "hook": "Watch the front row lose it"},
    {"slot": 6, "time_ist": "15:30", "hook": "This beat still shuts the venue down"},
    {"slot": 7, "time_ist": "17:00", "hook": "One note and the stadium erupts"},
    {"slot": 8, "time_ist": "18:30", "hook": "Count the phones in the air"},
    {"slot": 9, "time_ist": "20:00", "hook": "Wait for the ending"},
    {"slot": 10, "time_ist": "21:30", "hook": "Pause on the last second"},
)

_LINK_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
_X_MP4_RE = re.compile(
    r"^https://chatgpt820970\.github\.io/chad-media/whop/v/x-clip-[123]\.mp4$"
)
_YT_MP4_RE = re.compile(
    r"^https://chatgpt820970\.github\.io/chad-media/whop/v/yt-flp-[123]\.mp4$"
)

# YouTube Shorts are 12.5s and Instagram Reels are 15s, both inside the 8–35s
# Dishpilot window. X files are 30s.
YOUTUBE_DURATION_SEC = 12.5
INSTAGRAM_DURATION_SEC = 15.0
X_DURATION_SEC = 30.0
PLATFORM_WATERMARK = {
    "x": "dishpilot",
    "youtube_shorts": "dishpilot",
    "instagram_reels": "rascals · dishpilot",
}
PLATFORM_HASHTAGS = {
    "x": ["#Dishpilot"],
    "youtube_shorts": ["#Dishpilot", "#Shorts"],
    "instagram_reels": ["#Rascals", "#Dishpilot", "#Reels"],
}
YOUTUBE_VARIANTS = (
    {
        "file": "yt-flp-1.mp4",
        "source": "flp-diljit-worldtour.mp4",
        "hook": "WAIT FOR THE DROP",
        "corner_logo": False,
        "category": "indian_actor_reels",
    },
    {
        "file": "yt-flp-2.mp4",
        "source": "flp-honeysingh-vibe.mp4",
        "hook": "WHEN THE BEAT DROPS",
        "corner_logo": False,
        "category": "indian_actor_reels",
    },
    {
        "file": "yt-flp-3.mp4",
        "source": "flp-speedcube-lockin.mp4",
        "hook": "WATCH HIS HANDS",
        "corner_logo": True,
        "category": "funny",
    },
)
X_VARIANTS = (
    {
        "file": "x-clip-1.mp4",
        "source": "flp-speedcube-lockin.mp4",
        "badge": "dishpilot",
        "hook": "WATCH HIS HANDS",
        "campaign_id": DISHPILOT_ID,
    },
    {
        "file": "x-clip-2.mp4",
        "source": "flp-language-gauntlet.mp4",
        "badge": "dishpilot",
        "hook": "NAME EVERY FLAG",
        "campaign_id": DISHPILOT_ID,
    },
    {
        "file": "x-clip-3.mp4",
        "source": "flp-concert-vibes.mp4",
        "badge": "dishpilot",
        "hook": "WAIT FOR THE NOTE",
        "campaign_id": DISHPILOT_ID,
    },
)
LIBRARY_SOURCES = (
    "flp-anime-sakuga.mp4",
    "flp-concert-vibes.mp4",
    "flp-diljit-worldtour.mp4",
    "flp-honeysingh-vibe.mp4",
    "flp-khaby-lifehack.mp4",
    "flp-language-gauntlet.mp4",
    "flp-nyc-doorman.mp4",
    "flp-president-speech.mp4",
    "flp-speedcube-lockin.mp4",
    "flp-tea-science.mp4",
)
# Burned-in FastLoanPilot wordmark (upper) and the second banner / bottom CTA.
# Measured on the 1080x1920 masters. The corner box clears a previous badge so
# a re-render still leaves exactly one logo.
BURNED_IN_LOGO_FILTER = (
    "delogo=x=60:y=70:w=960:h=90:show=0,"
    "delogo=x=200:y=180:w=700:h=180:show=0,"
    "delogo=x=180:y=1540:w=740:h=240:show=0,"
    "delogo=x=800:y=1760:w=260:h=140:show=0"
)
_LOAN_SPAM_RE = re.compile(
    r"fast\s*loan\s*pilot|fastloanpilot|fastloan|"
    r"instant\s+cash|quick\s+loan|need\s+a\s+loan|need\s+cash|"
    r"\$\s*200|\$\s*5,?000|credit\s+score|funding\s+within|apply\s+for\s+a\s+loan",
    re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"#[A-Za-z][A-Za-z0-9_]*")


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
    for key in ("remaining_pool_usd", "pool_remaining_usd", "remaining_budget_usd"):
        if campaign.get(key) is not None:
            return float(campaign[key])
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


def _experience_id(campaign: dict) -> str:
    return str(
        campaign.get("experience_id")
        or campaign.get("whop_experience_id")
        or campaign.get("content_rewards_experience_id")
        or ""
    )


def _is_direct_submission(campaign: dict) -> bool:
    if "hub" in str(campaign.get("type") or ""):
        return False
    if not campaign.get("submission_url"):
        return False
    if not _experience_id(campaign):
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

    Each platform prefers its routed primary: Dishpilot on X and YouTube Shorts,
    Rascals on Instagram Reels. Closed campaigns (FastLoanPilot, Nova) never win.
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
    minimum = rules.get("min_duration_sec", campaign.get("min_duration_sec"))
    maximum = rules.get("max_duration_sec", campaign.get("max_duration_sec"))
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


def _extra_hashtags(caption: str, campaign: dict, limit: int | None = None) -> list[str]:
    existing = caption.lower()
    extras: list[str] = []
    for tag in _rules(campaign).get("hashtags") or []:
        text = str(tag).strip()
        if text and text.lower() not in existing:
            extras.append(text)
    if limit is not None:
        return extras[:limit]
    return extras


def caption_style(campaign: dict) -> str:
    style = str(_rules(campaign).get("caption_style") or "hook_cta_hashtags")
    if style == "hook_and_hashtags":
        return style
    if style == "hook_cta_hashtags":
        return style
    raise CampaignPlatformError(f"Unknown caption_style {style!r} on {campaign.get('id')}")


def contains_loan_spam(caption: str) -> bool:
    return _LOAN_SPAM_RE.search(caption or "") is not None


def _strip_links(caption: str) -> str:
    caption = _LINK_RE.sub("", caption)
    caption = re.sub(r"[ \t]{2,}", " ", caption)
    caption = re.sub(r" *\n", "\n", caption)
    caption = re.sub(r"\n{3,}", "\n\n", caption)
    return caption.strip()


def build_platform_caption(
    hook: str,
    campaign: dict,
    platform: str,
    *,
    links_blocked: bool | None = None,
) -> str:
    """Build the post caption from that campaign's terms.

    Routed Dishpilot, Rascals, and Mr. Pa posts use the platform's required tags:
    X is #Dishpilot, YouTube Shorts is #Dishpilot #Shorts, and Instagram Reels is
    #Rascals #Dishpilot #Reels. Other campaigns keep their own caption style.
    X drops raw links when the channel blocks them, and loan phrasing is removed
    from every X caption.
    """
    rules = _rules(campaign)
    style = caption_style(campaign)
    hook_text = hook.strip()
    platform_tags = PLATFORM_HASHTAGS.get(platform) if campaign.get("id") in {
        DISHPILOT_ID,
        MR_PA_ID,
        RASCALS_ID,
    } else None
    if platform_tags:
        caption = f"{hook_text}\n\n{' '.join(platform_tags)}" if hook_text else " ".join(platform_tags)
        if links_blocked is None:
            links_blocked = platform == "x"
        if links_blocked:
            caption = _strip_links(caption)
        if platform == "x":
            caption = _LOAN_SPAM_RE.sub("", caption)
            caption = re.sub(r"[ \t]{2,}", " ", caption)
            caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
        return caption.strip()
    if style == "hook_and_hashtags":
        limit = int(rules.get("max_hashtags") or 2)
        extras = _extra_hashtags(hook_text, campaign, limit)
        caption = f"{hook_text}\n\n{' '.join(extras)}" if hook_text and extras else (hook_text or " ".join(extras))
    elif style == "hook_cta_hashtags":
        cta = str(rules.get("mandatory_caption") or campaign.get("required_caption") or "").strip()
        parts = [part for part in (hook_text, cta) if part]
        caption = "\n\n".join(parts)
        extras = _extra_hashtags(caption, campaign)
        if extras:
            caption = f"{caption}\n\n{' '.join(extras)}" if caption else " ".join(extras)
    else:
        raise CampaignPlatformError(f"Unhandled caption_style {style!r}")
    if links_blocked is None:
        links_blocked = platform == "x"
    if links_blocked:
        caption = _strip_links(caption)
    if platform == "x":
        caption = _LOAN_SPAM_RE.sub("", caption)
        caption = re.sub(r"[ \t]{2,}", " ", caption)
        caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
    return caption.strip()


def x_links_blocked(campaigns_data: dict) -> bool:
    routing = _routing(campaigns_data, "x")
    if "links_blocked" in routing:
        return bool(routing["links_blocked"])
    channel = (campaigns_data.get("channels") or {}).get("x") or {}
    return bool(channel.get("links_blocked", True))


DISHPILOT_WATERMARK_TEXT = "dishpilot"
RASCALS_WATERMARK_TEXT = "rascals · dishpilot"
# Pixels cut from the bottom of a 1080x1920 master before it is scaled back.
# The baked fastloanpilot wordmark sits in this strip (around y=1818–1872).
BOTTOM_STRIP_CROP_PX = 200


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "")
        .replace("%", "%%%%")
        .replace(",", "\\,")
    )


def is_dishpilot_badge(text: str) -> bool:
    letters = "".join(ch for ch in text.casefold() if ch.isalpha())
    return letters in {"dishpilot", "dishpilotcom"}


def is_subtle_corner_badge(text: str) -> bool:
    letters = "".join(ch for ch in text.casefold() if ch.isalpha())
    return letters in {"dishpilot", "dishpilotcom", "rascalsdishpilot"}


def subtle_corner_drawtext(font_path: str, text: str) -> str:
    """Top-left mark: white at 0.58 alpha, a 1px shadow, and no box."""
    safe = escape_drawtext(text)
    return (
        f"drawtext=fontfile='{font_path}':text='{safe}':"
        "fontsize=22:fontcolor=white@0.58:"
        "shadowcolor=black@0.40:shadowx=1:shadowy=1:"
        "x=w*0.025:y=h*0.03"
    )


def dishpilot_watermark_drawtext(font_path: str) -> str:
    """Small semi-transparent dishpilot mark in the top-left corner. No pill."""
    return subtle_corner_drawtext(font_path, DISHPILOT_WATERMARK_TEXT)


def platform_overlay_spec(platform: str) -> dict:
    """The single corner mark required on that platform."""
    return {
        "logo_overlay_required": True,
        "logo_placement": "corner",
        "logo_count": 1,
        "logo_text": PLATFORM_WATERMARK[platform],
        "logo_subtext": "",
        "logo_path": "",
    }


def banner_overlay_spec(campaign: dict) -> dict:
    """One subtle corner badge when the campaign requires a logo. Never a center stack."""
    rules = _rules(campaign)
    required = bool(rules.get("logo_overlay_required", False))
    if rules.get("logo_count") is not None and int(rules["logo_count"]) < 1:
        required = False
    count = 1 if required else 0
    placement = "corner" if count == 1 else "none"
    return {
        "logo_overlay_required": required,
        "logo_placement": placement,
        "logo_count": count,
        "logo_text": str(rules.get("logo_text") or "") if count == 1 else "",
        "logo_subtext": "",
        "logo_path": str(campaign.get("logo_path") or "") if count == 1 else "",
        "min_duration_sec": rules.get("min_duration_sec"),
        "max_duration_sec": rules.get("max_duration_sec"),
    }


def overlay_plan(youtube_campaign: dict, x_campaign: dict) -> dict:
    """Platform-specific corner badges when the brands differ, one shared badge when they match."""
    youtube_spec = banner_overlay_spec(youtube_campaign)
    x_spec = banner_overlay_spec(x_campaign)
    same_brand = (
        youtube_spec["logo_text"] == x_spec["logo_text"]
        and youtube_spec["logo_count"] == x_spec["logo_count"]
        and youtube_spec["logo_placement"] == x_spec["logo_placement"]
        and youtube_spec["logo_path"] == x_spec["logo_path"]
    )
    mode = "dual_compatible" if same_brand else "platform_specific_variants"
    return {"mode": mode, "youtube_shorts": youtube_spec, "x": x_spec}


def ffmpeg_banner_filter(spec: dict, input_label: str, font_path: str) -> tuple[str, str]:
    """Draw a single subtle corner badge. Returns (filter graph, output label)."""
    if int(spec.get("logo_count") or 0) > 1:
        raise CampaignPlatformError("Refusing to draw more than one logo")
    if str(spec.get("logo_placement") or "none") not in ("corner", "none"):
        raise CampaignPlatformError("Logo placement must be a corner or omitted")
    if int(spec.get("logo_count") or 0) != 1:
        return "", input_label
    raw_logo = str(spec.get("logo_text") or "")
    if is_subtle_corner_badge(raw_logo):
        graph = f"{input_label}{subtle_corner_drawtext(font_path, raw_logo)}[vbadge]"
        return graph, "[vbadge]"
    logo_text = escape_drawtext(raw_logo)
    if not logo_text:
        return "", input_label
    graph = (
        f"{input_label}drawtext=fontfile='{font_path}':text='{logo_text}':"
        "fontcolor=white:fontsize=28:borderw=2:bordercolor=black:"
        "box=1:boxcolor=black@0.45:boxborderw=10:"
        "x=w-text_w-40:y=h-text_h-48[vbadge]"
    )
    return graph, "[vbadge]"


def platform_account(campaigns_data: dict, platform: str) -> dict:
    routing = _routing(campaigns_data, platform)
    channels = campaigns_data.get("channels") or {}
    if platform == "x":
        channel = channels.get("x") or {}
        return {
            "handle": routing.get("handle") or channel.get("handle") or "@chadriyo",
            "username": routing.get("username") or channel.get("username") or "",
        }
    if platform == "instagram_reels":
        channel = channels.get("instagram") or {}
        return {
            "handle": routing.get("handle") or channel.get("handle") or "@benreelr",
            "username": routing.get("username") or channel.get("username") or "@benreelr",
        }
    channel = channels.get("youtube") or {}
    return {
        "handle": routing.get("handle") or channel.get("handle") or "Denzen",
        "username": routing.get("username") or channel.get("username") or "@denzen0",
    }


def _min_indian_fraction(campaigns_data: dict) -> float:
    mix = (campaigns_data.get("content_mix") or {}).get("youtube_shorts") or {}
    if mix.get("min_indian_fraction") is None:
        return 0.80
    return float(mix["min_indian_fraction"])


def build_daily_manifest(campaigns_data: dict, *, generated_at: str | None = None) -> dict:
    """Ten slots. YouTube follows the Indian mix. X rotates campaigns that pay for X."""
    if len(DAILY_SLOTS) != DAILY_SLOT_COUNT or len(YOUTUBE_SLOT_PLAN) != DAILY_SLOT_COUNT:
        raise CampaignPlatformError(f"Expected {DAILY_SLOT_COUNT} daily slots")
    if len(X_SLOT_CATEGORIES) != DAILY_SLOT_COUNT:
        raise CampaignPlatformError(f"Expected {DAILY_SLOT_COUNT} X categories")
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
        plan = YOUTUBE_SLOT_PLAN[index]
        youtube_campaign = _campaign_by_id(campaigns_data, str(plan["campaign_id"]))
        if youtube_campaign is None:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} YouTube campaign {plan['campaign_id']!r} is missing"
            )
        bind_clip_to_campaign(youtube_campaign, "youtube_shorts")
        x_campaign = x_campaigns[index % len(x_campaigns)]
        bind_clip_to_campaign(x_campaign, "x")
        ig_plan = INSTAGRAM_SLOT_PLAN[index]
        instagram_campaign = _campaign_by_id(campaigns_data, str(ig_plan["campaign_id"]))
        if instagram_campaign is None:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} Instagram campaign {ig_plan['campaign_id']!r} is missing"
            )
        bind_clip_to_campaign(instagram_campaign, "instagram_reels")
        hook = str(spec["hook"])
        x_caption = build_platform_caption(hook, x_campaign, "x", links_blocked=links_blocked)
        youtube_caption = build_platform_caption(
            hook,
            youtube_campaign,
            "youtube_shorts",
            links_blocked=False,
        )
        instagram_caption = build_platform_caption(
            hook,
            instagram_campaign,
            "instagram_reels",
            links_blocked=False,
        )
        if links_blocked and _LINK_RE.search(x_caption):
            raise CampaignPlatformError(
                f"Slot {spec['slot']} X caption still contains a link for {x_campaign.get('id')}"
            )
        if contains_loan_spam(x_caption) or contains_loan_spam(youtube_caption) or contains_loan_spam(instagram_caption):
            raise CampaignPlatformError(
                f"Slot {spec['slot']} caption still contains loan text"
            )
        if x_campaign.get("id") == DISHPILOT_ID:
            tags = _HASHTAG_RE.findall(x_caption)
            if tags != ["#Dishpilot"]:
                raise CampaignPlatformError(
                    f"Slot {spec['slot']} Dishpilot X caption must be a hook plus #Dishpilot"
                )
        x_category = X_SLOT_CATEGORIES[index]
        if x_category not in X_VIRAL_CATEGORIES:
            raise CampaignPlatformError(f"Slot {spec['slot']} X category {x_category!r} is outside the locked mix")
        youtube_category = str(plan["category"])
        x_variant = X_VARIANTS[index % len(X_VARIANTS)]
        youtube_ok, youtube_reason = duration_fits(youtube_campaign, YOUTUBE_DURATION_SEC)
        x_ok, x_reason = duration_fits(x_campaign, X_DURATION_SEC)
        instagram_ok, instagram_reason = duration_fits(instagram_campaign, INSTAGRAM_DURATION_SEC)
        if not youtube_ok:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} YouTube render is outside the campaign window: {youtube_reason}"
            )
        if not x_ok:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} X render is outside the campaign window: {x_reason}"
            )
        if not instagram_ok:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} Instagram render is outside the campaign window: {instagram_reason}"
            )
        overlay = {
            "mode": "dual_compatible",
            "youtube_shorts": platform_overlay_spec("youtube_shorts"),
            "x": platform_overlay_spec("x"),
            "instagram_reels": platform_overlay_spec("instagram_reels"),
        }
        if overlay["x"]["logo_text"] != overlay["youtube_shorts"]["logo_text"]:
            overlay["mode"] = "platform_specific_variants"
        for side in ("youtube_shorts", "x", "instagram_reels"):
            side_spec = overlay[side]
            if int(side_spec.get("logo_count") or 0) != 1 or side_spec.get("logo_placement") != "corner":
                raise CampaignPlatformError(
                    f"Slot {spec['slot']} {side} overlay must be a single corner badge"
                )
        slots.append(
            {
                "slot": spec["slot"],
                "time_ist": spec["time_ist"],
                "hook": hook,
                "youtube_campaign": youtube_campaign.get("id"),
                "youtube_campaign_title": youtube_campaign.get("title"),
                "youtube_category": youtube_category,
                "youtube_rate_per_1k_views_usd": _rate(youtube_campaign),
                "youtube_remaining_pool_usd": _remaining_pool(youtube_campaign),
                "youtube_caption": youtube_caption,
                "youtube_submission_url": youtube_campaign.get("submission_url"),
                "youtube_handle": youtube_account["handle"],
                "youtube_username": youtube_account["username"],
                "youtube_mp4_url": PAGES_MEDIA_BASE + str(plan["file"]),
                "youtube_duration_sec": YOUTUBE_DURATION_SEC,
                "youtube_duration_ok": youtube_ok,
                "youtube_duration_reason": youtube_reason,
                "x_campaign": x_campaign.get("id"),
                "x_campaign_title": x_campaign.get("title"),
                "x_category": x_category,
                "x_rate_per_1k_views_usd": _rate(x_campaign),
                "x_remaining_pool_usd": _remaining_pool(x_campaign),
                "x_caption": x_caption,
                "x_submission_url": x_campaign.get("submission_url"),
                "x_handle": x_account["handle"],
                "x_mp4_url": PAGES_MEDIA_BASE + x_variant["file"],
                "x_duration_sec": X_DURATION_SEC,
                "x_duration_ok": x_ok,
                "x_duration_reason": x_reason,
                "x_badge": x_variant["badge"],
                "instagram_campaign": instagram_campaign.get("id"),
                "instagram_campaign_title": instagram_campaign.get("title"),
                "instagram_category": x_category,
                "instagram_rate_per_1k_views_usd": _rate(instagram_campaign),
                "instagram_remaining_pool_usd": _remaining_pool(instagram_campaign),
                "instagram_caption": instagram_caption,
                "instagram_submission_url": instagram_campaign.get("submission_url"),
                "instagram_handle": platform_account(campaigns_data, "instagram_reels")["handle"],
                "instagram_mp4_url": PAGES_MEDIA_BASE + str(plan["file"]),
                "instagram_duration_sec": INSTAGRAM_DURATION_SEC,
                "instagram_duration_ok": instagram_ok,
                "instagram_duration_reason": instagram_reason,
                "overlay": overlay,
            }
        )
    indian_slots = sum(1 for slot in slots if slot["youtube_category"] in INDIAN_CATEGORIES)
    minimum = _min_indian_fraction(campaigns_data)
    if indian_slots / len(slots) < minimum:
        raise CampaignPlatformError(
            f"YouTube Shorts Indian mix is {indian_slots}/{len(slots)}, below {minimum:.0%}"
        )
    return {
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "slot_count": len(slots),
        "guarantee": (
            "Every X (@chadriyo) slot is Dishpilot with #Dishpilot and no loan text. "
            "Every Instagram Reel (@benreelr) is Rascals or Dishpilot with "
            "#Rascals #Dishpilot #Reels. Every YouTube Shorts (Denzen / @denzen0) slot "
            "is Dishpilot or Mr. Pa with #Dishpilot #Shorts. At least 80% of YouTube "
            "slots are Indian actor reels or Indian cinema scenes. Each platform draws "
            "one top-left corner mark."
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
    youtube_ids = [campaign.get("id") for campaign in campaigns_for_platform(campaigns_data, "youtube_shorts")]
    if not youtube_ids:
        errors.append("no YouTube Shorts campaign passed Pool Guard")
        return errors
    x_ids = [campaign.get("id") for campaign in campaigns_for_platform(campaigns_data, "x")]
    if not x_ids:
        errors.append("no X-paying campaign passed Pool Guard")
        return errors
    links_blocked = x_links_blocked(campaigns_data)
    seen_x: set[str] = set()
    seen_x_categories: set[str] = set()
    indian_slots = 0
    for slot in slots:
        if not isinstance(slot, dict):
            errors.append("slot is not an object")
            continue
        slot_no = slot.get("slot")
        youtube_id = slot.get("youtube_campaign")
        x_id = slot.get("x_campaign")
        if youtube_id not in youtube_ids:
            errors.append(
                f"slot {slot_no} youtube_campaign {youtube_id!r} is not an active YouTube Shorts campaign"
            )
            continue
        youtube_campaign = _campaign_by_id(campaigns_data, str(youtube_id))
        if youtube_campaign is None:
            errors.append(f"slot {slot_no} youtube_campaign {youtube_id!r} is missing from active_campaigns")
            continue
        if x_id in CLOSED_CAMPAIGN_IDS:
            errors.append(f"slot {slot_no} pairs X with closed campaign {x_id}")
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
        x_style = caption_style(x_campaign)
        x_cta = str(_rules(x_campaign).get("mandatory_caption") or "")
        if contains_loan_spam(x_caption):
            errors.append(f"slot {slot_no} x_caption contains loan text")
        if contains_loan_spam(youtube_caption):
            errors.append(f"slot {slot_no} youtube_caption contains loan text")
        if x_style == "hook_cta_hashtags" and x_cta and x_cta not in x_caption:
            errors.append(f"slot {slot_no} x_caption is missing the {x_id} CTA")
        if x_id == DISHPILOT_ID:
            tags = _HASHTAG_RE.findall(x_caption)
            if tags != ["#Dishpilot"]:
                errors.append(f"slot {slot_no} Dishpilot X caption must be a hook plus #Dishpilot")
            if x_cta and x_cta in x_caption:
                errors.append(f"slot {slot_no} Dishpilot X caption includes a CTA past the hook and hashtags")
        yt_tags = _HASHTAG_RE.findall(youtube_caption)
        if yt_tags != ["#Dishpilot", "#Shorts"]:
            errors.append(f"slot {slot_no} YouTube caption must include #Dishpilot #Shorts")
        hook = str(slot.get("hook") or "")
        if hook and hook not in x_caption:
            errors.append(f"slot {slot_no} x_caption is missing the hook")
        if hook and hook not in youtube_caption:
            errors.append(f"slot {slot_no} youtube_caption is missing the hook")
        if links_blocked and _LINK_RE.search(x_caption):
            errors.append(f"slot {slot_no} x_caption contains a link")
        if slot.get("x_submission_url") and str(slot.get("x_submission_url")) in x_caption:
            errors.append(f"slot {slot_no} x_caption includes the submission URL")
        x_category = str(slot.get("x_category") or "")
        if x_category not in X_VIRAL_CATEGORIES:
            errors.append(f"slot {slot_no} x_category {x_category!r} is outside the locked X mix")
        else:
            seen_x_categories.add(x_category)
        youtube_category = str(slot.get("youtube_category") or "")
        if youtube_category in INDIAN_CATEGORIES:
            indian_slots += 1
        elif youtube_category not in X_VIRAL_CATEGORIES:
            errors.append(f"slot {slot_no} youtube_category {youtube_category!r} is not an allowed lane")
        overlay = slot.get("overlay") or {}
        if overlay.get("mode") not in ("platform_specific_variants", "dual_compatible"):
            errors.append(f"slot {slot_no} overlay mode is missing")
        expected_x = platform_overlay_spec("x")
        expected_yt = platform_overlay_spec("youtube_shorts")
        expected_ig = platform_overlay_spec("instagram_reels")
        for side, expected in (
            ("x", expected_x),
            ("youtube_shorts", expected_yt),
            ("instagram_reels", expected_ig),
        ):
            actual = overlay.get(side) or {}
            placement = str(actual.get("logo_placement") or "")
            count = int(actual.get("logo_count") or 0)
            if placement not in ("corner", "none"):
                errors.append(f"slot {slot_no} {side} logo must sit in a corner")
            if count > 1:
                errors.append(f"slot {slot_no} {side} has {count} logos")
            if actual.get("logo_subtext"):
                errors.append(f"slot {slot_no} {side} draws a second logo line")
            if count != int(expected.get("logo_count") or 0):
                errors.append(f"slot {slot_no} {side} logo_count does not match the campaign")
            if placement != str(expected.get("logo_placement") or ""):
                errors.append(f"slot {slot_no} {side} logo_placement does not match the campaign")
            if actual.get("logo_text") != expected.get("logo_text"):
                errors.append(f"slot {slot_no} {side} logo_text does not match the campaign")
        if slot.get("x_handle") != platform_account(campaigns_data, "x")["handle"]:
            errors.append(f"slot {slot_no} x_handle is not @chadriyo")
        if slot.get("youtube_handle") != platform_account(campaigns_data, "youtube_shorts")["handle"]:
            errors.append(f"slot {slot_no} youtube_handle is not Denzen")
        instagram_id = slot.get("instagram_campaign")
        instagram_ids = [
            campaign.get("id") for campaign in campaigns_for_platform(campaigns_data, "instagram_reels")
        ]
        if instagram_id not in instagram_ids:
            errors.append(
                f"slot {slot_no} instagram_campaign {instagram_id!r} is not an active Instagram Reels campaign"
            )
        else:
            instagram_campaign = _campaign_by_id(campaigns_data, str(instagram_id))
            if instagram_campaign is None:
                errors.append(f"slot {slot_no} instagram_campaign {instagram_id!r} is missing")
            else:
                try:
                    bind_clip_to_campaign(instagram_campaign, "instagram_reels")
                except CampaignPlatformError as exc:
                    errors.append(f"slot {slot_no}: {exc}")
                if slot.get("instagram_submission_url") != instagram_campaign.get("submission_url"):
                    errors.append(f"slot {slot_no} instagram_submission_url does not match the campaign")
                if float(slot.get("instagram_rate_per_1k_views_usd") or 0) != _rate(instagram_campaign):
                    errors.append(f"slot {slot_no} instagram rate does not match the campaign")
                instagram_caption = str(slot.get("instagram_caption") or "")
                if contains_loan_spam(instagram_caption):
                    errors.append(f"slot {slot_no} instagram_caption contains loan text")
                if _HASHTAG_RE.findall(instagram_caption) != ["#Rascals", "#Dishpilot", "#Reels"]:
                    errors.append(f"slot {slot_no} Instagram caption must include #Rascals #Dishpilot #Reels")
                if hook and hook not in instagram_caption:
                    errors.append(f"slot {slot_no} instagram_caption is missing the hook")
                if slot.get("instagram_handle") != platform_account(campaigns_data, "instagram_reels")["handle"]:
                    errors.append(f"slot {slot_no} instagram_handle is not @benreelr")
                if slot.get("instagram_duration_ok") is not True:
                    errors.append(f"slot {slot_no} instagram_duration_ok must be true")
                instagram_seconds = slot.get("instagram_duration_sec")
                if instagram_seconds is None or not duration_fits(instagram_campaign, float(instagram_seconds))[0]:
                    errors.append(f"slot {slot_no} Instagram duration is outside {instagram_id}'s window")
        if slot.get("youtube_duration_ok") is not True:
            errors.append(f"slot {slot_no} youtube_duration_ok must be true")
        youtube_seconds = slot.get("youtube_duration_sec")
        if youtube_seconds is None or not duration_fits(youtube_campaign, float(youtube_seconds))[0]:
            errors.append(f"slot {slot_no} YouTube duration is outside {youtube_id}'s window")
        if not _YT_MP4_RE.match(str(slot.get("youtube_mp4_url") or "")):
            errors.append(f"slot {slot_no} youtube_mp4_url must be a yt-flp-*.mp4 pages URL")
        if slot.get("x_duration_ok") is not True:
            errors.append(f"slot {slot_no} x_duration_ok must be true")
        x_seconds = slot.get("x_duration_sec")
        if x_seconds is None or not (20 <= float(x_seconds) <= 35):
            errors.append(f"slot {slot_no} X duration must be 20–35s")
        if x_seconds is None or not duration_fits(x_campaign, float(x_seconds))[0]:
            errors.append(f"slot {slot_no} X duration is outside {x_id}'s window")
        if not _X_MP4_RE.match(str(slot.get("x_mp4_url") or "")):
            errors.append(f"slot {slot_no} x_mp4_url must be an x-clip-*.mp4 pages URL")
    missing = [campaign_id for campaign_id in x_ids if campaign_id not in seen_x]
    if missing:
        errors.append("daily manifest never assigns X views to: " + ", ".join(str(item) for item in missing))
    missing_categories = [category for category in X_VIRAL_CATEGORIES if category not in seen_x_categories]
    if missing_categories:
        errors.append("X mix is missing: " + ", ".join(missing_categories))
    minimum = _min_indian_fraction(campaigns_data)
    if indian_slots / DAILY_SLOT_COUNT < minimum:
        errors.append(
            f"YouTube Shorts Indian mix is {indian_slots}/{DAILY_SLOT_COUNT}, below {minimum:.0%}"
        )
    return errors


def feed_from_manifest(manifest: dict) -> dict:
    return {
        "generated_at": manifest.get("generated_at"),
        "slot_count": manifest.get("slot_count"),
        "guarantee": manifest.get("guarantee"),
        "items": manifest.get("slots") or [],
    }


def write_manifest(path: Path | None = None, campaigns_data: dict | None = None) -> dict:
    data = campaigns_data or load_campaigns()
    manifest = build_daily_manifest(data)
    errors = verify_manifest_data(manifest, data)
    if errors:
        raise CampaignPlatformError("; ".join(errors))
    save_json(path or MANIFEST_PATH, manifest)
    save_json(FEED_JSON_PATH, feed_from_manifest(manifest))
    return manifest


def _run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffmpeg failed")[-800:])


def _hook_and_badge_filter(hook: str, badge: str, *, fps: int) -> str:
    """Clear the burned-in center logos, then draw one hook and at most one corner badge."""
    font = BOLD_FONT
    chain = f"scale=1080:1920:flags=lanczos,fps={fps},format=yuv420p,{BURNED_IN_LOGO_FILTER}"
    hook_text = escape_drawtext(hook.strip())
    if hook_text:
        chain += (
            f",drawtext=fontfile='{font}':text='{hook_text}':fontcolor=white:fontsize=48:"
            "borderw=3:bordercolor=black:x=(w-text_w)/2:y=78"
        )
    if badge.strip():
        if is_subtle_corner_badge(badge):
            chain += "," + subtle_corner_drawtext(font, badge)
        else:
            badge_text = escape_drawtext(badge)
            chain += (
                f",drawtext=fontfile='{font}':text='{badge_text}':fontcolor=white:fontsize=28:"
                "borderw=2:bordercolor=black:box=1:boxcolor=black@0.45:boxborderw=10:"
                "x=w-text_w-40:y=h-text_h-48"
            )
    return chain


def dishpilot_clip_filter(
    hook: str,
    *,
    fps: int,
    width: int,
    height: int,
    clean_bottom_strip: bool,
) -> str:
    """Vertical (or native) frame plus one bottom Dishpilot watermark.

    When ``clean_bottom_strip`` is set, the bottom band that holds the baked
    fastloanpilot wordmark is cropped off and the picture is scaled back, so
    the mark is gone without painting an opaque box.
    """
    font = BOLD_FONT
    chain = f"scale={width}:{height}:flags=lanczos,fps={fps},format=yuv420p"
    if clean_bottom_strip:
        if width == 1080 and height == 1920:
            chain += ",delogo=x=60:y=70:w=960:h=90:show=0,delogo=x=200:y=180:w=700:h=180:show=0"
        kept = height - BOTTOM_STRIP_CROP_PX
        if kept < 64:
            raise CampaignPlatformError("Bottom strip crop is taller than the frame")
        chain += f",crop={width}:{kept}:0:0,scale={width}:{height}:flags=lanczos"
    hook_text = escape_drawtext(hook.strip())
    if hook_text:
        chain += (
            f",drawtext=fontfile='{font}':text='{hook_text}':fontcolor=white:fontsize=48:"
            "borderw=3:bordercolor=black:x=(w-text_w)/2:y=78"
        )
    chain += "," + dishpilot_watermark_drawtext(font)
    return chain


def render_dishpilot_clip(
    source: Path,
    dest: Path,
    *,
    hook: str = "",
    duration: float | None = 30,
    fps: int = 30,
    width: int | None = 1080,
    height: int | None = 1920,
    clean_bottom_strip: bool = True,
) -> None:
    """Burn the small Dishpilot watermark. Optionally crop the baked bottom logo."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if width is None or height is None:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0 or "x" not in probe.stdout:
            raise RuntimeError(probe.stderr or "ffprobe failed")
        width_text, height_text = probe.stdout.strip().split("x", 1)
        width, height = int(width_text), int(height_text)
    video_filter = dishpilot_clip_filter(
        hook,
        fps=fps,
        width=width,
        height=height,
        clean_bottom_strip=clean_bottom_strip,
    )
    trim = ["-t", str(duration)] if duration is not None else []
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            *trim,
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            ENCODE_PRESET,
            "-crf",
            ENCODE_CRF,
            "-r",
            str(fps),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )


def _encode_variant(
    source: Path,
    dest: Path,
    *,
    hook: str,
    badge: str,
    duration: float | None,
    fps: int,
    logo_png: Path | None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    video_filter = _hook_and_badge_filter(hook, "" if logo_png else badge, fps=fps)
    trim = ["-t", str(duration)] if duration is not None else []
    if logo_png is None:
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                *trim,
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-preset",
                ENCODE_PRESET,
                "-crf",
                ENCODE_CRF,
                "-r",
                str(fps),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
        return
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-i",
            str(logo_png),
            *trim,
            "-filter_complex",
            f"[0:v]{video_filter}[base];"
            "[1:v]scale=200:-1:flags=lanczos,format=rgba[logo];"
            "[base][logo]overlay=W-w-36:H-h-48[vout]",
            "-map",
            "[vout]",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            ENCODE_PRESET,
            "-crf",
            ENCODE_CRF,
            "-r",
            str(fps),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )


def clean_library_masters(media_dir: Path | None = None) -> list[Path]:
    """Strip the stacked center logos from whop/v masters and leave one corner badge."""
    target = media_dir or MEDIA_DIR
    logo = ROOT / "logos" / "fastloanpilot.png"
    written: list[Path] = []
    for name in LIBRARY_SOURCES:
        source = target / name
        if not source.exists():
            continue
        temporary = target / f".{name}.clean.mp4"
        _encode_variant(
            source,
            temporary,
            hook="",
            badge="",
            duration=None,
            fps=30,
            logo_png=logo if logo.exists() else None,
        )
        temporary.replace(source)
        written.append(source)
    return written


def render_platform_variants(media_dir: Path | None = None) -> list[Path]:
    """Write the 12.5s YouTube Shorts files and the 30s X files into whop/v/.

    Center logos are removed first. A corner badge is added only when that
    variant's campaign requires one. Library masters are cleaned afterwards so
    the six platform files are cut from the pre-clean sources.
    """
    target = media_dir or MEDIA_DIR
    target.mkdir(parents=True, exist_ok=True)
    logo = ROOT / "logos" / "fastloanpilot.png"
    written: list[Path] = []
    for variant in YOUTUBE_VARIANTS:
        source = target / str(variant["source"])
        dest = target / str(variant["file"])
        _encode_variant(
            source,
            dest,
            hook=str(variant["hook"]),
            badge="",
            duration=YOUTUBE_DURATION_SEC,
            fps=60,
            logo_png=logo if variant.get("corner_logo") and logo.exists() else None,
        )
        written.append(dest)
    for variant in X_VARIANTS:
        source = target / str(variant["source"])
        dest = target / str(variant["file"])
        _encode_variant(
            source,
            dest,
            hook=str(variant["hook"]),
            badge=str(variant["badge"]),
            duration=X_DURATION_SEC,
            fps=30,
            logo_png=None,
        )
        written.append(dest)
    written.extend(clean_library_masters(target))
    return written


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Whop platform campaign mapping")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-manifest", help="Write whop/manifest.json and whop/feed.json for the 10 daily slots")
    sub.add_parser("verify-manifest", help="Fail if any slot pairs a platform with a campaign that does not pay it")
    sub.add_parser("render-variants", help="Render yt-flp-*.mp4 and x-clip-*.mp4 into whop/v/")
    args = parser.parse_args(argv)
    data = load_campaigns()
    if args.command == "build-manifest":
        manifest = write_manifest(campaigns_data=data)
        print(f"wrote {MANIFEST_PATH} and {FEED_JSON_PATH} ({manifest['slot_count']} slots)")
        return 0
    if args.command == "render-variants":
        paths = render_platform_variants()
        for path in paths:
            print(path)
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
