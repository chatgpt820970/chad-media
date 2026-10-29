"""Instagram Reels growth bot for @benreelr.

The workflow stays off until the Buffer channel is confirmed. Config `enabled`
is false, so a manual run does not schedule posts.
"""

from __future__ import annotations

import json
from pathlib import Path

from growth.utils.cli import Hooks
from growth.utils.quality import choose_count
from growth.utils.schedule import common_errors
from growth.instagram.caption import caption_for, review_caption
from growth.instagram.discover import discover

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def validate(config: dict) -> list[str]:
    errors = common_errors(config)
    if config.get("platform") != "instagram":
        errors.append("platform must be instagram")
    if bool(config.get("enabled")) != bool(config.get("channel_confirmed")):
        errors.append("enabled and channel_confirmed must change together")
    if config.get("handle") != "@benreelr":
        errors.append("handle must be @benreelr")
    low = float(config["indian_share_min"])
    high = float(config["indian_share_max"])
    share = float(config["indian_share"])
    if not 0.5 <= low <= share <= high <= 0.6:
        errors.append("indian share must stay inside 50-60 percent")
    lanes = {row.get("lane") for row in config.get("sources") or []}
    if "indian" not in lanes or "global" not in lanes:
        errors.append("instagram sources need indian and global lanes")
    return errors


def targets_for(goal: int, config: dict) -> dict[str, int]:
    indian = choose_count(
        goal,
        float(config["indian_share"]),
        float(config["indian_share_min"]),
        float(config["indian_share_max"]),
    )
    return {"indian": indian, "global": goal - indian}


HOOKS = Hooks(
    root=ROOT,
    load_config=load_config,
    validate=validate,
    discover=discover,
    targets_for=targets_for,
    caption_for=caption_for,
    review_caption=review_caption,
)
