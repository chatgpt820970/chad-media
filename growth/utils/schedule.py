"""Slot clocks and Buffer createPost payloads.

The GraphQL mutation lives in `clipper.buffer_api`. This module only fills it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from clipper.buffer_api import build_post_input, due_stamp


def slot_minutes(times: list[str]) -> list[int]:
    minutes: list[int] = []
    for value in times:
        hour, minute = (int(part) for part in value.split(":"))
        minutes.append(hour * 60 + minute)
    return sorted(set(minutes))


def slot_gaps_ok(times: list[str], minimum: int) -> bool:
    minutes = slot_minutes(times)
    if len(minutes) < 2:
        return True
    gaps = [minutes[index + 1] - minutes[index] for index in range(len(minutes) - 1)]
    gaps.append(minutes[0] + 1440 - minutes[-1])
    return min(gaps) >= minimum


def upcoming_slots(now: datetime, times: list[str], timezone_name: str, horizon_hours: float = 20) -> list[datetime]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    found: list[datetime] = []
    earliest = now + timedelta(minutes=2)
    latest = now + timedelta(hours=horizon_hours)
    for value in times:
        hour, minute = (int(part) for part in value.split(":"))
        for shift in (0, 1):
            day = (local + timedelta(days=shift)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            moment = day.astimezone(timezone.utc)
            if earliest <= moment <= latest:
                found.append(moment)
    unique = sorted(set(found))
    return unique


def channel_id(config: dict, env: dict[str, str]) -> str:
    override = (env.get(str(config.get("channel_id_env") or "")) or "").strip()
    return override or str(config.get("buffer_channel_id") or "").strip()


def buffer_payload(platform: str, caption: str, channel: str, video_url: str, when: datetime) -> dict:
    package = {
        "platform": platform,
        "caption": caption,
        "status": "scheduled",
        "due_at": due_stamp(when),
    }
    return build_post_input(package, channel, video_url)


def common_errors(config: dict) -> list[str]:
    errors: list[str] = []
    daily = int(config["posts_per_day"])
    low = int(config["posts_per_day_min"])
    high = int(config["posts_per_day_max"])
    cap = int(config["safe_daily_cap"])
    if not low <= daily <= high:
        errors.append("posts_per_day is outside posts_per_day_min and posts_per_day_max")
    if high > cap or daily > cap:
        errors.append("daily count is above safe_daily_cap")
    if int(config["min_resolution"]) < 720:
        errors.append("min_resolution is below 720")
    if not str(config.get("buffer_channel_id") or "").strip():
        errors.append("buffer_channel_id is missing")
    if len(config.get("slot_times") or []) < high:
        errors.append("slot_times has fewer entries than posts_per_day_max")
    if not slot_gaps_ok(list(config.get("slot_times") or []), int(config["min_gap_minutes"])):
        errors.append("slot gap is under min_gap_minutes")
    clip = float(config["clip_seconds"])
    if float(config["clip_min_seconds"]) > clip or clip > float(config["clip_max_seconds"]):
        errors.append("clip_seconds is outside clip_min_seconds and clip_max_seconds")
    if not config.get("sources"):
        errors.append("sources is empty")
    return errors
