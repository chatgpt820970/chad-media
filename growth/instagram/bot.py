"""Instagram Reels growth bot for @benreelr.

Each run still asks Buffer whether the channel exists and skips the slot if it does not.
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
    if not config.get("repost"):
        errors.append("instagram reposts the original reel")
    if config.get("zoom"):
        errors.append("instagram does not zoom the repost")
    if "Bollywood" not in str(config.get("strategy") or "") or "global viral" not in str(config.get("strategy") or ""):
        errors.append("instagram strategy must cover the Indian and global mix")
    if "love" not in str(config.get("strategy") or "").lower():
        errors.append("instagram strategy must include the love lane")
    if float((config.get("topic_weights") or {}).get("love") or 0) < 2:
        errors.append("love category needs a high weight")
    if int(config.get("min_gemini_score") or 0) < 8:
        errors.append("instagram requires a gemini score of at least 8")
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
