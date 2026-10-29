"""X growth bot for @chadriyo. News and video posts, this channel only."""

from __future__ import annotations

import json
from pathlib import Path

from growth.utils.cli import Hooks
from growth.utils.quality import split_share
from growth.utils.schedule import clock_minutes, common_errors, minutes_apart, slot_plan
from growth.x.caption import caption_for, review_caption
from growth.x.discover import discover

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def validate(config: dict) -> list[str]:
    errors = common_errors(config)
    if config.get("platform") != "x":
        errors.append("platform must be x")
    if not config.get("enabled"):
        errors.append("x bot is expected to be enabled")
    if config.get("handle") != "@chadriyo":
        errors.append("handle must be @chadriyo")
    if int(config.get("hashtag_min") or 0) != 1 or int(config.get("hashtag_max") or 0) != 2:
        errors.append("x captions use 1-2 hashtags")
    lanes = {row.get("lane") for row in config.get("sources") or []}
    if "news" not in lanes or "video" not in lanes:
        errors.append("x sources need news and video lanes")
    if not config.get("reject_promo"):
        errors.append("x must reject ads and promo")
    news = (20 * 60 + 47, 22 * 60 + 47, 23 * 60 + 47)
    for row in slot_plan(config):
        post = clock_minutes(str(row["time"]))
        if any(minutes_apart(post, item) < 20 for item in news):
            errors.append(f"{row['time']} is within 20 minutes of the X news routine")
        run = (post - int(row["fallback_lead"])) % 1440
        if any(minutes_apart(run, item) < 20 for item in news):
            errors.append(f"{row['time']} fallback run is within 20 minutes of the X news routine")
    return errors


def targets_for(goal: int, config: dict) -> dict[str, int]:
    news, video = split_share(goal, float(config["news_share"]))
    return {"news": news, "video": video}


HOOKS = Hooks(
    root=ROOT,
    load_config=load_config,
    validate=validate,
    discover=discover,
    targets_for=targets_for,
    caption_for=caption_for,
    review_caption=review_caption,
)
