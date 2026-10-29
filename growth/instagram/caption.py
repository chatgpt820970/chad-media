"""Punchy Hinglish Instagram captions. Indian and global lanes use different lines."""

from __future__ import annotations

import hashlib
import re

from growth.utils.ytdlp import Candidate

INDIAN = (
    "Bhai ye dekh ke control nahi hua",
    "Yaar ye scene dimaag se nahi nikalta",
    "Ek dum mast moment, dekh ke ruk jaoge",
)
GLOBAL = (
    "Ye viral clip dekh ke seedha rewind",
    "Duniya is waqt yahi dekh rahi hai",
    "Itna clean clip hai, ek baar aur dekho",
)
NAMES = (
    ("virat kohli", "Virat Kohli"),
    ("kapil sharma", "Kapil Sharma"),
    ("bigg boss", "Bigg Boss"),
    ("rohit sharma", "Rohit Sharma"),
    ("salman khan", "Salman Khan"),
)
TAGS = {
    "indian": ["#Reels", "#India", "#Viral", "#Trending"],
    "bigg-boss": ["#BiggBoss", "#Reels", "#India", "#Viral"],
    "kapil": ["#KapilSharma", "#Comedy", "#Reels", "#India"],
    "virat": ["#ViratKohli", "#Cricket", "#Reels", "#India"],
    "bollywood": ["#Bollywood", "#Reels", "#India", "#Viral"],
    "serial": ["#IndianTV", "#Reels", "#Drama", "#India"],
    "global": ["#Reels", "#Viral", "#Trending", "#Explore"],
    "sports": ["#Sports", "#Reels", "#Viral", "#Trending"],
    "celebrity": ["#Celebrity", "#Reels", "#Viral", "#Trending"],
}
HINGLISH = re.compile(r"(?i)\b(bhai|yaar|ye|dekh|nahi|dum|duniya|clip|scene|moment)\b")


def _pick(key: str, options: tuple[str, ...]) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _name(title: str) -> str:
    lowered = title.lower()
    for needle, name in NAMES:
        if needle in lowered:
            return name
    return ""


def hashtags_for(candidate: Candidate) -> list[str]:
    if candidate.lane == "global":
        tags = list(TAGS.get(candidate.topic) or TAGS["global"])
    else:
        tags = list(TAGS.get(candidate.topic) or TAGS["indian"])
    unique: list[str] = []
    for tag in tags:
        if tag not in unique:
            unique.append(tag)
    return unique[:5]


def caption_for(candidate: Candidate) -> str:
    pool = INDIAN if candidate.lane == "indian" else GLOBAL
    line = _pick(candidate.source_id or candidate.title, pool)
    name = _name(candidate.title)
    if name:
        line = f"{name}: {line}"
    line = " ".join(line.split())[:120]
    return f"{line}\n{' '.join(hashtags_for(candidate))}"


def review_caption(caption: str, candidate: Candidate) -> list[str]:
    del candidate
    reasons: list[str] = []
    lines = [line.strip() for line in caption.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return ["empty caption"]
    if not HINGLISH.search(lines[0]):
        reasons.append("missing hinglish hook")
    count = len(re.findall(r"#\w+", caption))
    if count < 3 or count > 5:
        reasons.append("needs 3-5 hashtags")
    return reasons
