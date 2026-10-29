"""Public search for @benreelr. Queries live in this bot's config."""

from __future__ import annotations

from growth.utils.ytdlp import Candidate, collect

LANES = {"indian", "global"}


def discover(config: dict, env: dict | None = None) -> list[Candidate]:
    del env
    return [row for row in collect(list(config.get("sources") or [])) if row.lane in LANES]
