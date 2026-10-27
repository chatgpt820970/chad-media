"""Manual Content Rewards queue. Nothing here submits to Whop or Content Rewards."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def empty_pending(raw_url: str, minutes: int) -> dict:
    return {
        "version": 1,
        "automated_submit": False,
        "submit_within_minutes": minutes,
        "raw_url": raw_url,
        "how_to_mark_submitted": (
            "Run the Mark submitted workflow with this item's id or post_url, "
            "or set the item status to submitted and commit the file."
        ),
        "items": [],
    }


def empty_views() -> dict:
    return {"version": 1, "items": []}


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_stamp(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def live_item(package: dict, post_url: str, posted_at: datetime, minutes: int, buffer_post_id: str = "") -> dict:
    deadline = posted_at.astimezone(timezone.utc) + timedelta(minutes=minutes)
    return {
        "id": f"{package['campaign_id']}:{package['platform']}:{package['source_id']}",
        "post_url": post_url,
        "platform": package["platform"],
        "campaign_id": package["campaign_id"],
        "content_rewards_campaign_id": package.get("content_rewards_campaign_id") or "",
        "submit_page": package.get("submit_page") or "",
        "posted_at": _stamp(posted_at),
        "deadline_at": _stamp(deadline),
        "status": "pending",
        "buffer_post_id": buffer_post_id,
    }


def add_live_item(pending: dict, item: dict) -> dict:
    items = [row for row in pending.get("items") or [] if row.get("id") != item["id"]]
    items.append(item)
    pending["items"] = items
    return pending


def mark_submitted(pending: dict, submitted: dict, key: str, when: datetime) -> tuple[dict, dict]:
    """Move one pending item into the submitted file. key is an id or a post URL."""
    needle = key.strip()
    kept = []
    moved = None
    for item in pending.get("items") or []:
        if item.get("id") == needle or item.get("post_url") == needle:
            moved = dict(item)
        else:
            kept.append(item)
    if moved is None:
        raise KeyError(f"no pending submission matches {key!r}")
    moved["status"] = "submitted"
    moved["submitted_at"] = _stamp(when)
    pending["items"] = kept
    rows = [row for row in submitted.get("items") or [] if row.get("id") != moved["id"]]
    rows.append(moved)
    submitted["items"] = rows
    submitted["version"] = 1
    return pending, submitted


def sweep_edited(pending: dict, submitted: dict, when: datetime) -> tuple[dict, dict]:
    """An assistant can mark a row by setting status to submitted in pending.json."""
    while True:
        marked = next((item for item in pending.get("items") or [] if item.get("status") == "submitted"), None)
        if marked is None:
            return pending, submitted
        pending, submitted = mark_submitted(pending, submitted, marked["id"], when)


def upsert_views(
    document: dict,
    *,
    item_id: str,
    platform: str,
    campaign_id: str,
    post_url: str,
    views: int,
    checked_at: datetime,
    campaign_min_views: int,
    flag_at: int,
) -> dict:
    rows = [row for row in document.get("items") or [] if row.get("id") != item_id]
    rows.append(
        {
            "id": item_id,
            "platform": platform,
            "campaign_id": campaign_id,
            "post_url": post_url,
            "views": int(views),
            "crossed_1k": int(views) >= flag_at,
            "crossed_campaign_minimum": int(views) >= int(campaign_min_views),
            "checked_at": _stamp(checked_at),
        }
    )
    document["items"] = rows
    document["version"] = 1
    return document


def record_sent_posts(
    pending: dict,
    posts: list[dict],
    platform: str,
    campaigns: list[dict],
    minutes: int,
) -> int:
    """Write a pending row as soon as Buffer reports the post sent.

    The 30-minute clock starts at sentAt. A scheduled post, or one with no
    public URL yet, is not queued. Content-plan posts have no campaign phrase
    and are left out.
    """
    added = 0
    existing_urls = {item.get("post_url") for item in pending.get("items") or []}
    existing_ids = {item.get("buffer_post_id") for item in pending.get("items") or []}
    for post in posts:
        if post.get("status") != "sent":
            continue
        link = (post.get("externalLink") or "").strip()
        sent_at = (post.get("sentAt") or "").strip()
        buffer_id = (post.get("id") or "").strip()
        if not link or not sent_at:
            continue
        if link in existing_urls or (buffer_id and buffer_id in existing_ids):
            continue
        text = post.get("text") or ""
        campaign = None
        for row in campaigns:
            if not row.get("active") or platform not in (row.get("platforms") or []):
                continue
            phrases = (row.get("caption") or {}).get("required_phrases") or []
            if any(phrase and phrase in text for phrase in phrases):
                campaign = row
                break
        if campaign is None:
            continue
        package = {
            "campaign_id": campaign["id"],
            "platform": platform,
            "source_id": buffer_id or "buffer",
            "content_rewards_campaign_id": campaign.get("content_rewards_campaign_id") or "",
            "submit_page": campaign.get("submit_page") or "",
        }
        item = live_item(package, link, parse_stamp(sent_at), minutes, buffer_post_id=buffer_id)
        add_live_item(pending, item)
        existing_urls.add(link)
        if buffer_id:
            existing_ids.add(buffer_id)
        added += 1
    return added


def overdue_items(pending: dict, now: datetime) -> list[dict]:
    late = []
    for item in pending.get("items") or []:
        if item.get("status") != "pending":
            continue
        deadline = item.get("deadline_at")
        if not deadline:
            continue
        if parse_stamp(deadline) < now.astimezone(timezone.utc):
            late.append(item)
    return late


def load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(default))
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
