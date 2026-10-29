"""Schedule X clips whose final caption is set.

Thirty minutes before a slot, an entry that still has no final caption is sent
with its Gemini draft so the slot is not missed. This file is the manual step
and the fallback cron.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from growth.utils.buffer_guard import channel_is_ready, post_video, scheduled_count
from growth.utils.schedule import buffer_payload, channel_id
from growth.x.bot import load_config
from growth.x.pending import caption_to_schedule, load_queue, parse_due, save_queue

ROOT = Path(__file__).resolve().parent


def schedule_ready(
    now: datetime,
    *,
    config: dict | None = None,
    queue_path: Path | None = None,
    poster=None,
    channel: str = "",
    queued: int = 0,
    queue_limit: int | None = None,
) -> dict:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    config = config or load_config()
    queue_path = queue_path or (ROOT / "state" / "pending_captions.json")
    document = load_queue(queue_path)
    limit = int(queue_limit if queue_limit is not None else config.get("queue_skip_at") or 9)
    deadline = int(config.get("caption_deadline_minutes") or 30)
    scheduled = 0
    skipped = 0
    for item in document.get("items") or []:
        chosen = caption_to_schedule(item, now, deadline)
        if chosen is None:
            continue
        if queued >= limit:
            item["skip_reason"] = f"{queued} scheduled posts already queued"
            skipped += 1
            continue
        text, source = chosen
        due = parse_due(str(item["due_at"]))
        if due <= now:
            item["skip_reason"] = "slot time has passed"
            skipped += 1
            continue
        payload = buffer_payload("x", text, channel, str(item["video_url"]), due)
        if poster is None:
            item["skip_reason"] = "no Buffer poster"
            skipped += 1
            continue
        post = poster(payload)
        item["status"] = "scheduled"
        item["used_caption"] = source
        item["buffer_post_id"] = str((post or {}).get("id") or "")
        item["scheduled_text"] = text
        queued += 1
        scheduled += 1
    save_queue(queue_path, document)
    return {"scheduled": scheduled, "skipped": skipped, "items": document["items"]}


def main_schedule(now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    config = load_config()
    token = (os.environ.get("BUFFER_API_TOKEN") or "").strip()
    channel = channel_id(config, dict(os.environ))
    if not token or not channel:
        print(json.dumps({"scheduled": 0, "reason": "buffer token or channel missing"}))
        return 0
    ok, reason = channel_is_ready(token, channel)
    if not ok:
        print(json.dumps({"scheduled": 0, "reason": reason}))
        return 0
    queued = scheduled_count(token, str(config.get("buffer_organization_id") or ""), channel)
    result = schedule_ready(
        moment,
        config=config,
        channel=channel,
        queued=queued,
        poster=lambda payload: post_video(token, payload),
    )
    print(json.dumps({"scheduled": result["scheduled"], "skipped": result["skipped"]}, sort_keys=True))
    return 0
