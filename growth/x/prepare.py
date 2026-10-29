"""Render the next X batch and leave the clips for BIBI to caption.

This does not call Buffer. The Gemini line is only a draft.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from growth.utils.hosting import PagesPublisher, asset_name, public_page_url
from growth.utils.media import produce
from growth.utils.schedule import due_stamp
from growth.utils.ytdlp import Candidate, collect
from growth.x.bot import load_config
from growth.x.caption import caption_for
from growth.x.pending import already_queued, batch_slots, load_queue, save_queue

ROOT = Path(__file__).resolve().parent


def brief_text(title: str, description: str, transcript: str) -> str:
    chunks = [part.strip() for part in (description, transcript) if part and part.strip()]
    text = " ".join(chunks) if chunks else title
    return " ".join(text.split())[:500]


def describe_source(url: str, title: str, work: Path) -> str:
    """Metadata description plus a short auto-caption transcript, when yt-dlp can read them."""
    work.mkdir(parents=True, exist_ok=True)
    described = subprocess.run(
        ["yt-dlp", "--skip-download", "--no-warnings", "--print", "%(description)s", url],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    description = (described.stdout or "").strip()
    sub = work / "sub"
    subprocess.run(
        ["yt-dlp", "--skip-download", "--no-warnings", "--write-auto-sub", "--sub-lang", "en", "--convert-subs", "vtt", "-o", str(sub), url],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    transcript = ""
    for path in work.glob("sub*.vtt"):
        lines = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("WEBVTT") or "-->" in stripped or stripped.isdigit():
                continue
            lines.append(stripped)
        transcript = " ".join(lines)
        break
    return brief_text(title, description, transcript)


def prepare_batch(
    now: datetime,
    *,
    config: dict | None = None,
    queue_path: Path | None = None,
    discover=None,
    render=None,
    upload=None,
    describe=None,
    draft=None,
    work: Path | None = None,
) -> dict:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    config = config or load_config()
    queue_path = queue_path or (ROOT / "state" / "pending_captions.json")
    document = load_queue(queue_path)
    discover = discover or (lambda: collect(list(config.get("sources") or []), timeout=90))
    render = render or (lambda candidate, folder: produce(candidate, "hook", config, folder, {}))
    describe = describe or (lambda candidate, folder: brief_text(candidate.title, candidate.description, ""))
    draft = draft or caption_for
    work = work or (ROOT.parent.parent / "work" / "growth-x-prepare")
    added = []
    candidates = [row for row in discover() if row.source_id]
    used: set[str] = set()
    for slot_name, moment in batch_slots(now, config):
        due = due_stamp(moment)
        if already_queued(document, due):
            continue
        chosen: Candidate | None = None
        rendered_path = ""
        examined = 0
        for candidate in candidates:
            if candidate.source_id in used or examined >= int(config.get("max_examined") or 6):
                continue
            examined += 1
            try:
                prepared = render(candidate, work / candidate.source_id)
            except Exception:
                continue
            if not getattr(prepared, "local_path", "") or prepared.scanners_unavailable or prepared.corner_hits or prepared.edge_cutoff:
                continue
            if min(int(prepared.width or 0), int(prepared.height or 0)) < int(config["min_resolution"]):
                continue
            chosen = candidate
            rendered_path = str(prepared.local_path)
            break
        if chosen is None or not rendered_path:
            continue
        video_url = rendered_path
        if upload is not None:
            video_url = upload(rendered_path, asset_name(str(config["asset_prefix"]), f"{slot_name.replace(':', '')}-{chosen.source_id}"))
        item = {
            "slot_time": slot_name,
            "due_at": due,
            "video_url": video_url,
            "source_title": chosen.title,
            "source_url": chosen.url,
            "topic": chosen.topic,
            "description": describe(chosen, work / chosen.source_id),
            "draft_caption": draft(chosen),
            "caption": "",
            "status": "pending",
            "buffer_post_id": "",
        }
        document["items"].append(item)
        used.add(chosen.source_id)
        added.append(item)
    save_queue(queue_path, document)
    return {"added": len(added), "items": added}


def main_prepare(now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    config = load_config()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    publisher = PagesPublisher(
        repo,
        os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""),
        str(config.get("release_tag") or "clip-media"),
    )

    def upload(path: str, name: str) -> str:
        publisher.add(path, name)
        return public_page_url(repo, name)

    result = prepare_batch(moment, config=config, upload=upload, describe=describe_source)
    if result["added"]:
        errors = publisher.finalize()
        if errors:
            print(json.dumps({"pages_errors": errors}, sort_keys=True))
    print(json.dumps({"prepared": result["added"]}, sort_keys=True))
    return 0
