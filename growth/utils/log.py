"""Run log kept next to the dedupe file. Trimmed so the repo does not grow without bound."""

from __future__ import annotations

import json
from pathlib import Path


def append_events(path: Path, events: list[dict], limit: int = 200) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if path.is_file():
        existing = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing.extend(json.dumps(event, sort_keys=True) for event in events)
    kept = existing[-limit:]
    text = "\n".join(kept)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")
