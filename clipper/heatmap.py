"""YouTube most-replayed window.

Ported from x-news-collector media_prep.pick_window. Shorts are a few seconds,
so the clip is centered on the heatmap peak instead of a 2:20 window. The
opening 3% is ignored because it is the peak on almost every video.
"""

from __future__ import annotations

import json
import subprocess

from clipper.fetchsrc import redact_log, ytdlp_argv


def pick_heatmap_start(info: dict | None, clip_seconds: float = 12.0) -> tuple[float, str]:
    info = info or {}
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= clip_seconds + 0.5:
        return 0.0, "full"
    points = []
    for row in info.get("heatmap") or []:
        if not isinstance(row, dict):
            continue
        try:
            end = float(row.get("end_time"))
            start = float(row.get("start_time") or 0)
            value = float(row.get("value") or 0)
        except (TypeError, ValueError):
            continue
        points.append((start, end, value))
    if len(points) >= 10:
        body = [row for row in points if row[0] >= duration * 0.03] or points
        peak = max(body, key=lambda row: row[2])
        if peak[2] > 0:
            mid = (peak[0] + peak[1]) / 2
            start = min(max(0.0, mid - clip_seconds / 3), max(0.0, duration - clip_seconds))
            return round(start, 1), f"heatmap peak at {mid:.0f}s"
    return 0.0, "from 0:00"


def section_arg(start: float, clip_seconds: float = 12.0) -> str:
    if start <= 0:
        return f"*0-{clip_seconds:.0f}"
    return f"*{start:.1f}-{start + clip_seconds:.1f}"


def probe_heatmap_section(url: str, cookies: str | None, clip_seconds: float = 12.0) -> str | None:
    """yt-dlp -J for one YouTube URL. None when the heatmap is missing or the probe fails."""
    command = ytdlp_argv() + ["-J", "--skip-download", "--no-warnings", "--no-playlist"]
    if cookies:
        command.extend(["--cookies", cookies])
    command.append(url)
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, RuntimeError):
        return None
    if not result.stdout:
        return None
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(info, dict) and info.get("_type") == "playlist":
        entries = [row for row in (info.get("entries") or []) if isinstance(row, dict)]
        info = entries[0] if entries else {}
    start, why = pick_heatmap_start(info if isinstance(info, dict) else {}, clip_seconds)
    del why
    if start <= 0:
        return None
    redact_log(result.stderr or "")
    return section_arg(start, clip_seconds)
