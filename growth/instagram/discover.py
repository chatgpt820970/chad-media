"""Rank recent viral clips for @benreelr. YouTube sign-in failures are skipped."""

from __future__ import annotations

from growth.utils.ytdlp import Candidate, collect, rank_recent

LANES = {"indian", "global"}


def discover(config: dict, env: dict | None = None) -> list[Candidate]:
    env = env or {}
    cookies = (env.get("YT_COOKIES_FILE") or "").strip() or None
    rows = collect(list(config.get("sources") or []), cookies=cookies)
    ranked = rank_recent(
        rows,
        float(config.get("max_age_hours") or 48),
        topic_weights=config.get("topic_weights"),
        account_weights=config.get("account_weights"),
        keep_undated=(env.get("GROWTH_KEEP_UNDATED") or "") == "1",
    )
    return [row for row in ranked if row.lane in LANES]
