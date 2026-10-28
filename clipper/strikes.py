"""Persistent per-platform pause when Buffer or the network rejects a post.

Safety skips are recorded and the source is not retried, but the platform
stays on. Clearing a pause is a manual edit of state/platform_alerts.json.
"""

from __future__ import annotations

from datetime import datetime, timezone

FAILED_STATUSES = {"error", "failed", "removed", "deleted", "rejected"}
REMOVED_STATUSES = {"removed", "deleted"}


def empty_alerts() -> dict:
    return {"version": 1, "paused": {}, "alerts": [], "blocked_sources": []}


def is_paused(state: dict, platform: str) -> bool:
    return platform in (state.get("paused") or {})


def _stamp(when: datetime | None) -> str:
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def alert_text(platform: str, kind: str, reason: str, buffer_post_id: str = "") -> str:
    return (
        f"{platform} auto-paused ({kind}). {reason}. "
        f"buffer_post_id={buffer_post_id or 'none'}. "
        f"Edit state/platform_alerts.json to clear paused.{platform} after you report it."
    )


def pause_platform(
    state: dict,
    platform: str,
    reason: str,
    kind: str,
    buffer_post_id: str = "",
    when: datetime | None = None,
) -> bool:
    """Pause once. A second failure on an already paused platform does not add another alert."""
    state.setdefault("paused", {})
    state.setdefault("alerts", [])
    state.setdefault("blocked_sources", [])
    if platform in state["paused"]:
        return False
    stamp = _stamp(when)
    state["paused"][platform] = {
        "paused_at": stamp,
        "reason": reason,
        "kind": kind,
        "buffer_post_id": buffer_post_id,
    }
    state["alerts"].append(
        {
            "platform": platform,
            "at": stamp,
            "reason": reason,
            "kind": kind,
            "buffer_post_id": buffer_post_id,
            "source_key": "",
            "identity": "",
        }
    )
    return True


def blocked_identities(state: dict) -> set[str]:
    found: set[str] = set()
    for key in state.get("blocked_sources") or []:
        text = str(key)
        found.add(text)
        if ":" in text:
            found.add(text.split(":", 1)[1])
    return found


def record_cookies_expired(
    state: dict,
    platform: str,
    reason: str,
    when: datetime | None = None,
) -> bool:
    """Note a rejected cookie file once. Posting stays on."""
    state.setdefault("paused", {})
    state.setdefault("alerts", [])
    state.setdefault("blocked_sources", [])
    already = any(
        row.get("kind") == "cookies_expired" and row.get("platform") == platform
        for row in state["alerts"]
    )
    if already:
        return False
    stamp = _stamp(when)
    state["alerts"].append(
        {
            "platform": platform,
            "at": stamp,
            "reason": (reason or "cookie download rejected")[:300],
            "kind": "cookies_expired",
            "buffer_post_id": "",
            "source_key": "",
            "identity": "",
        }
    )
    return True


def record_media_url(
    state: dict,
    platform: str,
    reason: str,
    when: datetime | None = None,
) -> bool:
    """The video URL failed our check or Buffer could not read it. Posting stays on."""
    state.setdefault("paused", {})
    state.setdefault("alerts", [])
    state.setdefault("blocked_sources", [])
    already = any(row.get("kind") == "media_url" and row.get("platform") == platform for row in state["alerts"])
    if already:
        return False
    stamp = _stamp(when)
    state["alerts"].append(
        {
            "platform": platform,
            "at": stamp,
            "reason": (reason or "video URL was not a direct mp4")[:300],
            "kind": "media_url",
            "buffer_post_id": "",
            "source_key": "",
            "identity": "",
        }
    )
    return True


def record_safety(
    state: dict,
    platform: str,
    source_key: str,
    category: str,
    when: datetime | None = None,
) -> bool:
    """Remember a blocked file and alert once. Does not pause the platform."""
    state.setdefault("blocked_sources", [])
    state.setdefault("alerts", [])
    identity = source_key.split(":", 1)[1] if ":" in source_key else source_key
    if source_key and source_key not in state["blocked_sources"]:
        state["blocked_sources"].append(source_key)
    already = any(
        row.get("kind") == "safety" and (row.get("identity") == identity or row.get("source_key") == source_key)
        for row in state["alerts"]
    )
    if already:
        return False
    stamp = _stamp(when)
    reason = f"skipped {source_key}: safety category {category}"
    state["alerts"].append(
        {
            "platform": platform,
            "at": stamp,
            "reason": reason,
            "kind": "safety",
            "buffer_post_id": "",
            "source_key": source_key,
            "identity": identity,
        }
    )
    return True


def record_post_statuses(
    state: dict,
    platform: str,
    posts: list[dict],
    when: datetime | None = None,
) -> list[str]:
    """Pause when Buffer shows a failed or removed post. Returns new alert lines."""
    messages: list[str] = []
    handled_ids = {
        str(row.get("buffer_post_id") or "")
        for row in state.get("alerts") or []
        if isinstance(row, dict) and row.get("resolved") and row.get("buffer_post_id")
    }
    for post in posts or []:
        status = str(post.get("status") or "").strip().lower()
        if status not in FAILED_STATUSES:
            continue
        kind = "removed" if status in REMOVED_STATUSES else "buffer_error"
        post_id = str(post.get("id") or "")
        if post_id and post_id in handled_ids:
            # Already reviewed and resolved; don't re-pause on the same old failed post.
            continue
        reason = f"Buffer reported status {status}"
        if pause_platform(state, platform, reason, kind, buffer_post_id=post_id, when=when):
            messages.append(alert_text(platform, kind, reason, post_id))
    return messages
