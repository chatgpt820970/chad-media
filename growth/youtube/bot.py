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
    if config.get("handle") != "Denzen":
        errors.append("handle must be Denzen")
    if not config.get("repost"):
        errors.append("youtube reposts the original short")
    if config.get("zoom"):
        errors.append("youtube does not zoom the repost")
    if float(config["clip_min_seconds"]) < 6 or float(config["clip_max_seconds"]) > 90:
        errors.append("youtube shorts stay inside 6-90 seconds")
    if int(config.get("min_gemini_score") or 0) < 8:
        errors.append("youtube requires a gemini score of at least 8")
    if "Bigg Boss" not in str(config.get("strategy") or ""):
        errors.append("youtube strategy must name the entertainment lanes")
    if "love" not in str(config.get("strategy") or "").lower():
        errors.append("youtube strategy must include the love lane")
    if float((config.get("topic_weights") or {}).get("love") or 0) < 2:
        errors.append("love category needs a high weight")
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
