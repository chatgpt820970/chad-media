"""yt-dlp search and download commands. Parsing is pure and does not touch the network."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

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
    view_count: int = 0
    like_count: int = 0
    timestamp: float | None = None
    uploader: str = ""
    account: str = ""


def search_target(source: dict) -> str:
    kind = str(source.get("kind") or "")
    count = int(source.get("count") or 8)
    match kind:
        case "ytsearch":
            return f"ytsearch{count}:{source['query']}"
        case "url" | "account":
            return str(source["url"])
        case "hashtag":
            platform = str(source.get("platform") or "")
            tag = str(source.get("tag") or "").lstrip("#")
            if platform == "tiktok":
                return f"https://www.tiktok.com/tag/{tag}"
            if platform == "instagram":
                return f"https://www.instagram.com/explore/tags/{tag}/"
            if platform == "youtube":
                return f"ytsearch{count}:{tag} shorts"
            unknown_platform: str = platform
            raise ValueError(f"unknown hashtag platform {unknown_platform}")
        case other:
            unknown: str = other
            raise ValueError(f"unknown source kind {unknown}")


def sign_in_required(message: str) -> bool:
    text = (message or "").lower()
    return "sign in to confirm" in text or "not a bot" in text or "login required" in text or "cookies" in text and "youtube" in text


def search_command(source: dict, cookies: str | None = None) -> list[str]:
    count = int(source.get("count") or 8)
    target = search_target(source)
    command = [
        "yt-dlp",
        target,
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-end",
        str(count),
    ]
    if "tiktok.com" in target:
        command[1:1] = ["--impersonate", "chrome"]
    if cookies and ("youtube.com" in target or target.startswith("ytsearch")):
        command[1:1] = ["--cookies", cookies]
    return command


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


def _timestamp(entry: dict) -> float | None:
    for key in ("timestamp", "release_timestamp"):
        value = _as_float(entry.get(key))
        if value:
            return value
    uploaded = str(entry.get("upload_date") or "")
    if len(uploaded) == 8 and uploaded.isdigit():
        moment = datetime.strptime(uploaded, "%Y%m%d").replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return None


def engagement(candidate: Candidate, topic_weights: dict | None = None, account_weights: dict | None = None) -> float:
    topic = float((topic_weights or {}).get(candidate.topic) or 1)
    account = 1.0
    haystack = " ".join((candidate.url or "", candidate.uploader or "", candidate.account or ""))
    for key, weight in (account_weights or {}).items():
        if key and key in haystack:
            account = float(weight)
            break
    return (int(candidate.view_count or 0) + int(candidate.like_count or 0) * 3) * topic * account


def rank_recent(candidates: list[Candidate], max_age_hours: float = 48, now: float | None = None, topic_weights: dict | None = None, account_weights: dict | None = None) -> list[Candidate]:
    """Keep clips posted inside the window and put the most watched first."""
    moment = now if now is not None else datetime.now(timezone.utc).timestamp()
    cutoff = moment - float(max_age_hours) * 3600
    fresh = [row for row in candidates if row.timestamp and row.timestamp >= cutoff]
    fresh.sort(key=lambda row: engagement(row, topic_weights, account_weights), reverse=True)
    return fresh


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
                view_count=_as_int(entry.get("view_count")) or 0,
                like_count=_as_int(entry.get("like_count")) or 0,
                timestamp=_timestamp(entry),
                uploader=str(entry.get("uploader") or entry.get("channel") or entry.get("uploader_id") or ""),
                account=str(source.get("url") or source.get("query") or source.get("tag") or ""),
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


def collect(sources: list[dict], timeout: int = 90, cookies: str | None = None) -> list[Candidate]:
    """Search every configured source. A source that fails, or YouTube asking for sign-in, is skipped."""
    found: list[Candidate] = []
    seen: set[str] = set()
    for source in sources:
        try:
            raw = _run(search_command(source, cookies), timeout)
        except Exception as exc:
            if sign_in_required(str(exc)):
                continue
            try:
                raw = _run(search_command(source, cookies), timeout)
            except Exception:
                continue
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
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
    if "tiktok.com" in url:
        command[1:1] = ["--impersonate", "chrome"]
    if cookies:
        command[1:1] = ["--cookies", cookies]
    return command
