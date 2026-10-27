"""Download public Drive folders, Dropbox shares, and content-plan videos.

Dry-run planning only records commands. `fetch_media` performs the downloads.
Cookie files are paths, never the secret text.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from clipper.engine import build_caption, ytdlp_command
from clipper.loadconfig import read_inbox_lines
from clipper.render import ffmpeg_available, logo_filter, apply_filters

# Drive's virus-scan page prints "(156M)", sometimes "(156MB)".
_SIZE = re.compile(r"\(([0-9.]+)\s*([KMGT])(?:i?B)?\)", re.IGNORECASE)
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
# yt-dlp exits 101 after a successful --max-downloads limit.
_YTDLP_OK = {0, 101}


def dropbox_direct(url: str) -> str:
    """Force a Dropbox share link to download (dl=1)."""
    parts = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "dl"]
    query.append(("dl", "1"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def download_command(source: dict, dest: str) -> list[str]:
    """Public-folder download. Drive uses gdown. Dropbox uses dl=1."""
    kind = source.get("kind") or ""
    url = str(source.get("url") or "")
    if kind == "google_drive_folder":
        return ["gdown", "--folder", "--remaining-ok", "-O", dest, url]
    if kind == "dropbox_folder":
        return [
            "curl",
            "-L",
            "--fail",
            "--retry",
            "3",
            "--retry-all-errors",
            "-A",
            "Mozilla/5.0",
            "-o",
            dest,
            dropbox_direct(url),
        ]
    raise ValueError(f"unsupported source kind {kind!r}")


def drive_bytes_from_headers(content_type: str, content_length: str | None, html: str) -> int | None:
    """Size from a Drive response. HTML warning pages include '(156M)'."""
    if content_type and "text/html" not in content_type.lower() and content_length:
        return int(content_length)
    match = _SIZE.search(html or "")
    if not match:
        return None
    number = float(match.group(1))
    scale = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[match.group(2).upper()]
    return int(number * scale)


def download_saved(returncode: int, saved: bool) -> bool:
    """A clip on disk counts. yt-dlp uses exit 101 when --max-downloads stops it."""
    return bool(saved) and returncode in _YTDLP_OK


def _drive_file_bytes(file_id: str) -> int | None:
    request = urllib.request.Request(f"https://drive.google.com/uc?id={file_id}")
    with urllib.request.urlopen(request, timeout=20) as response:
        content_type = response.headers.get("Content-Type") or ""
        length = response.headers.get("Content-Length")
        html = ""
        if "text/html" in content_type.lower():
            html = response.read(16000).decode("utf-8", "replace")
    return drive_bytes_from_headers(content_type, length, html)


def _run(command: list[str]) -> None:
    result = subprocess.run(command, check=False, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command[:6])}")


def _video_files(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in _VIDEO_SUFFIXES
    ]


def fetch_sui_clip(folder_url: str, output: Path, exclude_ids: set[str] | None = None) -> dict:
    """List the Drive folder with gdown and download the smallest clip not already posted."""
    import gdown

    output.mkdir(parents=True, exist_ok=True)
    listed = gdown.download_folder(url=folder_url, skip_download=True, quiet=True) or []
    if not listed:
        raise RuntimeError("Sui Drive folder returned no files")
    excluded = {str(item) for item in (exclude_ids or set()) if item}
    ranked: list[tuple[int, str, str]] = []
    for item in listed:
        file_id = str(getattr(item, "id", "") or "")
        name = Path(str(getattr(item, "path", "") or file_id)).name
        if not file_id or Path(name).suffix.lower() not in _VIDEO_SUFFIXES:
            continue
        if file_id in excluded:
            continue
        size = _drive_file_bytes(file_id) or 10**12
        ranked.append((size, file_id, name))
    if not ranked:
        return {
            "status": "exhausted",
            "file_id": "",
            "name": "",
            "listed": len(listed),
            "bytes": 0,
            "path": "",
            "excluded": sorted(excluded),
        }
    ranked.sort()
    size, file_id, name = ranked[0]
    source = output / "sui-source" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    gdown.download(id=file_id, output=str(source), quiet=False, retries=2, timeout=(30, 120))
    if not source.is_file() or source.stat().st_size < 100000:
        raise RuntimeError(f"gdown did not save a real clip at {source}")
    return {
        "status": "downloaded",
        "file_id": file_id,
        "name": name,
        "listed": len(listed),
        "bytes": source.stat().st_size,
        "path": str(source),
    }


def fetch_dropbox(url: str, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(download_command({"kind": "dropbox_folder", "url": url}, str(dest)))
    with dest.open("rb") as handle:
        magic = handle.read(2)
    if magic != b"PK":
        raise RuntimeError("Dropbox dl=1 did not return a zip")
    return {"path": str(dest), "bytes": dest.stat().st_size}


def cut_vertical(source: Path, dest: Path, seconds: float = 8.0) -> None:
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed")
    graph, uses_logo = logo_filter({"logo": {"required": False}}, 720, 1280, 0.0)
    if uses_logo or "drawtext" in graph:
        raise RuntimeError("Sui cut must not stamp a logo or text")
    apply_filters(source, dest, graph, None, seconds, 0.0)


def ytdlp_argv() -> list[str]:
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    try:
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed") from exc
    return [sys.executable, "-m", "yt_dlp"]


def youtube_attempts(source: str, output: str, cookies: str | None) -> list[list[str]]:
    """yt-dlp commands for a YouTube watch URL or ytsearch.

    GitHub-hosted runners are datacenter IPs. tv_downgraded and visionos are
    the public clients that still work without a login; web_safari and
    android_vr are the fallbacks. Each attempt is separate so a blocked
    client does not hide a working one.
    """
    attempts = []
    for client in ("tv_downgraded", "visionos", "web_safari", "android_vr"):
        command = [
            "yt-dlp",
            "--extractor-args",
            f"youtube:player_client={client}",
        ]
        if shutil.which("node"):
            command.extend(["--js-runtimes", "node"])
        command.extend(
            [
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
            "2",
            "-o",
            output,
            ]
        )
        if cookies:
            command.extend(["--cookies", cookies])
        command.append(source)
        attempts.append(command)
    return attempts


def _search_command(source: str, output: str, cookies: str | None) -> list[str]:
    command = [
        "yt-dlp",
        "-f",
        "bv*[height<=720]+ba/b[height<=720]/b",
        "--no-playlist",
        "--max-downloads",
        "1",
        "--download-sections",
        "*0-12",
        "--force-keyframes-at-cuts",
        "--merge-output-format",
        "mp4",
        "--retries",
        "2",
        "-o",
        output,
    ]
    if cookies:
        command.extend(["--cookies", cookies])
    command.append(source)
    return command


def _clear_partials(folder: Path) -> None:
    for path in folder.glob("*"):
        if path.is_file():
            path.unlink()


def _needs_cookies(log: str) -> bool:
    text = (log or "").lower()
    return "not a bot" in text or "sign in to confirm" in text


def _run_attempts(commands: list[list[str]], target: Path) -> tuple[int, list[Path], str]:
    argv = ytdlp_argv()
    last = 1
    log = ""
    for command in commands:
        _clear_partials(target)
        if command and command[0] == "yt-dlp":
            command = argv + command[1:]
        try:
            result = subprocess.run(command, check=False, text=True, timeout=240, capture_output=True)
            last = result.returncode
            log = ((result.stderr or "") + (result.stdout or ""))[-1500:]
        except subprocess.TimeoutExpired as exc:
            last = 1
            log = ((exc.stderr or "") if isinstance(exc.stderr, str) else "")[-1500:] or "yt-dlp timed out"
        if log:
            print(log)
        files = [path for path in _video_files(target) if path.stat().st_size > 20000]
        if download_saved(last, bool(files)):
            return last, files, log
    return last, [], log


def fetch_content_plan(bundle: dict, output: Path, env: dict) -> list[dict]:
    """One clip per platform. Cookie-free public sources run first.

    Cookie files stay optional. A public miss is "unavailable", not "failed",
    so a bot-wall or a blocked listing does not fail the Sui download.
    """
    from clipper.publicsrc import public_file_for_platform

    output.mkdir(parents=True, exist_ok=True)
    root = bundle["root"]
    results = []
    for platform, spec in bundle["sources"].get("platforms", {}).items():
        channel = bundle["channels"]["platforms"][platform]
        cookies = str(env.get(channel.get("cookies_env") or "") or "").strip()
        inbox = read_inbox_lines(root / "config" / "inbox" / f"{platform}.txt")
        queries = [str(item.get("q") or "") for item in spec.get("queries") or [] if item.get("q")]
        target = output / platform
        target.mkdir(parents=True, exist_ok=True)
        template = str(target / "%(id)s.mp4")
        public = None
        if inbox:
            label = inbox[0]
            if platform == "youtube" or "youtube.com" in label or "youtu.be" in label:
                commands = youtube_attempts(label, template, cookies or None)
            else:
                commands = [ytdlp_command(label, template, cookies or None)]
        else:
            if platform in {"x", "youtube", "tiktok"}:
                try:
                    public = public_file_for_platform(bundle, platform, target)
                except Exception as exc:
                    public = {"status": "unavailable", "error": f"{exc.__class__.__name__}: {exc}"}
                if public and public.get("status") == "downloaded" and public.get("path"):
                    results.append(
                        {
                            "platform": platform,
                            "status": "downloaded",
                            "source": public.get("url") or public.get("method") or "public",
                            "path": public["path"],
                            "bytes": public.get("bytes") or 0,
                            "via": "public",
                        }
                    )
                    continue
            # A cookie file must not turn TikTok, X, or Instagram into a YouTube
            # search. Those platforms already tried the public sources. YouTube
            # still falls through to yt-dlp, and a miss stays needs_cookies.
            if platform != "youtube":
                results.append(
                    {
                        "platform": platform,
                        "status": "unavailable" if public else "skipped",
                        "reason": (public or {}).get("error") or "no inbox URL and no cookie file",
                    }
                )
                continue
            query = queries[0] if queries else platform
            label = f"ytsearch1:{query}"
            if platform == "youtube":
                commands = youtube_attempts(label, template, cookies or None)
            else:
                commands = [_search_command(label, template, cookies or None)]
        code, files, log = _run_attempts(commands, target)
        if not files:
            blocked = platform == "youtube" and _needs_cookies(log)
            row = {
                "platform": platform,
                "status": "needs_cookies" if blocked else "unavailable",
                "source": label,
                "returncode": code,
                "error": log[-400:],
            }
            if public and public.get("error"):
                row["public_error"] = str(public.get("error"))[:400]
            results.append(row)
            continue
        results.append(
            {
                "platform": platform,
                "status": "downloaded",
                "source": label,
                "path": str(files[0]),
                "bytes": files[0].stat().st_size,
            }
        )
    return results


def fetch_media(
    bundle: dict,
    output: Path,
    env: dict | None = None,
    *,
    include_dropbox: bool = True,
    exclude_sui_ids: set[str] | None = None,
) -> dict:
    """Download Sui, Dhan Aziz, and content-plan media. Does not call Buffer."""
    env = env or {}
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    manifest: dict = {"errors": errors, "ok": False}
    campaigns = {row["id"]: row for row in bundle["campaigns"]["campaigns"]}
    sui = campaigns["sui-basecamp"]
    dhan = campaigns["dhan-aziz"]
    try:
        fetched = fetch_sui_clip(sui["source"]["url"], output, exclude_ids=exclude_sui_ids)
        manifest["sui"] = fetched
        if fetched.get("status") == "exhausted":
            pass
        else:
            short = output / "sui-basecamp-short.mp4"
            cut_vertical(Path(fetched["path"]), short, 8.0)
            limits = (bundle["pipeline"].get("platform_limits") or {}).get("youtube") or {}
            caption = build_caption(
                sui,
                {"source_id": Path(fetched["name"]).stem, "title": fetched["name"]},
                "youtube",
                int(bundle["pipeline"]["x_char_limit"]),
                limits=limits,
            )
            if "x.com/SuiNetwork" not in caption:
                raise RuntimeError("Sui caption is missing x.com/SuiNetwork")
            (output / "caption.txt").write_text(caption + "\n", encoding="utf-8")
            manifest["short"] = str(short)
            manifest["caption"] = caption
    except Exception as exc:
        errors.append(f"sui: {exc}")
    if include_dropbox:
        try:
            manifest["dropbox"] = fetch_dropbox(dhan["source"]["url"], output / "dhan-aziz.zip")
        except Exception as exc:
            errors.append(f"dropbox: {exc}")
    else:
        manifest["dropbox"] = {"status": "skipped", "reason": "not requested"}
    try:
        plan = fetch_content_plan(bundle, output / "content-plan", env)
        manifest["content_plan"] = plan
        youtube = next((row for row in plan if row["platform"] == "youtube"), None)
        youtube_status = (youtube or {}).get("status")
        if youtube_status == "downloaded":
            pass
        elif youtube_status == "needs_cookies":
            pass
        else:
            errors.append("content plan: YouTube yt-dlp did not save a clip")
        for row in plan:
            if row.get("status") == "failed":
                errors.append(f"content plan: {row['platform']} yt-dlp failed")
    except Exception as exc:
        errors.append(f"content plan: {exc}")
    manifest["ok"] = not errors
    return manifest
