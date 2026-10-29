"""python -m growth.x check|prepare|schedule|fallback|alert

X does not auto-post from `run`. `prepare` renders the next batch.
`schedule` sends entries that already have a final caption.
`fallback` is the same step when a cron is inside the 30-minute deadline.
"""

from __future__ import annotations

import sys

from growth.utils.cli import main as shared_main
from growth.x.bot import HOOKS
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from growth.utils.schedule import clock_minutes
from growth.x.bot import load_config
from growth.x.prepare import main_prepare
from growth.x.schedule_captioned import main_schedule


def tick_action(now: datetime, config: dict) -> str:
    """Prepare near 07:00 and 17:30 IST. Every other cron is the caption fallback."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo(str(config["timezone"])))
    minutes = local.hour * 60 + local.minute
    for batch in config.get("batches") or []:
        mark = clock_minutes(str(batch["at"]))
        if abs(minutes - mark) <= 6:
            return "prepare"
    return "fallback"


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        raise SystemExit(main_prepare())
    if command in ("schedule", "fallback"):
        raise SystemExit(main_schedule())
    if command == "tick":
        action = tick_action(datetime.now(timezone.utc), load_config())
        raise SystemExit(main_prepare() if action == "prepare" else main_schedule())
    if command == "run":
        print("X does not schedule from run. Preparing the caption batch instead.")
        raise SystemExit(main_prepare())
    raise SystemExit(shared_main(HOOKS))
