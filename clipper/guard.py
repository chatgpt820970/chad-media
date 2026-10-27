"""Permanent blocklist plus a 7-day topic cooldown from posted history.

The phrase list is config/blocklist.json, ported from x-news-collector.
Topics are recorded on the live ledger when a post is sent and dropped after
seven days. A match skips that clip. It does not pause the platform.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'+.-]{2,}")
_GENERIC = {
    "anime",
    "cricket",
    "bollywood",
    "hollywood",
    "romantic",
    "movie",
    "edit",
    "scene",
    "highlight",
    "interview",
    "celebrity",
    "trending",
    "serial",
    "dialogue",
    "video",
    "clip",
    "shorts",
    "today",
    "worth",
    "another",
    "look",
    "part",
    "people",
    "replay",
    "still",
    "making",
    "rounds",
    "clean",
    "cut",
    "scroll",
    "from",
}


def norm_text(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "


def load_blocklist(root: Path) -> dict:
    path = root / "config" / "blocklist.json"
    if not path.is_file():
        return {"phrases": [], "x_ids": [], "window_days": 7}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"phrases": [], "x_ids": [], "window_days": 7}
    data.setdefault("phrases", [])
    data.setdefault("x_ids", [])
    data.setdefault("window_days", 7)
    return data


def topics_from_text(text: str) -> list[str]:
    words = [match.group(0).lower() for match in _WORD.finditer(text or "")]
    kept = [word for word in words if word not in _GENERIC]
    if len(kept) < 2:
        return []
    phrase = " ".join(kept[:3])
    return [phrase]


def recent_topics(ledger: dict, now: datetime, window_days: int) -> list[str]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(days=window_days)
    found: list[str] = []
    for row in ledger.get("topics") or []:
        topic = str(row.get("topic") or "").strip().lower()
        raw = str(row.get("posted_at") or "")
        if not topic or not raw:
            continue
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment >= cutoff and topic not in found:
            found.append(topic)
    return found


def blocked_reason(root: Path, text: str, ledger: dict, now: datetime, x_ids: tuple[str, ...] = ()) -> str | None:
    block = load_blocklist(root)
    ids = {str(item) for item in x_ids} | set(re.findall(r"status/(\d+)", text or ""))
    hit = ids & {str(item) for item in block.get("x_ids") or []}
    if hit:
        return f"blocklisted post id {sorted(hit)[0]}"
    folded = norm_text(text)
    for phrase in block.get("phrases") or []:
        if norm_text(str(phrase)).strip() and norm_text(str(phrase)) in folded:
            return f"blocklisted phrase '{phrase}'"
    window = int(block.get("window_days") or 7)
    for topic in recent_topics(ledger, now, window):
        if norm_text(topic) in folded:
            return f"topic '{topic}' posted in the last {window} days"
    return None


def remember_topics(ledger: dict, text: str, when: datetime) -> None:
    ledger.setdefault("topics", [])
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    existing = {str(row.get("topic") or "") for row in ledger["topics"]}
    for topic in topics_from_text(text):
        if topic not in existing:
            ledger["topics"].append({"topic": topic, "posted_at": stamp})
            existing.add(topic)


def prune_topics(ledger: dict, now: datetime, window_days: int = 7) -> None:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.astimezone(timezone.utc) - timedelta(days=window_days)
    kept = []
    for row in ledger.get("topics") or []:
        raw = str(row.get("posted_at") or "")
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment >= cutoff:
            kept.append(row)
    ledger["topics"] = kept
