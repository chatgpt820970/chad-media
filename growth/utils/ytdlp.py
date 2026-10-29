"""yt-dlp search and download commands. Parsing is pure and does not touch the network."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from growth.utils.retry import MinorFailure, call_with_retry


def safe_id(source_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", source_id or "")[:48]
    return cleaned or "clip"


FORMAT = (
    "bv*[height>=720][format_note!*=watermark][ext=mp4]+ba[ext=m4a]/"
    "b[height>=720][format_note!*=watermark][ext=mp4]/"
    "bv*[height>=720][format_note!*=watermark]+ba/"
    "b[height>=720][format_note!*=watermark]"
)


@dataclass
class Candidate:
    source_id: str
    title: str
    url: str
    lane: str
    topic: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    description: str = ""


def search_target(source: dict) -> str:
    kind = str(source.get("kind") or "")
    match kind:
        case "ytsearch":
            count = int(source.get("count") or 4)
            return f"ytsearch{count}:{source['query']}"
        case "url":
            return str(source["url"])
        case other:
            unknown: str = other
            raise ValueError(f"unknown source kind {unknown}")


def search_command(source: dict) -> list[str]:
    count = int(source.get("count") or 4)
    return [
        "yt-dlp",
        search_target(source),
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-end",
        str(count),
    ]


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    number = _as_float(value)
    if number is None or number <= 0:
        return None
    return int(number)


def parse_entries(payload: dict | None, source: dict) -> list[Candidate]:
    if not payload:
        return []
    entries = payload.get("entries")
    if entries is None and payload.get("id"):
        entries = [payload]
    rows: list[Candidate] = []
    kind = str(source.get("kind") or "")
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("webpage_url") or entry.get("url") or entry.get("original_url") or "").strip()
        if kind == "ytsearch" and source_id and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={source_id}"
        if not source_id or not title or not url.startswith("http"):
            continue
        rows.append(
            Candidate(
                source_id=source_id,
                title=title,
                url=url,
                lane=str(source.get("lane") or ""),
                topic=str(source.get("topic") or ""),
                duration=_as_float(entry.get("duration")),
                width=_as_int(entry.get("width")),
                height=_as_int(entry.get("height")),
                description=str(entry.get("description") or ""),
            )
        )
    return rows


def _run(command: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MinorFailure(str(exc)[:300]) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "yt-dlp failed")[-400:]
        raise MinorFailure(detail)
    return result.stdout


def collect(sources: list[dict], timeout: int = 90) -> list[Candidate]:
    """Search every configured source. A source that fails is skipped."""
    found: list[Candidate] = []
    seen: set[str] = set()
    for source in sources:
        try:
            raw = call_with_retry(lambda source=source: _run(search_command(source), timeout), attempts=2)
            payload = json.loads(raw or "{}")
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for row in parse_entries(payload, source):
            if row.source_id in seen:
                continue
            seen.add(row.source_id)
            found.append(row)
    return found


def download_command(url: str, dest: str, cookies: str | None) -> list[str]:
    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "-f",
        FORMAT,
        "--merge-output-format",
        "mp4",
        "--max-filesize",
        "45M",
        "-o",
        dest,
        url,
    ]
    if cookies:
        command[1:1] = ["--cookies", cookies]
    return command
