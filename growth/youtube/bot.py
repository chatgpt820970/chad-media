"""YouTube Shorts growth bot for the Denzen channel."""

from __future__ import annotations

import json
from pathlib import Path

from growth.utils.cli import Hooks
from growth.utils.schedule import common_errors
from growth.youtube.caption import caption_for, review_caption
from growth.youtube.discover import discover

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def validate(config: dict) -> list[str]:
    errors = common_errors(config)
    if config.get("platform") != "youtube":
        errors.append("platform must be youtube")
    if not config.get("enabled"):
        errors.append("youtube bot is expected to be enabled")
    if config.get("handle") != "Denzen":
        errors.append("handle must be Denzen")
    if float(config["clip_min_seconds"]) < 15 or float(config["clip_max_seconds"]) > 30:
        errors.append("youtube clips must stay inside 15-30 seconds")
    if str(config.get("hook_enable")) != "lt(t,1)":
        errors.append("youtube hook must be on screen in the first second")
    if int(config.get("hashtag_min") or 0) != 3 or int(config.get("hashtag_max") or 0) != 5:
        errors.append("youtube captions use 3-5 hashtags")
    if any(row.get("lane") != "indian" for row in config.get("sources") or []):
        errors.append("youtube sources stay on the indian lane")
    return errors


def targets_for(goal: int, config: dict) -> None:
    del goal, config
    return None


HOOKS = Hooks(
    root=ROOT,
    load_config=load_config,
    validate=validate,
    discover=discover,
    targets_for=targets_for,
    caption_for=caption_for,
    review_caption=review_caption,
)
