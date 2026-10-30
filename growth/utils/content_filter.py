"""Pure source filters for the repost lanes: labels, promos, devotional, love intent, captions."""

from __future__ import annotations

import hashlib
import re

from growth.utils.ytdlp import Candidate

# Official music labels / film studios. Their uploads carry corner logos and are claimed.
LABELS = (
    "t-series", "tseries", "t series", "zee music", "zeemusic", "z music", "zmusic", "zee studios", "zeestudios",
    "zee5", "saregama", "sony music", "sonymusic", "tips official", "tips music", "tipsofficial", "tips films",
    "speed records", "speedrecords", "yrf", "yash raj", "yashraj", "eros now", "erosnow", "eros music",
    "venus", "shemaroo", "aditya music", "adityamusic", "lahari", "times music", "timesmusic",
    "desi music factory", "white hill", "whitehill", "dharma productions", "red chillies", "ultra bollywood",
    "rajshri", "goldmines", "pen movies", "pen music", "jjust music", "sony pictures", "excel movies",
    "panorama music", "mango music", "think music", "saregama music",
)
LABEL_IDS = (
    "UCq-Fj5jknLsUf-MWSy4_brA",  # T-Series
    "UCFFbwnve3yF62-tVXkTyHqg",  # Zee Music Company
    "UC_A7K2dXFsTMAciGmnNxy-Q",  # Saregama Music
    "UC56gTxNs4f9xZ7Pa2i5xNzg",  # Sony Music India
    "UCJrDMFOdv1I2k8n9oK_V21w",  # Tips Official
    "UCbTLwN10NoCU4WDzLf1JMOA",  # YRF
)
PROMO_TITLE = re.compile(
    r"(?i)\b(official\s+trailer|trailer|teaser|book\s+(your\s+)?tickets?|in\s+cinemas|jukebox|"
    r"full\s+album|lyrical|lyric\s+video|audio\s+song|full\s+song|official\s+video)\b"
)
DEVOTIONAL = re.compile(
    r"(?i)(?<![a-z])("
    r"ram|shri\s*ram|shree\s*ram|jai\s*shri\s*ram|jai\s*shree\s*ram|siya\s*ram|ramayan|ramayana|ramayanam|"
    r"ravan|ravana|raavan|tandav|shiv|shiva|shivaay|shankar|mahadev|bholenath|mahakal|bhole|"
    r"bhajan|bhajans|aarti|arti|krishna|krishn|radhe|radha\s*krishna|govind|kanha|hanuman|bajrang|"
    r"devotional|mantra|mantras|chalisa|bhakti|ganesh|ganpati|durga|navratri|mata\s*rani|jai\s*mata|"
    r"sai\s*baba|om\s*namah|har\s*har|bhagwan|bhagavad|gita|sanatan|kirtan|stotram|namaste\s*sada|"
    r"allah|naat|qawwali|gurbani|waheguru|jesus|worship|prabhu|mangal\s*rang"
    r")(?![a-z])"
)
LOVE = re.compile(
    r"(?i)(?<![a-z])("
    r"love|lovers?|romance|romantic|couple|couples|couplegoals|pyaar|pyar|ishq|mohabbat|prem|"
    r"dil|jaan|jaanu|sanam|humsafar|saathiya|valentine|crush|bae|wife|husband|shaadi|wedding|"
    r"proposal|propose|kiss|hug|date|girlfriend|boyfriend|bf|gf|lovestory|love\s*story|"
    r"lovesong|love\s*song|romanticstatus|bollywoodlove"
    r")(?![a-z])"
)
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3\U00002190-\U000021FF\U00003030\U0000303D\U00003297\U00003299]+"
)


def _who(candidate: Candidate) -> str:
    return " ".join((candidate.uploader or "", candidate.account or "", candidate.url or "")).lower()


def label_reason(candidate: Candidate) -> str | None:
    who = _who(candidate)
    squashed = re.sub(r"[\s_.\-]", "", who)
    for name in LABELS:
        if name in who or name.replace(" ", "").replace("-", "") in squashed:
            return f"official label/studio uploader ({name})"
    for cid in LABEL_IDS:
        if cid.lower() in who:
            return "official label/studio channel id"
    return None


def promo_title_reason(candidate: Candidate) -> str | None:
    hit = PROMO_TITLE.search(candidate.title or "")
    return f"trailer/promo/album title ({hit.group(0).lower()})" if hit else None


def devotional_reason(candidate: Candidate) -> str | None:
    hit = DEVOTIONAL.search(f"{candidate.title or ''}\n{candidate.description or ''}")
    return f"devotional/religious ({hit.group(0).lower()})" if hit else None


def is_love(candidate: Candidate) -> bool:
    if (candidate.topic or "") == "love":
        return True
    return bool(LOVE.search(f"{candidate.title or ''}\n{candidate.description or ''}"))


def source_reasons(candidate: Candidate, require_love: bool = False) -> list[str]:
    reasons = [r for r in (label_reason(candidate), promo_title_reason(candidate), devotional_reason(candidate)) if r]
    if require_love and not is_love(candidate):
        reasons.append("not love/romantic")
    return reasons


def strip_emoji(text: str) -> str:
    lines = [" ".join(EMOJI.sub("", line).split()) for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


LINES = (
    "Some moments just stay with you",
    "This one hits different",
    "Pure feels in a few seconds",
    "Saved this one for a reason",
    "Kuch pal bas yaad reh jaate hain",
    "Simple, soft and real",
    "Watch till the end",
)


def neutral_caption(candidate: Candidate, platform: str) -> str:
    """Short human caption for when Gemini is skipped. Never copies the source title, no emoji."""
    seed = int(hashlib.sha1((candidate.source_id or candidate.url or "").encode()).hexdigest(), 16)
    line = LINES[seed % len(LINES)]
    if is_love(candidate):
        tags = "#love #shorts" if platform == "youtube" else "#love #couplegoals"
    else:
        tags = "#shorts #viral" if platform == "youtube" else "#reels #viral"
    return f"{line}\n{tags}"
