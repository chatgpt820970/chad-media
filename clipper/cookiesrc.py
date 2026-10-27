"""One cookie-backed download per site, for the CI probe.

Each secret is optional. A missing file is status missing. A rejected
session is cookies_expired and does not pause posting. A saved file counts
only when it is a real video and the corner OCR is clean.
"""

from __future__ import annotations

from pathlib import Path

from clipper.fetchsrc import (
    cookie_file,
    cookies_look_expired,
    expired_reason,
    site_cookie_commands,
    youtube_attempts,
    _run_attempts,
)
from clipper.igsource import fetch_instagram_reel
from clipper.publicsrc import real_video, visible_platform_mark
from clipper.strikes import record_cookies_expired

PROBE_NAMES = ("youtube", "tiktok", "instagram")


def _row(name: str, status: str, bytes_size: int = 0, reason: str = "", path: str = "") -> dict:
    row = {"name": name, "status": status, "bytes": bytes_size, "via": "cookies", "reason": reason}
    if path:
        row["path"] = path
    return row


def _judge(name: str, files: list[Path], log: str, alerts: dict, *, include_handles: bool) -> dict:
    path = files[0] if files else None
    if path and real_video(path):
        mark = visible_platform_mark(path, include_handles=include_handles)
        if mark == "":
            path.unlink(missing_ok=True)
            return _row(name, "unavailable", reason="ocr unavailable")
        if mark:
            path.unlink(missing_ok=True)
            return _row(name, "unavailable", reason="watermark")
        return _row(name, "downloaded", path.stat().st_size, path=str(path))
    if path:
        path.unlink(missing_ok=True)
    if cookies_look_expired(log):
        record_cookies_expired(alerts, name, expired_reason(log))
        return _row(name, "cookies_expired", reason=expired_reason(log))
    return _row(name, "unavailable", reason="no file")


def _first_query(bundle: dict, platform: str, fallback: str) -> str:
    spec = ((bundle.get("sources") or {}).get("platforms") or {}).get(platform) or {}
    for item in spec.get("queries") or []:
        text = str(item.get("q") or "").strip()
        if text:
            return text
    return fallback


def probe_cookie_downloads(bundle: dict, output: Path, env: dict, alerts: dict) -> dict:
    """Probe YouTube, TikTok, and Instagram. Always returns ok true."""
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        _probe_youtube(bundle, output / "youtube", env, alerts),
        _probe_tiktok(bundle, output / "tiktok", env, alerts),
        _probe_instagram(bundle, output / "instagram", env, alerts),
    ]
    return {"ok": True, "sources": rows}


def _probe_youtube(bundle: dict, folder: Path, env: dict, alerts: dict) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cookies = cookie_file(str(env.get("YT_COOKIES_FILE") or ""))
    if not cookies:
        return _row("youtube", "missing", reason="no cookie file")
    query = _first_query(bundle, "youtube", "cricket match highlight India")
    template = str(folder / "%(id)s.mp4")
    commands = youtube_attempts(f"ytsearch1:{query}", template, cookies)[:1]
    _code, files, log = _run_attempts(commands, folder, timeout=90)
    return _judge("youtube", files, log, alerts, include_handles=False)


def _probe_tiktok(bundle: dict, folder: Path, env: dict, alerts: dict) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cookies = cookie_file(str(env.get("TIKTOK_COOKIES_FILE") or ""))
    if not cookies:
        return _row("tiktok", "missing", reason="no cookie file")
    public = ((bundle.get("sources") or {}).get("public_sources") or {}).get("tiktok") or {}
    tags = [str(item) for item in (public.get("tags") or []) if item]
    tag = tags[0] if tags else "anime"
    template = str(folder / "%(id)s.mp4")
    commands = site_cookie_commands("tiktok", tag, template, cookies, [tag])[:1]
    _code, files, log = _run_attempts(commands, folder, timeout=90)
    return _judge("tiktok", files, log, alerts, include_handles=False)


def _probe_instagram(bundle: dict, folder: Path, env: dict, alerts: dict) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cookies = cookie_file(str(env.get("INSTAGRAM_COOKIES_FILE") or ""))
    if not cookies:
        return _row("instagram", "missing", reason="no cookie file")
    # One plan hashtag. x's first tag is animeedit, which matches the 4K anime plan.
    scoped = {
        "sources": {
            "instagram_source": {
                "instagram": {
                    "hashtags": ((bundle.get("sources") or {}).get("instagram_source") or {}).get("x", {}).get("hashtags") or ["animeedit"],
                    "accounts": [],
                }
            }
        }
    }
    row = fetch_instagram_reel(
        scoped,
        "instagram",
        folder,
        cookies,
        set(),
        alerts,
        max_listings=1,
        max_downloads=2,
    )
    status = row.get("status") or "unavailable"
    if status not in {"downloaded", "cookies_expired", "unavailable", "missing"}:
        status = "unavailable"
    return _row(
        "instagram",
        status,
        int(row.get("bytes") or 0),
        reason=str(row.get("reason") or ""),
        path=str(row.get("path") or ""),
    )
