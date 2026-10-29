"""Per-bot seen-id log. Each platform keeps its own state file."""

from __future__ import annotations

import json
import re
from pathlib import Path

EMPTY = {"version": 1, "ids": [], "titles": [], "posts": []}


def title_key(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return text[:140]


def load_state(path: Path) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(EMPTY))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return json.loads(json.dumps(EMPTY))
    state = json.loads(json.dumps(EMPTY))
    if isinstance(payload, dict):
        state["ids"] = [str(item) for item in payload.get("ids") or [] if str(item)]
        state["titles"] = [str(item) for item in payload.get("titles") or [] if str(item)]
        posts = payload.get("posts") or []
        state["posts"] = [item for item in posts if isinstance(item, dict)]
    return state


def seen(state: dict, source_id: str, title: str) -> bool:
    if source_id and source_id in state["ids"]:
        return True
    key = title_key(title)
    return bool(key) and key in state["titles"]


def remember(state: dict, source_id: str, title: str, post: dict | None = None) -> None:
    if source_id and source_id not in state["ids"]:
        state["ids"].append(source_id)
    key = title_key(title)
    if key and key not in state["titles"]:
        state["titles"].append(key)
    if post:
        state["posts"].append(post)
    state["ids"] = state["ids"][-2000:]
    state["titles"] = state["titles"][-2000:]
    state["posts"] = state["posts"][-400:]


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
