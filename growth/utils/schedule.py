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


def slot_plan(config: dict) -> list[dict]:
    rows = config.get("slots") or []
    if rows:
        return [row for row in rows if isinstance(row, dict) and row.get("time")]
    return [{"time": value, "lead": 25, "backup_lead": 15} for value in config.get("slot_times") or []]


def slot_clocks(config: dict) -> list[str]:
    return [str(row["time"]) for row in slot_plan(config)]


def clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def minutes_apart(left: int, right: int) -> int:
    diff = abs(left - right) % 1440
    return min(diff, 1440 - diff)


def ist_utc_cron(hhmm: str, lead_minutes: int) -> str:
    """UTC cron for an Asia/Kolkata clock time, `lead_minutes` earlier. IST is UTC+5:30."""
    total = (clock_minutes(hhmm) - int(lead_minutes)) % 1440
    utc = (total - (5 * 60 + 30)) % 1440
    hour, minute = divmod(utc, 60)
    return f"{minute} {hour} * * *"


def spread_flags(total: int, count: int) -> list[bool]:
    flags = [False] * max(total, 0)
    if total <= 0 or count <= 0:
        return flags
    placed = 0
    acc = 0
    for index in range(total):
        acc += count
        if acc >= total and placed < count:
            flags[index] = True
            acc -= total
            placed += 1
    return flags


def lane_for_index(index: int, targets: dict[str, int]) -> str:
    lanes = list(targets)
    if not lanes:
        return ""
    total = sum(int(value) for value in targets.values())
    flags = spread_flags(total, int(targets[lanes[0]]))
    if len(lanes) == 1 or index < 0 or index >= len(flags) or flags[index]:
        return lanes[0]
    return lanes[1]


def _clock_soon(now: datetime, hhmm: str, timezone_name: str) -> datetime | None:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(timezone_name)
    local = now.astimezone(zone)
    hour, minute = (int(part) for part in hhmm.split(":"))
    found: list[datetime] = []
    for shift in (0, 1):
        day = (local + timedelta(days=shift)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        moment = day.astimezone(timezone.utc)
        if now - timedelta(minutes=2) <= moment <= now + timedelta(hours=30):
            found.append(moment)
    future = [moment for moment in found if moment >= now - timedelta(seconds=30)]
    if not future:
        return None
    return min(future)


def match_run(now: datetime, config: dict, tolerance: int = 4) -> dict | None:
    """Match this run to the primary or backup cron of one slot."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    best: dict | None = None
    timezone_name = str(config["timezone"])
    for row in slot_plan(config):
        for role, key in (("primary", "lead"), ("backup", "backup_lead")):
            lead = int(row[key])
            moment = _clock_soon(now, str(row["time"]), timezone_name)
            if moment is None:
                continue
            actual = (moment - now).total_seconds() / 60
            gap = abs(actual - lead)
            if actual < 0 or gap > tolerance:
                continue
            if best is None or gap < float(best["gap"]):
                best = {"when": moment, "time": str(row["time"]), "role": role, "lead": actual, "gap": gap}
    return best


def next_future_slot(now: datetime, config: dict, minimum_lead: int = 20) -> dict | None:
    """Soonest peak slot at least `minimum_lead` minutes away. Used by a manual run."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    best: dict | None = None
    timezone_name = str(config["timezone"])
    for row in slot_plan(config):
        moment = _clock_soon(now, str(row["time"]), timezone_name)
        if moment is None:
            continue
        actual = (moment - now).total_seconds() / 60
        if actual < minimum_lead:
            continue
        if best is None or moment < best["when"]:
            best = {"when": moment, "time": str(row["time"]), "role": "primary", "lead": actual, "gap": 0.0}
    return best


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
    clocks = slot_clocks(config)
    if len(clocks) < high:
        errors.append("slots has fewer entries than posts_per_day_max")
    if not slot_gaps_ok(clocks, int(config["min_gap_minutes"])):
        errors.append("slot gap is under min_gap_minutes")
    if int(config.get("posts_per_run") or 1) != 1:
        errors.append("each run schedules one post")
    if int(config.get("queue_skip_at") or 9) > 9:
        errors.append("queue_skip_at must stay at or below 9")
    if config.get("caption_mode") == "bibi":
        if [row.get("at") for row in config.get("batches") or []] != ["07:00", "17:30"]:
            errors.append("x prepares batches at 07:00 and 17:30 IST")
        for row in slot_plan(config):
            if "fallback_lead" not in row:
                errors.append(f"{row['time']} needs a fallback lead")
    else:
        for row in slot_plan(config):
            gap = int(row["lead"]) - int(row["backup_lead"])
            if gap < 8 or gap > 12:
                errors.append(f"{row['time']} backup lead is not about 10 minutes after the primary")
    clip = float(config["clip_seconds"])
    if float(config["clip_min_seconds"]) > clip or clip > float(config["clip_max_seconds"]):
        errors.append("clip_seconds is outside clip_min_seconds and clip_max_seconds")
    if not config.get("sources"):
        errors.append("sources is empty")
    return errors
