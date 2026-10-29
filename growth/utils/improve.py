"""Every three days, nudge source and topic weights toward posts that earned views."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper.buffer_api import BufferError, graphql, view_count
from growth.utils.buffer_guard import API
from growth.utils.gemini import generate

POSTS = """
query GrowthMetrics($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(first: 30, input: { organizationId: $organizationId, filter: { channelIds: [$channelId], status: [sent] } }) {
    edges { node { id text metrics { type value } } }
  }
}
"""


def due(last: str | None, now: datetime, days: int = 3) -> bool:
    if not last:
        return True
    try:
        moment = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return now - moment >= timedelta(days=days)


def metrics_brief(data: dict) -> str:
    edges = ((data or {}).get("posts") or {}).get("edges") or []
    lines = []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        views = view_count(node.get("metrics"))
        if views is None:
            views = 0
        text = " ".join(str(node.get("text") or "").split())[:120]
        if text:
            lines.append(f"views={views} caption={text}")
    return "\n".join(lines[:20])


def parse_weights(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("gemini did not return weight json")
    payload = json.loads(match.group(0))
    cleaned = {}
    for key in ("topic_weights", "account_weights"):
        raw = payload.get(key) or {}
        if not isinstance(raw, dict):
            continue
        cleaned[key] = {}
        for name, value in raw.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            cleaned[key][str(name)] = max(0.3, min(3.0, round(number, 2)))
    return cleaned


def apply_weights(config: dict, weights: dict) -> dict:
    updated = json.loads(json.dumps(config))
    for key in ("topic_weights", "account_weights"):
        current = dict(updated.get(key) or {})
        current.update(weights.get(key) or {})
        updated[key] = current
    return updated


def improve_prompt(strategy: str, brief: str, config: dict) -> str:
    topics = list((config.get("topic_weights") or {}).keys())
    accounts = list((config.get("account_weights") or {}).keys())
    return (
        "You tune a repost bot. Raise weights for topics and accounts whose posts got more views, "
        "and lower weights for ones that got ignored. Stay inside 0.3 to 3.0. "
        "If the metrics do not make a rule clear, decide from the strategy and say nothing outside the JSON. "
        "Use only the exact topic and account keys listed here. "
        f"Strategy: {strategy} topic_weights keys: {topics}. account_weights keys: {accounts}. "
        f"Recent posts:\n{brief}\n"
        "Return only JSON with topic_weights and account_weights."
    )


def _keep_known(weights: dict, config: dict) -> dict:
    topics = set((config.get("topic_weights") or {}).keys())
    accounts = set((config.get("account_weights") or {}).keys())
    kept = {"topic_weights": {}, "account_weights": {}}
    for name, value in (weights.get("topic_weights") or {}).items():
        if not topics or name in topics:
            kept["topic_weights"][name] = value
    for name, value in (weights.get("account_weights") or {}).items():
        if not accounts or name in accounts:
            kept["account_weights"][name] = value
    return kept


def run_improve(
    config_path: Path,
    state_path: Path,
    token: str,
    now: datetime | None = None,
    fetch=None,
    ask=None,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    if not due(state.get("last_improve"), moment):
        return {"updated": False, "reason": "last improve was under 3 days ago"}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not token:
        return {"updated": False, "error": "BUFFER_API_TOKEN is empty"}
    try:
        if fetch is None:
            data = graphql(API, token, POSTS, {
                "organizationId": config.get("buffer_organization_id"),
                "channelId": config.get("buffer_channel_id"),
            })
        else:
            data = fetch(config, token)
    except BufferError as exc:
        return {"updated": False, "error": str(exc)[:240]}
    brief = metrics_brief(data)
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if ask is None and not key:
        return {"updated": False, "error": "GEMINI_API_KEY is empty"}
    if not brief:
        return {"updated": False, "reason": "no sent posts"}
    prompt = improve_prompt(str(config.get("strategy") or ""), brief, config)
    try:
        if ask is not None:
            text = ask(prompt)
        else:
            text = generate(prompt, key, str(config.get("gemini_model") or ""))
        updated = apply_weights(config, _keep_known(parse_weights(text), config))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return {"updated": False, "error": str(exc)[:240]}
    changed = updated.get("topic_weights") != config.get("topic_weights") or updated.get("account_weights") != config.get("account_weights")
    if changed:
        config_path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    state["last_improve"] = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {"updated": changed, "reason": state["last_improve"]}
