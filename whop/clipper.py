#!/usr/bin/env python3
"""Platform-specific Whop campaign mapping.

X (@chadriyo) posts to Dishpilot. YouTube Shorts (Denzen) posts to Dishpilot
and Mr. Pa. Instagram Reels (@benreelr) posts to Rascals, Dishpilot, and Mr. Pa.
TikTok (@bi67325) posts to Dishpilot and Rascals. FastLoanPilot and Nova are
closed. The daily run (build_daily_30, also exposed as build-daily-40) fills 10
slots on each of the four Buffer channels, 40 posts a day, stamped Dishpilot or
Rascals only.
ingest-urls downloads an X or TikTok link, stamps one campaign name, and queues it.
Pool Guard rejects bounties whose denial rate is above the configured cap
(Shane Gillis is 99.8%).

Visual rule: one top-left campaign name only — "dishpilot", "rascals", or
"mr. pa". Never two names, and never "rascals · dishpilot". The caption carries
that campaign's single hashtag.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
CAMPAIGNS_PATH = ROOT / "campaigns.json"
MANIFEST_PATH = ROOT / "manifest.json"
FEED_JSON_PATH = ROOT / "feed.json"
MEDIA_DIR = ROOT / "v"
PAGES_MEDIA_BASE = "https://chatgpt820970.github.io/chad-media/whop/v/"
BOLD_FONT = "/usr/share/fonts/truetype/macos/Inter-Bold.ttf"

SUPPORTED_PLATFORMS = ("x", "youtube_shorts", "instagram_reels", "tiktok")
PLATFORM_ACCOUNTS = {
    "x": "@chadriyo",
    "youtube_shorts": "Denzen (@denzen0)",
    "instagram_reels": "@benreelr",
    "tiktok": "@bi67325",
}
BUFFER_ORGANIZATION_ID = "6ac41da09a0b5aca7c671500"
X_CAMPAIGN_IDS = ("dishpilot-clipping",)
YOUTUBE_PRIMARY_ID = "dishpilot-clipping"
MR_PA_ID = "mr-pa-clipping"
RASCALS_ID = "rascals-theme-pages"
DISHPILOT_ID = "dishpilot-clipping"
CLOSED_CAMPAIGN_IDS = ("fast-loan-pilot", "nova-x-clipping")
# Full HD encode. No bitrate cap: CRF 17 with the fast preset.
ENCODE_PRESET = "fast"
ENCODE_CRF = "17"
# Ingested X/TikTok sources are the sharpest the platforms serve, so they get a lower CRF.
INGEST_CRF = "16"
INGEST_SHARPEN = "unsharp=3:3:0.6:3:3:0.0"
YTDLP_FORMAT = "bestvideo+bestaudio/best"
YTDLP_SORT = "res,fps,br"
DAILY_SLOT_COUNT = 10
OPEN_STATUSES = ("open", "active", "live")
INDIAN_CATEGORIES = ("indian_actor_reels", "indian_movie_scenes")
# Daily catalog and ingest priority, highest first: romantic and kissing couple
# scenes, glamorous girls, 4K anime edits, then 4K movie edits. Everything else keeps
# catalog order.
PRIORITY_CATEGORIES = ("romantic_couples", "glamorous_girls", "anime_4k", "movie_4k", "celebrity")
INGEST_CATEGORIES = (*PRIORITY_CATEGORIES, "funny", "knowledge", "concert", "other")
X_VIRAL_CATEGORIES = ("funny", "ai", "knowledge", "movie", "anime", "celebrity", "concert")
COMMUNITY_GUIDELINES_BLOCKED_PATTERNS = (
    ("adult sexual content", re.compile(
        r"(?:\b(?:nsfw|porn(?:ography|ographic)?|xxx|onlyfans|nude|nudity|"
        r"naked|explicit\s+sex(?:ual)?|sexually\s+explicit|hentai|fetish)\b|"
        r"(?<!\w)18\s*\+(?!\w))",
        re.IGNORECASE,
    )),
    ("sexual exploitation", re.compile(
        r"\b(?:sexual\s+exploitation|revenge\s+porn|non[- ]consensual|"
        r"child\s+(?:porn|sexual)|underage\s+sex)\b",
        re.IGNORECASE,
    )),
    ("graphic violence", re.compile(
        r"\b(?:graphic\s+violence|gore|gory|bloodbath|behead(?:ing|ed)?|"
        r"decapitat(?:ion|ed)|dismember(?:ment|ed)?|mutilat(?:ion|ed)|snuff)\b",
        re.IGNORECASE,
    )),
    ("hate or extremism", re.compile(
        r"\b(?:hate\s+speech|racial\s+slur|white\s+supremac(?:y|ist)|"
        r"neo[- ]?nazi|terrorist\s+propaganda|violent\s+extremis(?:m|t))\b",
        re.IGNORECASE,
    )),
    ("self-harm", re.compile(
        r"\b(?:self[- ]harm|suicide\s+(?:method|tutorial|challenge)|"
        r"how\s+to\s+(?:self[- ]harm|commit\s+suicide))\b",
        re.IGNORECASE,
    )),
    ("dangerous or illegal activity", re.compile(
        r"\b(?:drug\s+dealing|buy\s+illegal\s+drugs|bomb[- ]making|"
        r"weapon[- ]making|human\s+trafficking|animal\s+cruelty)\b",
        re.IGNORECASE,
    )),
    ("scam or platform manipulation", re.compile(
        r"\b(?:phishing|credential\s+stealing|engagement\s+bot|fake\s+giveaway|"
        r"guaranteed\s+money|ban\s+evasion)\b",
        re.IGNORECASE,
    )),
)
COMMUNITY_GUIDELINES_UNSAFE_FLAGS = frozenset({
    "adult",
    "adult_content",
    "age_restricted",
    "explicit",
    "graphic_violence",
    "hate",
    "hate_speech",
    "nsfw",
    "nudity",
    "porn",
    "restricted",
    "self_harm",
    "sexual",
    "sexual_content",
    "terrorism",
    "unsafe",
    "violence_graphic",
})
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
TIKTOK_SLOT_PLAN = tuple(
    {"campaign_id": DISHPILOT_ID if index % 2 == 0 else RASCALS_ID}
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
TIKTOK_DURATION_SEC = 15.0
X_DURATION_SEC = 30.0
PLATFORM_WATERMARK = {
    "x": "dishpilot",
    "youtube_shorts": "dishpilot",
    "instagram_reels": "rascals",
    "tiktok": "dishpilot",
}
# One name and one campaign hashtag. Platform suffixes (#Shorts, #Reels) are not a second brand.
CAMPAIGN_MARK = {
    "dishpilot-clipping": ("dishpilot", "#Dishpilot"),
    "rascals-theme-pages": ("rascals", "#Rascals"),
    "mr-pa-clipping": ("mr. pa", "#MrPa"),
}
# Source of truth for the three paid campaign brands. Values deliberately use
# public platform names rather than internal feed keys.
PLATFORM_ELIGIBLE_CAMPAIGNS = {
    "dishpilot-clipping": ("x", "youtube", "instagram", "tiktok"),
    "rascals-theme-pages": ("instagram", "tiktok", "youtube"),
    "mr-pa-clipping": ("youtube", "instagram", "tiktok"),
}
ELIGIBILITY_PLATFORM_NAMES = {
    "x": "x",
    "youtube_shorts": "youtube",
    "instagram_reels": "instagram",
    "tiktok": "tiktok",
}
PLATFORM_SUFFIX_TAGS = {
    "x": [],
    "youtube_shorts": ["#Shorts"],
    "instagram_reels": ["#Reels"],
    "tiktok": [],
}
INSTAGRAM_CAMPAIGN_WATERMARK = {
    campaign_id: mark[0] for campaign_id, mark in CAMPAIGN_MARK.items()
}
INSTAGRAM_CAMPAIGN_HASHTAGS = {
    campaign_id: [mark[1], *PLATFORM_SUFFIX_TAGS["instagram_reels"]]
    for campaign_id, mark in CAMPAIGN_MARK.items()
}
PLATFORM_HASHTAGS = {
    "x": ["#Dishpilot"],
    "youtube_shorts": ["#Dishpilot", "#Shorts"],
    "instagram_reels": ["#Rascals", "#Reels"],
    "tiktok": ["#Dishpilot"],
}
CAMPAIGN_ALIASES = {
    "dishpilot": "dishpilot-clipping",
    "dishpilot-clipping": "dishpilot-clipping",
    "rascals": "rascals-theme-pages",
    "rascals-theme-pages": "rascals-theme-pages",
    "mrpa": "mr-pa-clipping",
    "mr. pa": "mr-pa-clipping",
    "mr-pa": "mr-pa-clipping",
    "mr-pa-clipping": "mr-pa-clipping",
}
SINGLE_CAMPAIGN_NAMES = frozenset({"dishpilot", "rascals", "mr. pa"})
SINGLE_CAMPAIGN_TAGS = ("#Dishpilot", "#Rascals", "#MrPa")


def platform_watermark(platform: str, campaign_id: str | None = None) -> str:
    """The one campaign name drawn on that post. Never two names."""
    if campaign_id in CAMPAIGN_MARK:
        return CAMPAIGN_MARK[campaign_id][0]
    text = PLATFORM_WATERMARK[platform]
    if text not in SINGLE_CAMPAIGN_NAMES:
        raise CampaignPlatformError(f"Refusing combined watermark {text!r}")
    return text


def platform_hashtags(platform: str, campaign_id: str | None = None) -> list[str]:
    """One campaign hashtag, plus a platform suffix that does not name a second brand."""
    if campaign_id in CAMPAIGN_MARK:
        return [CAMPAIGN_MARK[campaign_id][1], *PLATFORM_SUFFIX_TAGS[platform]]
    return list(PLATFORM_HASHTAGS[platform])


def campaign_id_from_alias(alias: str) -> str:
    key = alias.strip().casefold()
    campaign_id = CAMPAIGN_ALIASES.get(key)
    if campaign_id is None:
        raise CampaignPlatformError(
            f"Unknown campaign {alias!r}. Expected dishpilot, rascals, or mrpa."
        )
    return campaign_id


# Buffer channel ids. Instagram is the @benreelr channel supplied for the daily run.
DAILY_CHANNEL_IDS = {
    "x": "6ac41eb06a5c39ccb625fdf1",
    "youtube_shorts": "6ac4da806a5c39ccb62eab64",
    "instagram_reels": "6ac93d936a5c39ccb66624d7",
    "tiktok": "6add07e4778b240e2fb35110",
}
# Two hours apart. 00:00 and 02:00 are the next IST night, which is still the
# same UTC date as the 00:15 UTC cron (05:45 IST).
DAILY_30_TIMES_IST = (
    "08:00",
    "10:00",
    "12:00",
    "14:00",
    "16:00",
    "18:00",
    "20:00",
    "22:00",
    "00:00",
    "02:00",
)
DAILY_30_PER_PLATFORM = 10
DAILY_30_INDIAN_SLOTS = 8
DAILY_40_PER_PLATFORM = DAILY_30_PER_PLATFORM
DAILY_40_TOTAL_POSTS = DAILY_40_PER_PLATFORM * 4
# Key used for each platform inside the schedule's `slots` object.
DAILY_SLOT_KEYS = {
    "x": "x",
    "youtube_shorts": "youtube",
    "instagram_reels": "instagram",
    "tiktok": "tiktok",
}
DAILY_WATERMARK_POSITION = {
    "placement": "top-left",
    "x": "w*0.025",
    "y": "h*0.03",
    "expr": "x=w*0.025:y=h*0.03",
}
# The daily run stamps Dishpilot or Rascals only. Rascals leads the Reels rotation.
INSTAGRAM_DAILY_ROTATION = (RASCALS_ID, DISHPILOT_ID)
# TikTok pays Dishpilot ($2/1K) and Rascals ($3/1K). Dishpilot leads.
TIKTOK_DAILY_ROTATION = (DISHPILOT_ID, RASCALS_ID)
SHORTS_MEDIA_BASE = "https://chatgpt820970.github.io/chad-media/shorts/v/"
_IST_OFFSET_MINUTES = 5 * 60 + 30
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


def _metadata_scalars(value, path: str = "source_metadata"):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            child_path = f"{path}.{normalized}"
            flag_name = normalized.removesuffix("_score")
            if flag_name in COMMUNITY_GUIDELINES_UNSAFE_FLAGS:
                if child is True or (isinstance(child, str) and child.casefold() in {"true", "yes", "1"}):
                    yield child_path, normalized
                elif normalized.endswith("_score") and isinstance(child, (int, float)) and child > 0:
                    yield child_path, flag_name
            yield from _metadata_scalars(child, child_path)
    elif isinstance(value, (list, tuple, set)):
        for index, child in enumerate(value):
            yield from _metadata_scalars(child, f"{path}[{index}]")
    elif value is not None and value is not False:
        yield path, str(value)


def community_guidelines_violations(
    *,
    title: str = "",
    hook: str = "",
    tags=(),
    source_metadata=None,
) -> list[str]:
    """Return four-platform textual/metadata safety violations.

    This deliberately permits glamorous, attractive, romantic, anime, and
    movie-scene labels. It blocks explicit/adult content and high-risk safety
    labels shared by X, Instagram, YouTube, and TikTok.
    """
    if isinstance(tags, str):
        tag_values = [tags]
    else:
        tag_values = [str(tag) for tag in tags or ()]
    fields = [("title", title), ("hook", hook)]
    fields.extend((f"tags[{index}]", tag) for index, tag in enumerate(tag_values))
    fields.extend(_metadata_scalars(source_metadata or {}))
    violations: list[str] = []
    for field, raw_value in fields:
        value = str(raw_value)
        for label, pattern in COMMUNITY_GUIDELINES_BLOCKED_PATTERNS:
            if pattern.search(value):
                finding = f"{field}: {label}"
                if finding not in violations:
                    violations.append(finding)
        normalized_value = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
        if normalized_value in COMMUNITY_GUIDELINES_UNSAFE_FLAGS:
            finding = f"{field}: unsafe safety flag"
            if finding not in violations:
                violations.append(finding)
    return violations


def check_community_guidelines_safe(
    title: str | dict = "",
    hook: str = "",
    tags=(),
    source_metadata=None,
) -> bool:
    """True only when text and supplied source metadata pass all four platforms.

    A feed/catalog item dictionary can be passed as the first argument for
    callers that already have the complete content record.
    """
    if isinstance(title, dict):
        item = title
        caption = str(item.get("caption") or "")
        title = str(item.get("title") or "")
        hook = str(item.get("hook") or caption)
        tags = item.get("tags") or _HASHTAG_RE.findall(caption)
        source_metadata = {
            "category": item.get("category"),
            "source": item.get("source"),
            "source_url": item.get("source_url") or item.get("video_url"),
            "source_metadata": item.get("source_metadata"),
        }
    return not community_guidelines_violations(
        title=str(title),
        hook=hook,
        tags=tags,
        source_metadata=source_metadata,
    )


def assert_community_guidelines_safe(
    *,
    title: str = "",
    hook: str = "",
    tags=(),
    source_metadata=None,
    context: str = "clip",
) -> None:
    violations = community_guidelines_violations(
        title=title,
        hook=hook,
        tags=tags,
        source_metadata=source_metadata,
    )
    if violations:
        raise CampaignPlatformError(
            f"{context} failed four-platform Community Guidelines safety: "
            + "; ".join(violations)
        )


def assert_feed_item_community_guidelines_safe(item: dict) -> None:
    caption = str(item.get("caption") or "")
    assert_community_guidelines_safe(
        title=str(item.get("title") or ""),
        hook=caption,
        tags=_HASHTAG_RE.findall(caption),
        source_metadata={
            "category": item.get("category"),
            "source": item.get("source"),
            "source_url": item.get("source_url"),
            "source_metadata": item.get("source_metadata"),
        },
        context=f"feed item {item.get('clip_id') or '<unknown>'}",
    )


def _safe_caption(caption: str, hook: str, platform: str) -> str:
    cleaned = caption.strip()
    assert_community_guidelines_safe(
        title=hook,
        hook=cleaned,
        tags=_HASHTAG_RE.findall(cleaned),
        source_metadata={"platform": platform},
        context=f"{platform} caption",
    )
    return cleaned


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


def campaign_is_platform_eligible(campaign_id: str, platform: str) -> bool:
    """Whether the explicit paid-campaign matrix permits this pairing."""
    public_platform = ELIGIBILITY_PLATFORM_NAMES.get(platform)
    if public_platform is None:
        return False
    eligible = PLATFORM_ELIGIBLE_CAMPAIGNS.get(campaign_id)
    return eligible is None or public_platform in eligible


def bind_clip_to_campaign(campaign: dict, platform: str) -> dict:
    """Raise if this clip's platform is not explicitly paid by the campaign."""
    campaign_id = str(campaign.get("id") or "")
    if not campaign_is_platform_eligible(campaign_id, platform):
        public_platform = ELIGIBILITY_PLATFORM_NAMES.get(platform, platform)
        raise CampaignPlatformError(
            f"Refusing to pair {campaign_id!r} with {public_platform!r}: "
            f"PLATFORM_ELIGIBLE_CAMPAIGNS={PLATFORM_ELIGIBLE_CAMPAIGNS.get(campaign_id)}"
        )
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
        if platform not in accepted or not campaign_is_platform_eligible(
            str(campaign.get("id") or ""), platform
        ):
            continue
        campaign_id = str(campaign.get("id") or "")
        if allowed is not None and campaign_id not in allowed:
            continue
        by_id[campaign_id] = campaign
        ordered.append(campaign)
    if isinstance(allow_ids, list):
        return [by_id[campaign_id] for campaign_id in allow_ids if campaign_id in by_id]
    return ordered


def assert_feed_item_campaign_identity(item: dict, campaigns_data: dict | None = None) -> None:
    """Fail closed on campaign/platform, watermark, or caption-tag mix-ups."""
    platform = str(item.get("platform") or "")
    campaign_id = str(item.get("campaign_id") or "")
    if campaign_id not in CAMPAIGN_MARK:
        raise CampaignPlatformError(f"Feed item has unknown campaign {campaign_id!r}")
    if not campaign_is_platform_eligible(campaign_id, platform):
        raise CampaignPlatformError(
            f"{campaign_id!r} is not eligible for {ELIGIBILITY_PLATFORM_NAMES.get(platform, platform)!r}"
        )
    expected_badge, expected_campaign_tag = CAMPAIGN_MARK[campaign_id]
    actual_badge = str(item.get("watermark_text") or "")
    if actual_badge != expected_badge:
        raise CampaignPlatformError(
            f"{campaign_id!r} requires watermark {expected_badge!r}, found {actual_badge!r}"
        )
    tags = _HASHTAG_RE.findall(str(item.get("caption") or ""))
    campaign_tags = [tag for tag in tags if tag in SINGLE_CAMPAIGN_TAGS]
    if campaign_tags != [expected_campaign_tag]:
        raise CampaignPlatformError(
            f"{campaign_id!r} requires exactly {expected_campaign_tag}, found {campaign_tags!r}"
        )
    expected_tags = platform_hashtags(platform, campaign_id)
    if tags != expected_tags:
        raise CampaignPlatformError(
            f"{campaign_id!r} on {platform} requires caption tags {expected_tags!r}, found {tags!r}"
        )
    if campaigns_data is not None:
        campaign = _campaign_by_id(campaigns_data, campaign_id)
        if campaign is None:
            raise CampaignPlatformError(f"Feed campaign {campaign_id!r} is missing")
        bind_clip_to_campaign(campaign, platform)
        eligible_ids = {
            str(row.get("id"))
            for row in campaigns_for_platform(campaigns_data, platform)
        }
        if campaign_id not in eligible_ids:
            raise CampaignPlatformError(
                f"{campaign_id!r} is not active, paying, and routed for {platform}"
            )


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

    Routed Dishpilot, Rascals, and Mr. Pa posts use one campaign hashtag that
    matches the watermark: #Dishpilot, #Rascals, or #MrPa. YouTube also adds
    #Shorts and Instagram also adds #Reels. Other campaigns keep their own caption style.
    X drops raw links when the channel blocks them, and loan phrasing is removed
    from every X caption.
    """
    rules = _rules(campaign)
    style = caption_style(campaign)
    hook_text = hook.strip()
    platform_tags = platform_hashtags(platform, campaign.get("id")) if campaign.get("id") in {
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
        return _safe_caption(caption, hook_text, platform)
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
    return _safe_caption(caption, hook_text, platform)


def x_links_blocked(campaigns_data: dict) -> bool:
    routing = _routing(campaigns_data, "x")
    if "links_blocked" in routing:
        return bool(routing["links_blocked"])
    channel = (campaigns_data.get("channels") or {}).get("x") or {}
    return bool(channel.get("links_blocked", True))


DISHPILOT_WATERMARK_TEXT = "dishpilot"
RASCALS_WATERMARK_TEXT = "rascals"
MR_PA_WATERMARK_TEXT = "mr. pa"
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
    return letters in {"dishpilot", "dishpilotcom", "rascals", "mrpa"}


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


def platform_overlay_spec(platform: str, campaign_id: str | None = None) -> dict:
    """The single corner mark required on that platform."""
    return {
        "logo_overlay_required": True,
        "logo_placement": "corner",
        "logo_count": 1,
        "logo_text": platform_watermark(platform, campaign_id),
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
    if platform == "tiktok":
        channel = channels.get("tiktok") or {}
        return {
            "handle": routing.get("handle") or channel.get("handle") or "@bi67325",
            "username": routing.get("username") or channel.get("username") or "@bi67325",
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
    instagram_account = platform_account(campaigns_data, "instagram_reels")
    tiktok_account = platform_account(campaigns_data, "tiktok")
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
        tiktok_plan = TIKTOK_SLOT_PLAN[index]
        tiktok_campaign = _campaign_by_id(campaigns_data, str(tiktok_plan["campaign_id"]))
        if tiktok_campaign is None:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} TikTok campaign {tiktok_plan['campaign_id']!r} is missing"
            )
        bind_clip_to_campaign(tiktok_campaign, "tiktok")
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
        tiktok_caption = build_platform_caption(
            hook,
            tiktok_campaign,
            "tiktok",
            links_blocked=False,
        )
        if links_blocked and _LINK_RE.search(x_caption):
            raise CampaignPlatformError(
                f"Slot {spec['slot']} X caption still contains a link for {x_campaign.get('id')}"
            )
        if any(
            contains_loan_spam(caption)
            for caption in (x_caption, youtube_caption, instagram_caption, tiktok_caption)
        ):
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
        tiktok_ok, tiktok_reason = duration_fits(tiktok_campaign, TIKTOK_DURATION_SEC)
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
        if not tiktok_ok:
            raise CampaignPlatformError(
                f"Slot {spec['slot']} TikTok render is outside the campaign window: {tiktok_reason}"
            )
        overlay = {
            "mode": "dual_compatible",
            "youtube_shorts": platform_overlay_spec("youtube_shorts", youtube_campaign.get("id")),
            "x": platform_overlay_spec("x", x_campaign.get("id")),
            "instagram_reels": platform_overlay_spec("instagram_reels", instagram_campaign.get("id")),
            "tiktok": platform_overlay_spec("tiktok", tiktok_campaign.get("id")),
        }
        if len(
            {
                overlay[side]["logo_text"]
                for side in ("youtube_shorts", "x", "instagram_reels", "tiktok")
            }
        ) > 1:
            overlay["mode"] = "platform_specific_variants"
        for side in ("youtube_shorts", "x", "instagram_reels", "tiktok"):
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
                "instagram_handle": instagram_account["handle"],
                "instagram_mp4_url": PAGES_MEDIA_BASE + str(plan["file"]),
                "instagram_duration_sec": INSTAGRAM_DURATION_SEC,
                "instagram_duration_ok": instagram_ok,
                "instagram_duration_reason": instagram_reason,
                "tiktok_campaign": tiktok_campaign.get("id"),
                "tiktok_campaign_title": tiktok_campaign.get("title"),
                "tiktok_category": x_category,
                "tiktok_rate_per_1k_views_usd": _rate(tiktok_campaign),
                "tiktok_remaining_pool_usd": _remaining_pool(tiktok_campaign),
                "tiktok_caption": tiktok_caption,
                "tiktok_submission_url": tiktok_campaign.get("submission_url"),
                "tiktok_handle": tiktok_account["handle"],
                "tiktok_username": tiktok_account["username"],
                "tiktok_mp4_url": PAGES_MEDIA_BASE + str(plan["file"]),
                "tiktok_duration_sec": TIKTOK_DURATION_SEC,
                "tiktok_duration_ok": tiktok_ok,
                "tiktok_duration_reason": tiktok_reason,
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
        "platform_eligible_campaigns": {
            campaign_id: list(platforms)
            for campaign_id, platforms in PLATFORM_ELIGIBLE_CAMPAIGNS.items()
        },
        "guarantee": (
            "Every X, YouTube, Instagram, and TikTok slot uses a campaign permitted by "
            "PLATFORM_ELIGIBLE_CAMPAIGNS. Its caption has only that campaign's hashtag "
            "and its overlay logo_text is the matching dishpilot, rascals, or mr. pa badge. "
            "Rascals and Mr. Pa are never assigned to X. At least 80% of YouTube slots "
            "are Indian actor reels or Indian cinema scenes. Each platform draws one "
            "top-left corner mark."
        ),
        "slots": slots,
    }


def _campaign_by_id(campaigns_data: dict, campaign_id: str) -> dict | None:
    for campaign in campaigns_data.get("active_campaigns") or []:
        if isinstance(campaign, dict) and campaign.get("id") == campaign_id:
            return campaign
    return None


MANIFEST_PLATFORM_FIELDS = (
    ("x", "x", "x"),
    ("youtube", "youtube_shorts", "youtube_shorts"),
    ("instagram", "instagram_reels", "instagram_reels"),
    ("tiktok", "tiktok", "tiktok"),
)


def verify_slot_campaign_identity(slot: dict, campaigns_data: dict) -> list[str]:
    """Verify eligibility, badge, and caption identity on every platform in one slot."""
    errors: list[str] = []
    slot_no = slot.get("slot")
    overlay = slot.get("overlay") if isinstance(slot.get("overlay"), dict) else {}
    for prefix, platform, overlay_key in MANIFEST_PLATFORM_FIELDS:
        campaign_id = str(slot.get(f"{prefix}_campaign") or "")
        campaign = _campaign_by_id(campaigns_data, campaign_id)
        if campaign is None:
            errors.append(f"slot {slot_no} {prefix}_campaign {campaign_id!r} is missing")
            continue
        if campaign_id not in PLATFORM_ELIGIBLE_CAMPAIGNS:
            errors.append(
                f"slot {slot_no} {prefix}_campaign {campaign_id!r} is absent from "
                "PLATFORM_ELIGIBLE_CAMPAIGNS"
            )
            continue
        try:
            bind_clip_to_campaign(campaign, platform)
        except CampaignPlatformError as exc:
            errors.append(f"slot {slot_no}: {exc}")
        routed_ids = {
            str(row.get("id"))
            for row in campaigns_for_platform(campaigns_data, platform)
        }
        if campaign_id not in routed_ids:
            errors.append(
                f"slot {slot_no} {campaign_id!r} is not active, paying, and routed for {platform}"
            )
        expected_badge, expected_campaign_tag = CAMPAIGN_MARK[campaign_id]
        actual_overlay = overlay.get(overlay_key) if isinstance(overlay, dict) else {}
        actual_badge = (
            actual_overlay.get("logo_text") if isinstance(actual_overlay, dict) else None
        )
        if actual_badge != expected_badge:
            errors.append(
                f"slot {slot_no} overlay.{overlay_key}.logo_text {actual_badge!r} "
                f"does not match {campaign_id!r} ({expected_badge!r})"
            )
        caption = str(slot.get(f"{prefix}_caption") or "")
        tags = _HASHTAG_RE.findall(caption)
        campaign_tags = [tag for tag in tags if tag in SINGLE_CAMPAIGN_TAGS]
        if campaign_tags != [expected_campaign_tag]:
            errors.append(
                f"slot {slot_no} {prefix}_caption campaign tags {campaign_tags!r} "
                f"do not exactly match {campaign_id!r} ({expected_campaign_tag})"
            )
        expected_tags = platform_hashtags(platform, campaign_id)
        if tags != expected_tags:
            errors.append(
                f"slot {slot_no} {prefix}_caption tags {tags!r} must be {expected_tags!r}"
            )
    return errors


def verify_manifest_data(manifest: dict, campaigns_data: dict) -> list[str]:
    """Return human-readable mapping violations. An empty list means the manifest is safe to post."""
    errors: list[str] = []
    expected_eligibility = {
        campaign_id: list(platforms)
        for campaign_id, platforms in PLATFORM_ELIGIBLE_CAMPAIGNS.items()
    }
    if manifest.get("platform_eligible_campaigns") != expected_eligibility:
        errors.append("manifest platform_eligible_campaigns does not match the source-of-truth matrix")
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
        errors.extend(verify_slot_campaign_identity(slot, campaigns_data))
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
        expected_yt_tags = platform_hashtags("youtube_shorts", str(youtube_id))
        if yt_tags != expected_yt_tags:
            errors.append(
                f"slot {slot_no} YouTube caption must be {' '.join(expected_yt_tags)}"
            )
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
        expected_x = platform_overlay_spec("x", str(x_id or ""))
        expected_yt = platform_overlay_spec("youtube_shorts", str(youtube_id or ""))
        expected_ig = platform_overlay_spec("instagram_reels", str(slot.get("instagram_campaign") or ""))
        expected_tiktok = platform_overlay_spec("tiktok", str(slot.get("tiktok_campaign") or ""))
        for side, expected in (
            ("x", expected_x),
            ("youtube_shorts", expected_yt),
            ("instagram_reels", expected_ig),
            ("tiktok", expected_tiktok),
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
                expected_tags = platform_hashtags("instagram_reels", instagram_campaign.get("id"))
                if _HASHTAG_RE.findall(instagram_caption) != expected_tags:
                    errors.append(f"slot {slot_no} Instagram caption must be {' '.join(expected_tags)} only")
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
        "platform_eligible_campaigns": manifest.get("platform_eligible_campaigns"),
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


def _pages_url(filename: str) -> str:
    return f"{PAGES_MEDIA_BASE}{filename}"


def _shorts_url(clip_id: str) -> str:
    return f"{SHORTS_MEDIA_BASE}{clip_id}.mp4"


# Stable catalog. Indian actor / cinema rows come first so YouTube can take
# eight unseen ones and still leave some for the next day. Global rows fill X,
# Instagram, and the two non-Indian YouTube slots. Raw twimg links are the
# hd-live source; packaged files use the Pages URL.
RAW_MEDIA_BASE = "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/"
# A fifth element locks a clip to one campaign because that campaign's name is already
# burned into the pixels (ig-hd-1..5 and Spider-Man are dishpilot, ig-hd-6..9 are rascals).
_DAILY_CLIP_ROWS = (
    ("flp-diljit-worldtour", "indian_actor_reels", "Diljit shut the stadium down", _pages_url("flp-diljit-worldtour.mp4")),
    ("flp-honeysingh-vibe", "indian_actor_reels", "Honey Singh takes the drop", _pages_url("flp-honeysingh-vibe.mp4")),
    ("yt-flp-1", "indian_actor_reels", "Wait for Diljit to hit the note", _pages_url("yt-flp-1.mp4")),
    ("yt-flp-2", "indian_movie_scenes", "When Honey Singh owns the beat", _pages_url("yt-flp-2.mp4")),
    ("tt-7087185238102723842", "indian_actor_reels", "Honey Singh takes the chorus", _shorts_url("tt-7087185238102723842")),
    ("tt-7683220492567809294", "indian_actor_reels", "Diljit brings Daura to the crowd", _shorts_url("tt-7683220492567809294")),
    ("x-sachin", "indian_actor_reels", "Sachin still stops the room", "https://video.twimg.com/amplify_video/2110725157686063105/vid/avc1/720x720/9wEKen-NsjmE2zu7.mp4"),
    ("x-rohit", "indian_actor_reels", "Rohit brings the crowd up", "https://video.twimg.com/amplify_video/2113090359249240064/vid/avc1/480x852/er9LJ0EKKDLnQDX5.mp4"),
    ("x-srk-salman", "indian_movie_scenes", "Salman and Shah Rukh share the frame", "https://video.twimg.com/amplify_video/2110404095626612736/vid/avc1/640x360/2vmTjUOMtRB6yEtE.mp4"),
    ("x-ranbir-alia", "indian_movie_scenes", "Ranbir and Alia own the scene", "https://video.twimg.com/amplify_video/2109350581123125248/vid/avc1/480x852/47lTmE-8904XTFnv.mp4"),
    ("x-virat-mumbai", "indian_actor_reels", "Virat walks into Mumbai", "https://video.twimg.com/amplify_video/2112746643015778304/vid/avc1/480x852/G9NkYIKWNf3avnSU.mp4"),
    ("x-allu", "indian_actor_reels", "Allu takes the stage", "https://video.twimg.com/amplify_video/2108890651677151232/vid/avc1/480x852/lKMyE_VzG7YtqVmU.mp4"),
    ("x-hardik", "indian_actor_reels", "Hardik's celebration takes over", "https://video.twimg.com/amplify_video/2110911489867669504/vid/avc1/480x852/MeUuwscm1dCEjM4Y.mp4"),
    ("x-user-2", "romantic_couples", "The chemistry in this scene with Agustin Bernasconi and Wanda Nara is unreal", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-2.mp4", DISHPILOT_ID),
    ("x-user-5", "glamorous_girls", "Kim Ji-won owning the stage with pure elegance and confidence", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-5.mp4", DISHPILOT_ID),
    ("x-user-7", "glamorous_girls", "LISA Tarantino Official Music Video visuals are on another level", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-7.mp4", RASCALS_ID),
    ("x-user-1", "anime_4k", "This animation is insane", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-1.mp4", DISHPILOT_ID),
    ("spiderman-4k-dishpilot", "movie_4k", "Tom Holland's Spider-Man edit hits different", f"{RAW_MEDIA_BASE}spiderman-4k-dishpilot.mp4", DISHPILOT_ID),
    ("x-user-3", "celebrity", "When the beat drops and the whole room loses it", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-3.mp4", DISHPILOT_ID),
    ("x-user-4", "movie_4k", "Still one of the funniest scenes in Hotel Transylvania", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-4.mp4", DISHPILOT_ID),
    ("x-user-6", "celebrity", "Shenzhen at night looks straight out of 2050", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-6.mp4", RASCALS_ID),
    ("x-user-8", "celebrity", "Every fandom when their favorite character gets the spotlight", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-8.mp4", RASCALS_ID),
    ("x-user-9", "celebrity", "Inside Japan luxury seaside inn with its own giant live pool", "https://raw.githubusercontent.com/chatgpt820970/chad-media/main/ig-hd-9.mp4", RASCALS_ID),
    ("flp-concert-vibes", "concert", "Wait for the note", _pages_url("flp-concert-vibes.mp4")),
    ("x-clip-3", "concert", "Wait for the note", _pages_url("x-clip-3.mp4")),
    ("flp-anime-sakuga", "anime_4k", "This animation is insane", _pages_url("flp-anime-sakuga.mp4")),
    ("flp-nyc-doorman", "funny", "NYC doorman plot twist", _pages_url("flp-nyc-doorman.mp4")),
    ("flp-khaby-lifehack", "funny", "He did it the simple way", _pages_url("flp-khaby-lifehack.mp4")),
    ("flp-speedcube-lockin", "funny", "Watch his hands", _pages_url("flp-speedcube-lockin.mp4")),
    ("x-clip-1", "funny", "Watch his hands", _pages_url("x-clip-1.mp4")),
    ("yt-flp-3", "funny", "Watch the solve land", _pages_url("yt-flp-3.mp4")),
    ("x-kinderjoy", "funny", "The toy was inside the whole time", _pages_url("x-kinderjoy-dishpilot.mp4")),
    ("flp-tea-science", "knowledge", "The cup changes color", _pages_url("flp-tea-science.mp4")),
    ("flp-president-speech", "funny", "Wait for the third promise", _pages_url("flp-president-speech.mp4")),
    ("flp-language-gauntlet", "knowledge", "Name every flag", _pages_url("flp-language-gauntlet.mp4")),
    ("x-clip-2", "knowledge", "Name every flag", _pages_url("x-clip-2.mp4")),
    ("tt-7688400123809893662", "funny", "The glasses stay on", _shorts_url("tt-7688400123809893662")),
    ("tt-7501669584592817451", "funny", "Do I have your vote", _shorts_url("tt-7501669584592817451")),
    ("tt-7482079182034341162", "funny", "Flying got interesting", _shorts_url("tt-7482079182034341162")),
    ("tt-7438260086109261086", "funny", "Bring back the tomatoes", _shorts_url("tt-7438260086109261086")),
    ("tt-7691740195418377502", "funny", "Just five more minutes", _shorts_url("tt-7691740195418377502")),
    ("tt-7625798204092632333", "funny", "Beatbox challenge", _shorts_url("tt-7625798204092632333")),
    ("tt-7699120594402479373", "celebrity", "The dog knows the doorman", _shorts_url("tt-7699120594402479373")),
    ("tt-7694335322922044703", "funny", "Was the ball out", _shorts_url("tt-7694335322922044703")),
    ("tt-7564513815384116498", "celebrity", "This one came from the heart", _shorts_url("tt-7564513815384116498")),
    ("tt-7548779492546825473", "celebrity", "For everyone who has loved", _shorts_url("tt-7548779492546825473")),
    ("x-2105358655927517442", "anime_4k", "Show me a better fight scene", _shorts_url("x-2105358655927517442")),
    ("tt-7408963192170335534", "knowledge", "The kitchen reveal", _shorts_url("tt-7408963192170335534")),
)


def _catalog_clip(row: tuple) -> dict:
    clip_id, category, hook, video_url, *locked = row
    clip = {"id": clip_id, "category": category, "hook": hook, "video_url": video_url}
    if locked:
        clip["campaign_lock"] = locked[0]
    assert_community_guidelines_safe(
        title=hook,
        hook=hook,
        tags=(category,),
        source_metadata={"source_url": video_url, "category": category},
        context=f"catalog clip {clip_id}",
    )
    return clip


DAILY_CLIP_CATALOG = tuple(_catalog_clip(row) for row in _DAILY_CLIP_ROWS)


def daily_due_at_utc(run_date: date, time_ist: str) -> str:
    """UTC timestamp on `run_date` for an IST clock time spaced across that day."""
    hour_text, minute_text = time_ist.split(":")
    ist_minutes = int(hour_text) * 60 + int(minute_text)
    if ist_minutes < 8 * 60:
        ist_minutes += 24 * 60
    utc_minutes = ist_minutes - _IST_OFFSET_MINUTES
    if utc_minutes < 0 or utc_minutes >= 24 * 60:
        raise CampaignPlatformError(
            f"{time_ist} IST does not fall on {run_date.isoformat()} UTC"
        )
    hour_utc, minute_utc = divmod(utc_minutes, 60)
    return f"{run_date.isoformat()}T{hour_utc:02d}:{minute_utc:02d}:00Z"


def daily_channel_id(campaigns_data: dict, platform: str) -> str:
    channel_key = {
        "x": "x",
        "youtube_shorts": "youtube",
        "instagram_reels": "instagram",
        "tiktok": "tiktok",
    }[platform]
    channel = (campaigns_data.get("channels") or {}).get(channel_key) or {}
    found = str(channel.get("buffer_channel_id") or "")
    if re.fullmatch(r"[a-f\d]{24}", found):
        return found
    return DAILY_CHANNEL_IDS[platform]


def daily_feed_is_current(feed: dict, today: date) -> bool:
    """True when today's 10 slots on each of the four platforms are already built.

    Ingested extras do not count toward the 40, and a same-day feed is kept so a
    manual run does not replace it.
    """
    items = feed.get("items")
    if feed.get("date") != today.isoformat() or not isinstance(items, list):
        return False
    counts = {platform: 0 for platform in SUPPORTED_PLATFORMS}
    for item in items:
        if not isinstance(item, dict):
            return False
        try:
            assert_feed_item_campaign_identity(item)
            assert_feed_item_community_guidelines_safe(item)
        except CampaignPlatformError:
            return False
        if item.get("source") != "ingest" and item.get("platform") in counts:
            counts[str(item["platform"])] += 1
    return all(counts[platform] >= DAILY_40_PER_PLATFORM for platform in SUPPORTED_PLATFORMS)


def build_daily_40_schedule(feed: dict, campaigns_data: dict | None = None) -> dict:
    """Group a daily feed into `slots.x|youtube|instagram|tiktok`, 10 each, 40 total.

    Ingested posts are listed separately so `total_posts` stays the 40 scheduled
    clips. Every slot is re-checked for one campaign mark, its channel id, and the
    four-platform Community Guidelines.
    """
    items = [item for item in feed.get("items") or [] if isinstance(item, dict)]
    scheduled = [item for item in items if item.get("source") != "ingest"]
    slots: dict[str, list[dict]] = {key: [] for key in DAILY_SLOT_KEYS.values()}
    for item in scheduled:
        platform = str(item.get("platform"))
        if platform not in DAILY_SLOT_KEYS:
            raise CampaignPlatformError(f"Unknown daily platform {platform!r}")
        mark = str(item.get("watermark_text"))
        if mark not in SINGLE_CAMPAIGN_NAMES:
            raise CampaignPlatformError(
                f"Daily {platform} slot {item.get('slot')} has unknown watermark {mark!r}"
            )
        if item.get("channel_id") != DAILY_CHANNEL_IDS[platform]:
            raise CampaignPlatformError(f"Daily {platform} slot {item.get('slot')} has the wrong channel id")
        assert_feed_item_campaign_identity(item, campaigns_data)
        assert_feed_item_community_guidelines_safe(item)
        slots[DAILY_SLOT_KEYS[platform]].append(item)
    for key, rows in slots.items():
        rows.sort(key=lambda row: int(row.get("slot") or 0))
        if [int(row.get("slot") or 0) for row in rows] != list(range(1, DAILY_40_PER_PLATFORM + 1)):
            raise CampaignPlatformError(f"Daily {key} needs exactly slots 1..{DAILY_40_PER_PLATFORM}")
    total = sum(len(rows) for rows in slots.values())
    if total != DAILY_40_TOTAL_POSTS:
        raise CampaignPlatformError(f"Expected {DAILY_40_TOTAL_POSTS} daily posts, found {total}")
    channels = {
        DAILY_SLOT_KEYS[platform]: daily_channel_id(campaigns_data, platform)
        if campaigns_data
        else DAILY_CHANNEL_IDS[platform]
        for platform in SUPPORTED_PLATFORMS
    }
    return {
        "generated_at": feed.get("generated_at"),
        "date": feed.get("date"),
        "total_posts": total,
        "posts_per_platform": DAILY_40_PER_PLATFORM,
        "channels": channels,
        "slots": slots,
        "ingested": [item for item in items if item.get("source") == "ingest"],
        "count": len(items),
        "items": items,
    }


def _seen_lists(seen: dict | None) -> tuple[list[str], dict[str, str]]:
    prior = seen or {}
    ids = [str(item) for item in prior.get("ids") or [] if str(item)]
    uses = {
        str(clip_id): str(used_on)
        for clip_id, used_on in (prior.get("uses") or {}).items()
        if str(clip_id)
    }
    return ids, uses


def priority_rank(category: str) -> int:
    """0 for the top priority category; len(PRIORITY_CATEGORIES) for everything else."""
    if category in PRIORITY_CATEGORIES:
        return PRIORITY_CATEGORIES.index(category)
    return len(PRIORITY_CATEGORIES)


def _rank_unseen(
    catalog: tuple[dict, ...],
    predicate,
    seen_ids: set[str],
    used_today: set[str],
    uses: dict[str, str],
) -> list[dict]:
    """Fresh clips first, then recycled by oldest use; priority categories lead each group."""
    order = {clip["id"]: index for index, clip in enumerate(catalog)}
    fresh: list[dict] = []
    recycled: list[dict] = []
    for clip in catalog:
        clip_id = str(clip["id"])
        if clip_id in used_today or not predicate(clip):
            continue
        if clip_id in seen_ids:
            recycled.append(clip)
        else:
            fresh.append(clip)
    fresh.sort(key=lambda clip: (priority_rank(str(clip["category"])), order[str(clip["id"])]))
    recycled.sort(
        key=lambda clip: (
            priority_rank(str(clip["category"])),
            uses.get(str(clip["id"]), ""),
            order[str(clip["id"])],
        )
    )
    return fresh + recycled


def _take_clips(
    catalog: tuple[dict, ...],
    predicate,
    count: int,
    seen_ids: set[str],
    used_today: set[str],
    uses: dict[str, str],
) -> list[dict]:
    ranked = _rank_unseen(catalog, predicate, seen_ids, used_today, uses)
    if len(ranked) < count:
        raise CampaignPlatformError(
            f"Need {count} unseen-or-recycled clips, found {len(ranked)}"
        )
    chosen = ranked[:count]
    used_today.update(str(clip["id"]) for clip in chosen)
    return chosen


def _take_preferring_global(
    catalog: tuple[dict, ...],
    count: int,
    seen_ids: set[str],
    used_today: set[str],
    uses: dict[str, str],
    eligible=None,
) -> list[dict]:
    """Fill from non-Indian clips first so Denzen keeps the Indian inventory."""
    allowed = eligible or (lambda _clip: True)
    ranked = _rank_unseen(
        catalog,
        lambda clip: clip["category"] not in INDIAN_CATEGORIES and allowed(clip),
        seen_ids,
        used_today,
        uses,
    )
    chosen = list(ranked[:count])
    if len(chosen) < count:
        held = set(used_today)
        held.update(str(clip["id"]) for clip in chosen)
        chosen.extend(
            _rank_unseen(catalog, allowed, seen_ids, held, uses)[: count - len(chosen)]
        )
    if len(chosen) < count:
        raise CampaignPlatformError(
            f"Need {count} clips for a non-Indian slot, found {len(chosen)}"
        )
    used_today.update(str(clip["id"]) for clip in chosen)
    return chosen


def _dishpilot_compatible(clip: dict) -> bool:
    """X and YouTube Shorts only pay Dishpilot, so a rascals-marked clip cannot go there."""
    return clip.get("campaign_lock") in (None, DISHPILOT_ID)


def _deal_preferring_global(
    catalog: tuple[dict, ...],
    count: int,
    seen_ids: set[str],
    used_today: set[str],
    uses: dict[str, str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Deal ranked clips round-robin to X, Instagram, and TikTok.

    Each lane gets a share of the priority categories. The X lane skips clips with a
    rascals mark burned in, since X only pays Dishpilot.
    """
    pool = _take_preferring_global(catalog, 3 * count, seen_ids, used_today, uses)
    lanes: tuple[list[dict], list[dict], list[dict]] = ([], [], [])
    cursor = 0
    for clip in pool:
        for offset in range(3):
            lane = (cursor + offset) % 3
            if len(lanes[lane]) >= count or (lane == 0 and not _dishpilot_compatible(clip)):
                continue
            lanes[lane].append(clip)
            cursor = (lane + 1) % 3
            break
        else:
            raise CampaignPlatformError(f"No lane can take clip {clip['id']}")
    if any(len(lane) != count for lane in lanes):
        raise CampaignPlatformError("Daily lanes could not be filled with compatible clips")
    return lanes


def _campaign_for_daily_slot(campaigns_data: dict, campaign_id: str, platform: str) -> dict:
    eligible = campaigns_for_platform(campaigns_data, platform)
    chosen = next((campaign for campaign in eligible if campaign.get("id") == campaign_id), None)
    if chosen is None:
        passed = [str(campaign.get("id")) for campaign in eligible]
        raise CampaignPlatformError(
            f"{campaign_id} is not an active Pool-Guard campaign for {platform} "
            f"(eligible: {passed or 'none'})"
        )
    ok, reason = check_campaign_pool_guard(chosen, campaigns_data.get("pool_guard_rules") or {})
    if not ok:
        raise CampaignPlatformError(f"{campaign_id} failed Pool Guard: {reason}")
    return bind_clip_to_campaign(chosen, platform)


def _daily_item(
    *,
    slot: int,
    platform: str,
    clip: dict,
    campaign: dict,
    campaigns_data: dict,
    run_date: date,
    links_blocked: bool,
) -> dict:
    hook = str(clip["hook"])
    time_ist = DAILY_30_TIMES_IST[slot - 1]
    caption = build_platform_caption(hook, campaign, platform, links_blocked=links_blocked)
    if contains_loan_spam(caption) or contains_loan_spam(hook):
        raise CampaignPlatformError(f"{clip['id']} caption contains loan text")
    assert_community_guidelines_safe(
        title=hook,
        hook=hook,
        tags=_HASHTAG_RE.findall(caption),
        source_metadata={
            "category": clip.get("category"),
            "source_url": clip.get("video_url"),
            "source_metadata": clip.get("source_metadata"),
        },
        context=f"{platform} clip {clip['id']}",
    )
    item = {
        "slot": slot,
        "platform": platform,
        "channel_id": daily_channel_id(campaigns_data, platform),
        "scheduled_time_ist": time_ist,
        "due_at_utc": daily_due_at_utc(run_date, time_ist),
        "campaign_id": campaign.get("id"),
        "watermark_text": platform_watermark(platform, campaign.get("id")),
        "watermark_position": dict(DAILY_WATERMARK_POSITION),
        "video_url": clip["video_url"],
        "title": hook,
        "caption": caption,
        "clip_id": clip["id"],
        "category": clip["category"],
        "priority": priority_rank(str(clip["category"])) < len(PRIORITY_CATEGORIES),
        "pre_stamped": bool(clip.get("campaign_lock")),
        "buffer_status": "pending",
    }
    assert_feed_item_campaign_identity(item, campaigns_data)
    assert_feed_item_community_guidelines_safe(item)
    return item


def build_daily_30(
    campaigns_data: dict,
    seen: dict | None = None,
    *,
    today: date | None = None,
    catalog: tuple[dict, ...] | None = None,
) -> tuple[dict, dict]:
    """Pick 10 unseen clips for each of X, YouTube, Instagram, and TikTok (40 a day).

    Every post is stamped Dishpilot or Rascals, never both. X and YouTube Shorts
    are Dishpilot. Instagram alternates Rascals and Dishpilot. TikTok alternates
    Dishpilot and Rascals. A clip with a burned-in mark (`campaign_lock`) always
    gets that campaign. YouTube keeps 8 Indian actor/cinema clips. X, Instagram,
    and TikTok pick first from the priority categories (romantic couples,
    glamorous girls, 4K anime, 4K movie edits), then YouTube's two global slots
    take what is left. A clip already in `seen` is reused only after every
    unseen clip of that kind is gone, and never twice on the same day.
    """
    run_day = today or datetime.now(timezone.utc).date()
    source = catalog if catalog is not None else DAILY_CLIP_CATALOG
    if len(DAILY_30_TIMES_IST) != DAILY_30_PER_PLATFORM:
        raise CampaignPlatformError("Daily times must cover 10 slots")
    seen_ids_list, uses = _seen_lists(seen)
    seen_ids = set(seen_ids_list)
    used_today: set[str] = set()
    indian = _take_clips(
        source,
        lambda clip: clip["category"] in INDIAN_CATEGORIES,
        DAILY_30_INDIAN_SLOTS,
        seen_ids,
        used_today,
        uses,
    )
    x_clips, instagram_clips, tiktok_clips = _deal_preferring_global(
        source,
        DAILY_30_PER_PLATFORM,
        seen_ids,
        used_today,
        uses,
    )
    youtube_global = _take_preferring_global(
        source,
        DAILY_30_PER_PLATFORM - DAILY_30_INDIAN_SLOTS,
        seen_ids,
        used_today,
        uses,
        eligible=_dishpilot_compatible,
    )
    links_blocked = x_links_blocked(campaigns_data)
    items: list[dict] = []
    for slot, clip in enumerate(x_clips, start=1):
        campaign = _campaign_for_daily_slot(campaigns_data, DISHPILOT_ID, "x")
        items.append(
            _daily_item(
                slot=slot,
                platform="x",
                clip=clip,
                campaign=campaign,
                campaigns_data=campaigns_data,
                run_date=run_day,
                links_blocked=links_blocked,
            )
        )
    youtube_clips = indian + youtube_global
    for slot, clip in enumerate(youtube_clips, start=1):
        campaign_id = DISHPILOT_ID
        campaign = _campaign_for_daily_slot(campaigns_data, campaign_id, "youtube_shorts")
        items.append(
            _daily_item(
                slot=slot,
                platform="youtube_shorts",
                clip=clip,
                campaign=campaign,
                campaigns_data=campaigns_data,
                run_date=run_day,
                links_blocked=False,
            )
        )
    for slot, clip in enumerate(instagram_clips, start=1):
        campaign_id = clip.get("campaign_lock") or INSTAGRAM_DAILY_ROTATION[
            (slot - 1) % len(INSTAGRAM_DAILY_ROTATION)
        ]
        campaign = _campaign_for_daily_slot(campaigns_data, campaign_id, "instagram_reels")
        items.append(
            _daily_item(
                slot=slot,
                platform="instagram_reels",
                clip=clip,
                campaign=campaign,
                campaigns_data=campaigns_data,
                run_date=run_day,
                links_blocked=False,
            )
        )
    for slot, clip in enumerate(tiktok_clips, start=1):
        campaign_id = clip.get("campaign_lock") or TIKTOK_DAILY_ROTATION[
            (slot - 1) % len(TIKTOK_DAILY_ROTATION)
        ]
        campaign = _campaign_for_daily_slot(campaigns_data, campaign_id, "tiktok")
        items.append(
            _daily_item(
                slot=slot,
                platform="tiktok",
                clip=clip,
                campaign=campaign,
                campaigns_data=campaigns_data,
                run_date=run_day,
                links_blocked=False,
            )
        )
    expected_count = DAILY_30_PER_PLATFORM * len(SUPPORTED_PLATFORMS)
    if len(items) != expected_count:
        raise CampaignPlatformError(f"Expected {expected_count} daily items, built {len(items)}")
    clip_ids = [str(item["clip_id"]) for item in items]
    if len(set(clip_ids)) != len(clip_ids):
        raise CampaignPlatformError("Daily feed reused a clip inside the same day")
    blob = json.dumps(items)
    if contains_loan_spam(blob) or "FastLoanPilot" in blob:
        raise CampaignPlatformError("Daily feed contains FastLoanPilot or loan text")
    updated_ids = list(seen_ids_list)
    updated_uses = dict(uses)
    day_text = run_day.isoformat()
    for clip_id in clip_ids:
        if clip_id not in updated_uses:
            updated_ids.append(clip_id)
        updated_uses[clip_id] = day_text
    feed = {
        "generated_at": f"{day_text}T00:15:00Z",
        "date": day_text,
        "count": len(items),
        "items": items,
    }
    seen_state = {"ids": updated_ids, "uses": updated_uses}
    return feed, seen_state


def _is_future(due_iso: str, now: datetime | None) -> bool:
    try:
        due = datetime.fromisoformat(due_iso)
    except ValueError:
        return False
    return due > (now or datetime.now(timezone.utc))


def build_daily_40(*args, **kwargs) -> tuple[dict, dict]:
    """Four-platform 40-a-day name for `build_daily_30`."""
    return build_daily_30(*args, **kwargs)


def buffer_create_post_body(item: dict, now: datetime | None = None) -> dict:
    """Buffer createPost input for one daily slot. The token is never included.

    A slot whose time already passed (for example an ingest run late in the day)
    goes to the channel queue instead, since Buffer rejects past `dueAt` values.
    """
    assert_feed_item_campaign_identity(item)
    assert_feed_item_community_guidelines_safe(item)
    due = str(item["due_at_utc"])
    if due.endswith("Z"):
        due = due[:-1] + "+00:00"
    body: dict = {
        "channelId": item["channel_id"],
        "schedulingType": "automatic",
        "text": item["caption"],
        "assets": [{"video": {"url": item["video_url"]}}],
    }
    if _is_future(due, now):
        body["mode"] = "customScheduled"
        body["dueAt"] = due
    else:
        body["mode"] = "addToQueue"
    platform = item["platform"]
    if platform == "youtube_shorts":
        body["metadata"] = {
            "youtube": {
                "title": str(item["title"])[:100],
                "categoryId": "24",
                "privacy": "public",
            }
        }
    elif platform == "instagram_reels":
        body["metadata"] = {
            "instagram": {
                "type": "reel",
                "shouldShareToFeed": True,
            }
        }
    elif platform == "tiktok":
        body["metadata"] = {"tiktok": {"title": str(item["title"])[:150]}}
    return body


_X_STATUS_RE = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)",
    re.IGNORECASE,
)
_TIKTOK_VIDEO_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/(\d+)",
    re.IGNORECASE,
)
_TIKTOK_SHORT_RE = re.compile(
    r"https?://(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]+/?",
    re.IGNORECASE,
)
_DIRECT_MP4_RE = re.compile(r"https?://\S+\.mp4(?:\?\S*)?$", re.IGNORECASE)
PLATFORM_ALIASES = {
    "ig": "instagram_reels",
    "instagram": "instagram_reels",
    "reels": "instagram_reels",
    "yt": "youtube_shorts",
    "youtube": "youtube_shorts",
    "shorts": "youtube_shorts",
    "twitter": "x",
    "tt": "tiktok",
}
# Slight crop, speed, and metadata differences so the four uploads do not share a hash.
PLATFORM_ENCODE_TWEAK = {
    "x": {"crop_x": "0", "crop_y": "0", "atempo": "1.010", "comment": "whop-x-chadriyo"},
    "youtube_shorts": {"crop_x": "4", "crop_y": "0", "atempo": "1.015", "comment": "whop-yt-denzen"},
    "instagram_reels": {"crop_x": "0", "crop_y": "4", "atempo": "1.020", "comment": "whop-ig-benreelr"},
    "tiktok": {"crop_x": "2", "crop_y": "2", "atempo": "1.025", "comment": "whop-tt-bi67325"},
}


def parse_platform_list(text: str) -> tuple[str, ...]:
    raw = (text or "all").strip().casefold()
    if raw in ("", "all", "*"):
        return SUPPORTED_PLATFORMS
    chosen: list[str] = []
    for part in re.split(r"[\s,]+", raw):
        if not part:
            continue
        name = PLATFORM_ALIASES.get(part, part)
        if name not in SUPPORTED_PLATFORMS:
            raise CampaignPlatformError(
                f"Unknown platform {part!r}. Expected all or {', '.join(SUPPORTED_PLATFORMS)}."
            )
        if name not in chosen:
            chosen.append(name)
    if not chosen:
        raise CampaignPlatformError("No platforms selected")
    return tuple(chosen)


def platforms_for_campaign(campaigns_data: dict, campaign_id: str, requested: tuple[str, ...]) -> tuple[str, ...]:
    """Platforms in `requested` that this funded campaign actually pays."""
    accepted: list[str] = []
    for platform in requested:
        eligible = {str(campaign.get("id")) for campaign in campaigns_for_platform(campaigns_data, platform)}
        if campaign_id in eligible:
            accepted.append(platform)
    if not accepted:
        raise CampaignPlatformError(
            f"{campaign_id} does not pass Pool Guard on any of {', '.join(requested)}"
        )
    return tuple(accepted)


def parse_source_url(url: str) -> dict:
    text = url.strip()
    status = _X_STATUS_RE.search(text)
    if status:
        return {"kind": "x", "id": status.group(1), "url": text}
    video = _TIKTOK_VIDEO_RE.search(text)
    if video:
        return {"kind": "tiktok", "id": video.group(1), "url": text}
    short = _TIKTOK_SHORT_RE.search(text)
    if short:
        token = short.group(0).rstrip("/").rsplit("/", 1)[-1]
        return {"kind": "tiktok", "id": token, "url": text}
    if _DIRECT_MP4_RE.match(text):
        stem = text.rstrip("/").rsplit("/", 1)[-1].split(".", 1)[0]
        safe = re.sub(r"[^A-Za-z0-9_-]", "", stem)[:40] or "clip"
        return {"kind": "mp4", "id": safe, "url": text}
    raise CampaignPlatformError(
        f"Unsupported link {text!r}. Expected an X status, a TikTok video, or a direct MP4."
    )


def download_attempt_plan(parsed: dict) -> list[dict]:
    """Ordered clean-download attempts. JSON APIs come before yt-dlp."""
    kind = parsed["kind"]
    source = parsed["url"]
    if kind == "mp4":
        return [{"via": "direct", "url": source}]
    if kind == "x":
        status_id = parsed["id"]
        return [
            {"via": "vxtwitter", "url": f"https://api.vxtwitter.com/Twitter/status/{status_id}"},
            {"via": "fixupx", "url": f"https://api.fxtwitter.com/status/{status_id}"},
            {"via": "ytdlp", "url": source},
        ]
    if kind == "tiktok":
        return [
            {"via": "tikwm", "url": "https://tikwm.com/api/?hd=1&url=" + quote(source, safe="")},
            {"via": "ytdlp", "url": source},
        ]
    raise CampaignPlatformError(f"No downloader for {kind}")


_URL_DIMENSIONS_RE = re.compile(r"/(\d{3,4})x(\d{3,4})/")


def _stream_score(media: dict, url: str) -> tuple[int, int, int]:
    """Rank a stream by pixels, then frame rate, then bitrate so 4K and 1080p60 win."""
    width = int(media.get("width") or 0)
    height = int(media.get("height") or 0)
    size = media.get("size") if isinstance(media.get("size"), dict) else {}
    width = width or int(size.get("width") or 0)
    height = height or int(size.get("height") or 0)
    if not (width and height):
        match = _URL_DIMENSIONS_RE.search(url)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
    fps = int(float(media.get("fps") or media.get("frame_rate") or 0))
    bitrate = int(media.get("bitrate") or 0)
    return (width * height, fps, bitrate)


def _highest_mp4(candidates: list[tuple[tuple[int, int, int], str]]) -> str:
    ranked = [(score, url) for score, url in candidates if url.startswith("http") and ".mp4" in url.lower()]
    if not ranked:
        return ""
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _media_entries(payload: dict) -> list[dict]:
    entries = [media for media in payload.get("media_extended") or [] if isinstance(media, dict)]
    tweet = payload.get("tweet") if isinstance(payload.get("tweet"), dict) else {}
    media_block = tweet.get("media") if isinstance(tweet.get("media"), dict) else {}
    for video in media_block.get("videos") or []:
        if isinstance(video, dict):
            entries.append(video)
    for variant in payload.get("variants") or []:
        if isinstance(variant, dict):
            entries.append(variant)
    return entries


def extract_mp4(via: str, payload: dict) -> str:
    """Pull the highest-resolution MP4 URL out of a vxtwitter, fixupx, or tikwm payload."""
    if not isinstance(payload, dict):
        return ""
    if via == "tikwm":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        hd = str(data.get("hdplay") or "")
        play = str(data.get("play") or "")
        return _highest_mp4([((2, 0, 0), hd), ((1, 0, 0), play)])
    if via in ("vxtwitter", "fixupx"):
        scored: list[tuple[tuple[int, int, int], str]] = []
        for media in _media_entries(payload):
            url = str(media.get("url") or "")
            if "mp4" not in url.lower():
                continue
            scored.append((_stream_score(media, url), url))
        for url in payload.get("mediaURLs") or payload.get("media_urls") or []:
            text = str(url)
            scored.append((_stream_score({}, text), text))
        return _highest_mp4(scored)
    return ""


_SAFETY_METADATA_KEY_RE = re.compile(
    r"(?:title|desc|caption|tag|label|safety|moderation|sensitive|nsfw|adult|"
    r"explicit|nudity|sexual|violence|hate|restricted|rating)",
    re.IGNORECASE,
)


def source_safety_metadata(payload, depth: int = 0):
    """Keep only API metadata that can describe or safety-label the source."""
    if depth > 5:
        return None
    if isinstance(payload, dict):
        selected = {}
        for key, value in payload.items():
            if _SAFETY_METADATA_KEY_RE.search(str(key)):
                selected[str(key)] = value
            elif isinstance(value, (dict, list)):
                nested = source_safety_metadata(value, depth + 1)
                if nested:
                    selected[str(key)] = nested
        return selected
    if isinstance(payload, list):
        selected = [source_safety_metadata(value, depth + 1) for value in payload]
        return [value for value in selected if value]
    return None


def resolve_clean_mp4_url(url: str, fetch_json, metadata_sink: dict | None = None) -> str | None:
    """Return a clean MP4 URL, or None when the caller should fall back to yt-dlp."""
    parsed = parse_source_url(url)
    if metadata_sink is not None:
        metadata_sink.update({"source_kind": parsed["kind"], "source_id": parsed["id"]})
    for attempt in download_attempt_plan(parsed):
        if attempt["via"] == "direct":
            return attempt["url"]
        if attempt["via"] == "ytdlp":
            return None
        try:
            payload = fetch_json(attempt["url"])
        except Exception:
            continue
        if metadata_sink is not None and isinstance(payload, dict):
            metadata_sink["provider"] = attempt["via"]
            metadata_sink["safety"] = source_safety_metadata(payload)
        found = extract_mp4(attempt["via"], payload if isinstance(payload, dict) else {})
        if found:
            return found
    return None


def single_campaign_drawtext(font_path: str, text: str) -> str:
    """Ingest watermark: one campaign name, top-left, white 0.78 on a thin dark box."""
    if text not in SINGLE_CAMPAIGN_NAMES or "·" in text:
        raise CampaignPlatformError(
            f"Watermark must be one of {sorted(SINGLE_CAMPAIGN_NAMES)}, not {text!r}"
        )
    safe = escape_drawtext(text)
    return (
        f"drawtext=fontfile='{font_path}':text='{safe}':"
        "fontsize=22:fontcolor=white@0.78:"
        "box=1:boxcolor=black@0.28:boxborderw=8:"
        "x=w*0.025:y=h*0.03"
    )


def ingest_ffmpeg_command(
    src: Path,
    dest: Path,
    platform: str,
    watermark: str,
    font_path: str,
) -> list[str]:
    """1080x1920 CRF 16 encode with lanczos scaling and a light sharpen.

    Each platform gets its own crop and speed so the four hashes differ.
    """
    if platform not in PLATFORM_ENCODE_TWEAK:
        raise CampaignPlatformError(f"No encode tweak for {platform}")
    tweak = PLATFORM_ENCODE_TWEAK[platform]
    draw = single_campaign_drawtext(font_path, watermark)
    video_filter = (
        "scale=1088:1928:flags=lanczos:force_original_aspect_ratio=increase,"
        f"crop=1080:1920:{tweak['crop_x']}:{tweak['crop_y']},"
        f"{INGEST_SHARPEN},"
        f"setpts=PTS/{tweak['atempo']},"
        f"{draw}"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        video_filter,
        "-filter:a",
        f"atempo={tweak['atempo']}",
        "-c:v",
        "libx264",
        "-preset",
        ENCODE_PRESET,
        "-crf",
        INGEST_CRF,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-metadata",
        f"comment={tweak['comment']}",
        str(dest),
    ]


def build_ingest_items(
    campaigns_data: dict,
    *,
    source_url: str,
    source_id: str,
    video_url: str,
    campaign_alias: str,
    platforms: str,
    hook: str,
    today: date | None = None,
    slot: int = 1,
    category: str = "glamorous_girls",
    source_metadata: dict | None = None,
) -> list[dict]:
    """Queue one rendered file onto each platform that campaign pays."""
    if category not in INGEST_CATEGORIES:
        raise CampaignPlatformError(
            f"Unknown category {category!r}. Expected one of {', '.join(INGEST_CATEGORIES)}."
        )
    assert_community_guidelines_safe(
        title=hook,
        hook=hook,
        tags=(category,),
        source_metadata={
            "source_url": source_url,
            "category": category,
            "api": source_metadata or {},
        },
        context=f"ingest source {source_id}",
    )
    run_day = today or datetime.now(timezone.utc).date()
    campaign_id = campaign_id_from_alias(campaign_alias)
    selected = platforms_for_campaign(campaigns_data, campaign_id, parse_platform_list(platforms))
    campaign = _campaign_for_daily_slot(campaigns_data, campaign_id, selected[0])
    links_blocked = x_links_blocked(campaigns_data)
    if slot < 1 or slot > DAILY_30_PER_PLATFORM:
        raise CampaignPlatformError(f"Ingest slot must be 1..{DAILY_30_PER_PLATFORM}")
    items: list[dict] = []
    for platform in selected:
        bound = _campaign_for_daily_slot(campaigns_data, campaign_id, platform)
        item = _daily_item(
            slot=slot,
            platform=platform,
            clip={
                "id": f"ingest-{source_id}-{platform}",
                "hook": hook.strip() or "Watch this",
                "category": category,
                "video_url": video_url,
            },
            campaign=bound,
            campaigns_data=campaigns_data,
            run_date=run_day,
            links_blocked=links_blocked and platform == "x",
        )
        mark = str(item["watermark_text"])
        tags = _HASHTAG_RE.findall(str(item["caption"]))
        campaign_tags = [tag for tag in tags if tag in SINGLE_CAMPAIGN_TAGS]
        if mark not in SINGLE_CAMPAIGN_NAMES or len(campaign_tags) != 1:
            raise CampaignPlatformError(f"Ingest item for {platform} does not have one campaign mark")
        if CAMPAIGN_MARK[campaign_id][1] not in campaign_tags:
            raise CampaignPlatformError(f"Ingest caption for {platform} does not match {campaign_id}")
        item["source"] = "ingest"
        item["source_url"] = source_url
        item["source_metadata"] = source_metadata or {}
        assert_feed_item_campaign_identity(item, campaigns_data)
        assert_feed_item_community_guidelines_safe(item)
        items.append(item)
    if campaign.get("id") != campaign_id:
        raise CampaignPlatformError("Ingest campaign did not bind")
    return items


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
