"""Instagram public reels as a source for X, YouTube, and TikTok.

Posting to Instagram stays off. A Netscape cookie file is optional. Hashtag
pages and profile pages (not /reels/) are listed with yt-dlp, the higher
view counts are preferred, and a reel with a sexual title, a visible
platform mark, or a burned-in @handle is not kept. The same media id is
only assigned to one destination in a fetch.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from clipper.fetchsrc import (
    _clear_partials,
    _video_files,
    cookies_look_expired,
    expired_reason,
    redact_log,
    ytdlp_argv,
)
from clipper.publicsrc import real_video, visible_platform_mark
from clipper.safety import classify
from clipper.strikes import record_cookies_expired

_SHORTCODE = re.compile(r"/(?:p|tv|reels?)/([^/?#&]+)")


def instagram_targets(bundle: dict, platform: str) -> list[str]:
    """Hashtag and profile URLs for one destination platform."""
    spec = ((bundle.get("sources") or {}).get("instagram_source") or {}).get(platform) or {}
    if not isinstance(spec, dict):
        return []
    urls: list[str] = []
    for tag in spec.get("hashtags") or []:
        slug = "".join(ch for ch in str(tag).lower() if ch.isalnum())
        if slug:
            urls.append(f"https://www.instagram.com/explore/tags/{slug}/")
    for account in spec.get("accounts") or []:
        user = str(account).strip().lstrip("@")
        if user and "/" not in user and user not in {"reels", "explore", "p", "stories"}:
            urls.append(f"https://www.instagram.com/{user}/")
    return urls


def reel_shortcode(url: str, media_id: str) -> str:
    match = _SHORTCODE.search(url or "")
    raw = match.group(1) if match else str(media_id or "")
    return "".join(ch for ch in raw if ch.isalnum() or ch in "-_")


def rank_reels(info: dict, seen: set[str]) -> list[dict]:
    """Highest view count first. Sexual titles and already used ids are dropped.

    A missing view count keeps the listing order, so the first item stays
    the top-post proxy.
    """
    rows: list[dict] = []
    for entry in info.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        media_id = str(entry.get("id") or "")
        url = str(entry.get("url") or entry.get("webpage_url") or "")
        title = str(entry.get("title") or entry.get("description") or "")
        shortcode = reel_shortcode(url, media_id)
        if not shortcode and not url:
            continue
        if shortcode in seen or media_id in seen or f"ig-{shortcode}" in seen:
            continue
        if classify(title):
            continue
        if not url and shortcode:
            url = f"https://www.instagram.com/reel/{shortcode}/"
        try:
            views = int(entry.get("view_count") or 0)
        except (TypeError, ValueError):
            views = 0
        try:
            likes = int(entry.get("like_count") or 0)
        except (TypeError, ValueError):
            likes = 0
        rows.append(
            {
                "id": shortcode or media_id,
                "url": url,
                "title": title,
                "view_count": views,
                "like_count": likes,
            }
        )
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda pair: (-pair[1]["view_count"], -pair[1]["like_count"], pair[0]))
    return [row for _, row in indexed]


def _flat_playlist(url: str, cookies: str, timeout: int = 40) -> tuple[dict | None, str]:
    command = ytdlp_argv() + [
        "--flat-playlist",
        "-J",
        "--playlist-end",
        "8",
        "--cookies",
        cookies,
        "--no-warnings",
        url,
    ]
    try:
        result = subprocess.run(command, check=False, text=True, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        return None, "yt-dlp timed out"
    log = redact_log(result.stderr or "")
    if log:
        print(log[:300])
    if cookies_look_expired(result.stderr or ""):
        return None, result.stderr or ""
    try:
        info = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None, log or "playlist json missing"
    if not isinstance(info, dict):
        return None, log or "playlist json missing"
    return info, result.stderr or ""


def _download_reel(url: str, dest: Path, cookies: str, timeout: int = 70) -> tuple[list[Path], str]:
    _clear_partials(dest.parent)
    command = ytdlp_argv() + [
        "-f",
        "b[height<=720]/bv*[height<=720]+ba/b",
        "--no-playlist",
        "--max-downloads",
        "1",
        "--download-sections",
        "*0-12",
        "--force-keyframes-at-cuts",
        "--merge-output-format",
        "mp4",
        "--retries",
        "1",
        "--cookies",
        cookies,
        "-o",
        str(dest),
        url,
    ]
    try:
        result = subprocess.run(command, check=False, text=True, timeout=timeout, capture_output=True)
        log = (result.stderr or "") + "\n" + (result.stdout or "")
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        log = err or "yt-dlp timed out"
    printed = redact_log(log)
    if printed:
        print(printed[:300])
    files = [path for path in _video_files(dest.parent) if path.stat().st_size > 8000]
    return files, log


def _note_expired(alerts: dict | None, log: str) -> None:
    if alerts is None:
        return
    record_cookies_expired(alerts, "instagram", expired_reason(log))


def fetch_instagram_reel(
    bundle: dict,
    platform: str,
    output: Path,
    cookies: str,
    seen: set[str],
    alerts: dict | None = None,
    *,
    max_listings: int = 1,
    max_downloads: int = 2,
) -> dict:
    """Save one clean reel for this destination, or say why none was kept."""
    output.mkdir(parents=True, exist_ok=True)
    targets = instagram_targets(bundle, platform)[:max_listings]
    if not targets:
        return {"platform": platform, "status": "unavailable", "reason": "no instagram targets", "via": "instagram"}
    downloads = 0
    last_reason = "no reel"
    for url in targets:
        info, log = _flat_playlist(url, cookies)
        if cookies_look_expired(log):
            _note_expired(alerts, log)
            return {
                "platform": platform,
                "status": "cookies_expired",
                "reason": expired_reason(log),
                "via": "instagram",
            }
        if not info:
            last_reason = "playlist empty"
            continue
        for reel in rank_reels(info, seen):
            if downloads >= max_downloads:
                break
            downloads += 1
            shortcode = reel["id"]
            dest = output / f"ig-{shortcode}.mp4"
            files, download_log = _download_reel(reel["url"], dest, cookies)
            if cookies_look_expired(download_log) and not files:
                _note_expired(alerts, download_log)
                return {
                    "platform": platform,
                    "status": "cookies_expired",
                    "reason": expired_reason(download_log),
                    "via": "instagram",
                }
            if not files:
                last_reason = "download missed"
                continue
            path = files[0]
            if path.resolve() != dest.resolve():
                dest.unlink(missing_ok=True)
                path.replace(dest)
                path = dest
            if not real_video(path):
                path.unlink(missing_ok=True)
                last_reason = "not a video"
                continue
            mark = visible_platform_mark(path, include_handles=True)
            if mark == "":
                path.unlink(missing_ok=True)
                return {
                    "platform": platform,
                    "status": "unavailable",
                    "reason": "ocr unavailable",
                    "via": "instagram",
                }
            if mark:
                path.unlink(missing_ok=True)
                seen.add(shortcode)
                last_reason = "watermark"
                continue
            seen.add(shortcode)
            seen.add(path.stem)
            return {
                "platform": platform,
                "status": "downloaded",
                "source": reel["url"],
                "path": str(path),
                "bytes": path.stat().st_size,
                "via": "instagram",
            }
        if downloads >= max_downloads:
            break
    return {"platform": platform, "status": "unavailable", "reason": last_reason, "via": "instagram"}
