"""Queue of rendered X clips waiting for BIBI's final caption."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from growth.utils.schedule import _clock_soon, clock_minutes
from growth.utils.ytdlp import Candidate
from growth.x.caption import review_caption


def empty_queue() -> dict:
    return {"version": 1, "items": []}


def load_queue(path: Path) -> dict:
    if not path.is_file():
        return empty_queue()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty_queue()
    items = payload.get("items") if isinstance(payload, dict) else None
    return {"version": 1, "items": [item for item in items or [] if isinstance(item, dict)]}


def save_queue(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def choose_batch(now: datetime, config: dict) -> dict:
    """The prepare window that owns `now`. Before 07:00 IST, that is the previous evening."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo(str(config["timezone"])))
    minutes = local.hour * 60 + local.minute
    batches = list(config.get("batches") or [])
    chosen = batches[-1]
    for batch in batches:
        if clock_minutes(str(batch["at"])) <= minutes:
            chosen = batch
    return chosen


def batch_slots(now: datetime, config: dict) -> list[tuple[str, datetime]]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    batch = choose_batch(now, config)
    found: list[tuple[str, datetime]] = []
    timezone_name = str(config["timezone"])
    for value in batch.get("slots") or []:
        moment = _clock_soon(now, str(value), timezone_name)
        if moment is not None and moment > now:
            found.append((str(value), moment))
    found.sort(key=lambda item: item[1])
    return found


def already_queued(document: dict, due_at: str) -> bool:
    return any(str(item.get("due_at") or "") == due_at for item in document.get("items") or [])


def parse_due(value: str) -> datetime:
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def caption_to_schedule(item: dict, now: datetime, deadline_minutes: int = 30) -> tuple[str, str] | None:
    """Final caption if BIBI set one. Otherwise the Gemini draft once the deadline has arrived."""
    if str(item.get("status") or "") == "scheduled":
        return None
    title = str(item.get("source_title") or "")
    topic = str(item.get("topic") or "news")
    probe = Candidate("pending", title, str(item.get("source_url") or "https://example.com/v"), "news", topic)
    final = str(item.get("caption") or "").strip()
    if final and not review_caption(final, probe):
        return final, "caption"
    due = parse_due(str(item.get("due_at") or ""))
    if now >= due - timedelta(minutes=deadline_minutes):
        draft = str(item.get("draft_caption") or "").strip()
        if draft and not review_caption(draft, probe):
            return draft, "draft"
    return None
