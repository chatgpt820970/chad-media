"""Cookie-free public video sources for content-plan slots.

Cookies stay optional. These paths use public pages only:

- X: public search is attempted, then syndication profile timelines.
  A tweet video is saved with yt-dlp or gallery-dl when that works, and
  otherwise from the public video.twimg.com mp4.
- TikTok: public tag pages, then yt-dlp with no cookies. Watermarked
  formats (format_note containing "watermark") are skipped.
- Reddit: public JSON with an explicit User-Agent, then v.redd.it.
- YouTube: Invidious, then Piped, using the instance's local proxy.
  Direct googlevideo URLs are not fetched.

A miss is status "unavailable". Callers must not treat that as a fetch
failure, or a blocked source would fail the Sui download.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from clipper.safety import classify
from clipper.watermark import PLATFORM_MARK, ocr_image, scan_text

USER_AGENT = "chad-media/1.0 (public clip research; +https://github.com/chatgpt820970/chad-media)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
SOURCE_NAMES = ("x_public", "tiktok_public", "reddit_json", "youtube_frontend")
MIN_BYTES = 8000
CLIP_SECONDS = "40"
# Progressive itag 18 is often missing. These are the avc1 video itags.
H264_ITAGS = (136, 135, 134, 133, 160)
TIKTOK_FORMAT = (
    "bv*[format_note!*=watermark][vcodec^=avc1]+ba/"
    "b[format_note!*=watermark]/"
    "bv*[format_note!*=watermark]+ba/"
    "b[format_note!*=watermark]"
)
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_DIM = re.compile(r"/(\d{2,4})x(\d{2,4})(?:/|\.)")
_TIKTOK_URL = re.compile(r"https://www\.tiktok\.com/@[\w.]+/video/\d+")
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
# Public anonymous bearer shipped in X's web client. Not a user cookie.
_X_BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"


def user_agent(bundle: dict) -> str:
    public = (bundle.get("sources") or {}).get("public_sources") or {}
    return str(public.get("user_agent") or USER_AGENT)


def format_is_watermarked(note: str) -> bool:
    return "watermark" in (note or "").lower()


def variant_height(url: str) -> int | None:
    match = _DIM.search(url or "")
    if not match:
        return None
    return int(match.group(2))


def parse_x_timeline(html: str) -> list[dict]:
    """Videos from a syndication timeline. Sexual titles are dropped."""
    match = _NEXT_DATA.search(html or "")
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    entries = (
        ((data.get("props") or {}).get("pageProps") or {}).get("timeline") or {}
    ).get("entries") or []
    videos: list[dict] = []
    for entry in entries:
        content = entry.get("content") or {}
        tweet = content.get("tweet") or (content.get("itemContent") or {}).get("tweet") or {}
        if not tweet:
            continue
        text = str(tweet.get("full_text") or tweet.get("text") or "")
        if classify(text):
            continue
        user = str((tweet.get("user") or {}).get("screen_name") or "")
        tweet_id = str(tweet.get("id_str") or tweet.get("id") or "")
        media = ((tweet.get("extended_entities") or {}).get("media") or [])
        for item in media:
            if item.get("type") != "video":
                continue
            variants = ((item.get("video_info") or {}).get("variants") or [])
            videos.append(
                {
                    "id": tweet_id,
                    "text": text,
                    "user": user,
                    "url": f"https://x.com/{user}/status/{tweet_id}" if user and tweet_id else "",
                    "variants": variants,
                }
            )
    return videos


def pick_x_variant(variants: list[dict], max_height: int = 720) -> dict | None:
    """Highest-bitrate mp4 at or under max_height. A taller file is the fallback."""
    mp4s: list[dict] = []
    for variant in variants or []:
        url = str(variant.get("url") or "")
        ctype = str(variant.get("content_type") or "")
        if "mp4" not in ctype and ".mp4" not in url.split("?", 1)[0]:
            continue
        mp4s.append(
            {
                "url": url,
                "height": variant_height(url),
                "bitrate": int(variant.get("bitrate") or 0),
            }
        )
    if not mp4s:
        return None
    capped = [row for row in mp4s if row["height"] and row["height"] <= max_height]
    if capped:
        capped.sort(key=lambda row: row["bitrate"], reverse=True)
        return capped[0]
    mp4s.sort(key=lambda row: (row["height"] or 10**9, -row["bitrate"]))
    return mp4s[0]


def parse_reddit_listing(payload: dict) -> list[dict]:
    """v.redd.it posts. over_18 and sexual titles are dropped."""
    children = ((payload or {}).get("data") or {}).get("children") or []
    rows: list[dict] = []
    for child in children:
        data = child.get("data") or {}
        if data.get("over_18"):
            continue
        title = str(data.get("title") or "")
        if classify(title):
            continue
        reddit_video = ((data.get("media") or {}).get("reddit_video") or {})
        fallback = str(reddit_video.get("fallback_url") or "")
        url = str(data.get("url") or "")
        permalink = str(data.get("permalink") or "")
        is_video = bool(data.get("is_video")) or "v.redd.it" in url or bool(fallback)
        if not is_video:
            continue
        post_id = str(data.get("id") or "")
        rows.append(
            {
                "id": post_id,
                "title": title,
                "url": url,
                "permalink": permalink,
                "fallback_url": fallback,
                "watch": f"https://www.reddit.com{permalink}" if permalink.startswith("/") else url,
            }
        )
    return rows


def parse_invidious_search(items: list) -> list[dict]:
    """Search hits with a video id. Sexual titles are dropped."""
    rows: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (None, "video"):
            continue
        title = str(item.get("title") or "")
        video_id = str(item.get("videoId") or "")
        if not video_id or classify(title):
            continue
        rows.append({"id": video_id, "title": title})
    return rows


def pick_h264_itag(video: dict) -> int | None:
    formats = (video or {}).get("adaptiveFormats") or []
    found: dict[int, str] = {}
    for fmt in formats:
        try:
            itag = int(fmt.get("itag") or 0)
        except (TypeError, ValueError):
            continue
        found[itag] = str(fmt.get("type") or fmt.get("encoding") or "")
    for itag in H264_ITAGS:
        codec = found.get(itag)
        if codec is None:
            continue
        if not codec or "avc1" in codec or "avc" in codec:
            return itag
    return None


def real_video(path: Path, min_bytes: int = MIN_BYTES) -> bool:
    if not path.is_file() or path.stat().st_size < min_bytes:
        return False
    with path.open("rb") as handle:
        magic = handle.read(16)
    if b"ftyp" not in magic and magic[:4] != b"\x1a\x45\xdf\xa3":
        return False
    if not shutil.which("ffprobe"):
        return True
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return probe.returncode == 0 and "video" in (probe.stdout or "")


def visible_platform_mark(path: Path, *, include_handles: bool = False) -> str | None:
    """OCR the corners. The synthetic ink detector is not used on real frames.

    Returns a platform word when tesseract sees one, an empty string when
    tesseract is missing, and None when the corners are clean. Instagram
    reels also pass include_handles so a burned-in @name is a skip.
    """
    if not shutil.which("tesseract") or not shutil.which("ffmpeg"):
        return ""
    crops = {
        "top-left": "crop=iw*0.28:ih*0.18:0:0",
        "top-right": "crop=iw*0.28:ih*0.18:iw*0.72:0",
        "bottom-left": "crop=iw*0.28:ih*0.18:0:ih*0.82",
        "bottom-right": "crop=iw*0.28:ih*0.18:iw*0.72:ih*0.82",
    }
    folder = path.parent / "_ocr"
    folder.mkdir(parents=True, exist_ok=True)
    marks: list[str] = []
    try:
        for name, crop in crops.items():
            image = folder / f"{name}.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "1",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    crop,
                    str(image),
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
            if not image.is_file():
                continue
            text = ocr_image(image)
            for item in scan_text(text):
                keep = bool(PLATFORM_MARK.search(item)) or (include_handles and item.startswith("@"))
                if keep and item not in marks:
                    marks.append(item)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if marks:
        return ", ".join(marks)
    return None


def http_get(
    url: str,
    headers: dict | None = None,
    timeout: int = 20,
    max_bytes: int = 1_500_000,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(max_bytes)
    except urllib.error.HTTPError as exc:
        body = exc.read(4000) if exc.fp else b""
        return int(exc.code), body
    except Exception:
        return 0, b""


def http_post(url: str, headers: dict, timeout: int = 20) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(8000)
    except urllib.error.HTTPError as exc:
        body = exc.read(2000) if exc.fp else b""
        return int(exc.code), body
    except Exception:
        return 0, b""


def _row(
    name: str,
    status: str,
    *,
    url: str = "",
    path: str = "",
    method: str = "",
    error: str = "",
    attempts: list[str] | None = None,
) -> dict:
    size = 0
    if path and Path(path).is_file():
        size = Path(path).stat().st_size
    return {
        "name": name,
        "status": status,
        "bytes": size,
        "url": url,
        "path": path,
        "method": method,
        "error": error[:500],
        "attempts": (attempts or [])[-24:],
    }


def _finish(name: str, path: Path, url: str, method: str, attempts: list[str]) -> dict | None:
    if not real_video(path):
        attempts.append(f"{method} saved nothing playable")
        if path.is_file():
            path.unlink()
        return None
    mark = visible_platform_mark(path)
    if mark is None:
        attempts.append("corner ocr clean")
    elif mark == "":
        attempts.append("corner ocr unavailable")
    else:
        attempts.append(f"visible watermark {mark}")
        path.unlink()
        return _row(name, "unavailable", url=url, method=method, error=f"visible watermark: {mark}", attempts=attempts)
    return _row(name, "downloaded", url=url, path=str(path), method=method, attempts=attempts)


def _ffmpeg_cut(url: str, dest: Path, header_ua: str) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-user_agent",
        header_ua,
        "-t",
        CLIP_SECONDS,
        "-i",
        url,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-an",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    try:
        subprocess.run(command, check=False, capture_output=True, text=True, timeout=55)
    except subprocess.TimeoutExpired:
        return False
    return real_video(dest)


def _ytdlp(
    url: str,
    folder: Path,
    stem: str,
    extra: list[str],
    timeout: int = 70,
    playlist: bool = False,
) -> Path | None:
    from clipper.fetchsrc import ytdlp_argv

    folder.mkdir(parents=True, exist_ok=True)
    template = str(folder / f"{stem}.%(ext)s")
    scope = ["--playlist-end", "1"] if playlist else ["--no-playlist"]
    command = ytdlp_argv() + scope + [
        "--max-downloads",
        "1",
        "--download-sections",
        "*0-12",
        "--force-keyframes-at-cuts",
        "--merge-output-format",
        "mp4",
        "--retries",
        "1",
        "-o",
        template,
    ]
    command.extend(extra)
    command.append(url)
    try:
        subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, RuntimeError):
        return None
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in _VIDEO_SUFFIXES and path.stem.startswith(stem)
    ]
    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    for path in files:
        if real_video(path):
            return path
    return None


def _impersonate() -> list[str]:
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return []
    return ["--impersonate", "Chrome-133"]


def _gallery_dl(url: str, folder: Path) -> Path | None:
    binary = shutil.which("gallery-dl")
    if not binary:
        return None
    target = folder / "gallery"
    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [binary, "--dest", str(target), "--range", "1-2", url],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return None
    files = [
        path
        for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() in _VIDEO_SUFFIXES
    ]
    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    for path in files:
        if real_video(path):
            return path
    return None


def _save_x_video(
    post: dict,
    folder: Path,
    attempts: list[str],
    ua: str,
    *,
    try_tools: bool,
) -> dict | None:
    chosen = pick_x_variant(post.get("variants") or [])
    tweet = str(post.get("url") or "")
    stem = f"x-{post.get('id') or 'clip'}"
    if try_tools and tweet:
        saved = _ytdlp(tweet, folder, stem, ["-f", "bv*[height<=720]+ba/b[height<=720]/b"], timeout=40)
        attempts.append(f"yt-dlp {tweet} -> {'saved' if saved else 'miss'}")
        if saved:
            return _finish("x_public", saved, tweet, "yt-dlp", attempts)
        saved = _gallery_dl(tweet, folder)
        attempts.append(f"gallery-dl {tweet} -> {'saved' if saved else 'miss'}")
        if saved:
            final = folder / f"{stem}{saved.suffix}"
            if saved.resolve() != final.resolve():
                shutil.copyfile(saved, final)
            return _finish("x_public", final if final.is_file() else saved, tweet, "gallery-dl", attempts)
    if not chosen:
        attempts.append(f"no mp4 variant for {post.get('id')}")
        return None
    dest = folder / f"{stem}.mp4"
    ok = _ffmpeg_cut(chosen["url"], dest, ua)
    attempts.append(f"twimg {chosen.get('height')}p -> {'saved' if ok else 'miss'}")
    if ok:
        return _finish("x_public", dest, chosen["url"], "twimg-mp4", attempts)
    return None


def _x_search(query: str, ua: str, attempts: list[str]) -> list[dict]:
    encoded = quote(query)
    urls = [
        f"https://syndication.twitter.com/srv/timeline-search/search?q={encoded}",
        f"https://syndication.twitter.com/timeline/search?q={encoded}",
    ]
    found: list[dict] = []
    for url in urls:
        code, body = http_get(url, {"User-Agent": ua}, timeout=15)
        attempts.append(f"search {code} {len(body)} {url.split('?', 1)[0]}")
        if code == 200 and body:
            found.extend(parse_x_timeline(body.decode("utf-8", "replace")))
    code, body = http_post(
        "https://api.twitter.com/1.1/guest/activate.json",
        {"Authorization": f"Bearer {_X_BEARER}", "User-Agent": ua},
        timeout=15,
    )
    guest = ""
    if code == 200 and body:
        try:
            guest = str(json.loads(body).get("guest_token") or "")
        except json.JSONDecodeError:
            guest = ""
    attempts.append(f"guest activate {code} token={'yes' if guest else 'no'}")
    if guest:
        search = f"https://api.twitter.com/1.1/search/tweets.json?q={encoded}&result_type=recent&count=5"
        status, payload = http_get(
            search,
            {"Authorization": f"Bearer {_X_BEARER}", "x-guest-token": guest, "User-Agent": ua},
            timeout=15,
        )
        attempts.append(f"guest search {status} {len(payload)} bytes")
    return found


def probe_x(bundle: dict, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cfg = ((bundle.get("sources") or {}).get("public_sources") or {}).get("x") or {}
    ua = user_agent(bundle)
    attempts: list[str] = []
    seen: set[str] = set()
    tool_tries = 0
    for query in list(cfg.get("search_queries") or [])[:2]:
        for post in _x_search(str(query), ua, attempts)[:2]:
            if post.get("id") in seen:
                continue
            seen.add(str(post.get("id")))
            tool_tries += 1
            saved = _save_x_video(post, folder, attempts, ua, try_tools=tool_tries <= 1)
            if saved and saved.get("status") == "downloaded":
                return saved
    for profile in list(cfg.get("profiles") or []):
        url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{quote(str(profile))}"
        code, body = http_get(url, {"User-Agent": ua}, timeout=20, max_bytes=2_000_000)
        if code == 429:
            time.sleep(3)
            code, body = http_get(url, {"User-Agent": BROWSER_UA}, timeout=20, max_bytes=2_000_000)
        attempts.append(f"profile {profile} {code} {len(body)} bytes")
        posts = parse_x_timeline(body.decode("utf-8", "replace")) if code == 200 else []
        for post in posts[:3]:
            if post.get("id") in seen:
                continue
            seen.add(str(post.get("id")))
            tool_tries += 1
            saved = _save_x_video(post, folder, attempts, ua, try_tools=tool_tries <= 1)
            if saved and saved.get("status") == "downloaded":
                return saved
    return _row("x_public", "unavailable", error="; ".join(attempts[-6:]) or "no public X video", attempts=attempts)


def _tiktok_info(url: str) -> dict | None:
    from clipper.fetchsrc import ytdlp_argv

    command = ytdlp_argv() + _impersonate() + ["-J", "--no-download", url]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return None
    if not (result.stdout or "").strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _tiktok_clean(info: dict) -> bool:
    formats = info.get("formats") or []
    videos = [fmt for fmt in formats if str(fmt.get("vcodec") or "none") != "none"]
    if not videos:
        return False
    return any(not format_is_watermarked(str(fmt.get("format_note") or "")) for fmt in videos)


def discover_tiktok_urls(tag: str, attempts: list[str]) -> list[str]:
    page = f"https://www.tiktok.com/tag/{tag}"
    found: list[str] = []
    try:
        from clipper.fetchsrc import ytdlp_argv

        command = ytdlp_argv() + _impersonate() + ["--flat-playlist", "--playlist-end", "3", "-J", page]
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=40)
        attempts.append(f"yt-dlp tag {tag} exit {result.returncode}")
        if (result.stdout or "").strip():
            data = json.loads(result.stdout)
            for entry in data.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                url = str(entry.get("webpage_url") or entry.get("url") or "")
                if "/video/" in url and url not in found:
                    found.append(url)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, RuntimeError) as exc:
        attempts.append(f"yt-dlp tag {tag} {exc.__class__.__name__}")
    if found:
        return found
    try:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(page, impersonate="chrome", timeout=20)
        text = response.text or ""
        attempts.append(f"tag html {tag} {response.status_code} {len(text)} bytes")
        for url in _TIKTOK_URL.findall(text):
            if url not in found:
                found.append(url)
    except Exception as exc:
        attempts.append(f"tag html {tag} {exc.__class__.__name__}")
    attempts.append(f"tag {tag} video urls {len(found)}")
    return found


def probe_tiktok(bundle: dict, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cfg = ((bundle.get("sources") or {}).get("public_sources") or {}).get("tiktok") or {}
    attempts: list[str] = []
    seen: set[str] = set()
    handles: list[str] = []
    for user in cfg.get("users") or []:
        handle = "".join(ch for ch in str(user).strip().lstrip("@") if ch.isalnum() or ch in "._")
        if handle and handle not in handles:
            handles.append(handle)
    for handle in handles[:2]:
        url = f"https://www.tiktok.com/@{handle}"
        if url in seen:
            continue
        seen.add(url)
        saved = _ytdlp(
            url,
            folder,
            f"tiktok-{handle}",
            ["-f", TIKTOK_FORMAT, *_impersonate()],
            timeout=70,
            playlist=True,
        )
        attempts.append(f"yt-dlp {url} -> {'saved' if saved else 'miss'}")
        if not saved:
            continue
        done = _finish("tiktok_public", saved, url, "yt-dlp-no-watermark", attempts)
        if done:
            return done
    tags = [str(tag) for tag in (cfg.get("tags") or [])]
    tags = tags[:1] if handles else tags[:3]
    for tag in tags:
        for url in discover_tiktok_urls(str(tag), attempts):
            if url in seen:
                continue
            seen.add(url)
            info = _tiktok_info(url)
            if info is not None and not _tiktok_clean(info):
                attempts.append(f"watermark-only {url}")
                continue
            video_id = str((info or {}).get("id") or "clip")
            saved = _ytdlp(
                url,
                folder,
                f"tiktok-{video_id}",
                ["-f", TIKTOK_FORMAT, *_impersonate()],
                timeout=70,
            )
            attempts.append(f"yt-dlp {url} -> {'saved' if saved else 'miss'}")
            if not saved:
                continue
            done = _finish("tiktok_public", saved, url, "yt-dlp-no-watermark", attempts)
            if done:
                return done
    return _row(
        "tiktok_public",
        "unavailable",
        error="; ".join(attempts[-6:]) or "no public TikTok video url",
        attempts=attempts,
    )


def _reddit_subs(bundle: dict, platform: str | None) -> list[str]:
    reddit = ((bundle.get("sources") or {}).get("public_sources") or {}).get("reddit") or {}
    if platform:
        return [str(item) for item in reddit.get(platform) or []]
    found: list[str] = []
    for values in reddit.values():
        for item in values or []:
            text = str(item)
            if text not in found:
                found.append(text)
    return found


def _redlib_hosts(attempts: list[str]) -> list[str]:
    code, body = http_get(
        "https://raw.githubusercontent.com/redlib-org/redlib-instances/master/instances.json",
        {"User-Agent": USER_AGENT},
        timeout=15,
        max_bytes=500_000,
    )
    attempts.append(f"redlib instances {code} {len(body)} bytes")
    if code != 200 or not body:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    hosts: list[str] = []
    rows = data.get("instances") if isinstance(data, dict) else data
    for item in rows or []:
        url = ""
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("instance") or "")
        elif isinstance(item, list) and item:
            url = str(item[0])
        url = url.rstrip("/")
        if url.startswith("http") and url not in hosts:
            hosts.append(url)
        if len(hosts) >= 3:
            break
    return hosts


def _listing_urls(sub: str, hosts: list[str]) -> list[str]:
    urls = [
        f"https://www.reddit.com/r/{sub}/hot.json?limit=15&raw_json=1",
        f"https://old.reddit.com/r/{sub}/hot.json?limit=15&raw_json=1",
        f"https://api.reddit.com/r/{sub}/hot?limit=15",
    ]
    for host in hosts:
        urls.append(f"{host}/r/{sub}/hot.json?limit=15")
    return urls


def _reddit_posts(code: int, body: bytes) -> list[dict] | None:
    if code != 200 or not body or body.lstrip()[:1] not in (b"{", b"["):
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload and isinstance(payload[0], dict) else {}
    if not isinstance(payload, dict):
        return None
    return parse_reddit_listing(payload)


def _save_reddit_post(post: dict, folder: Path, ua: str, name: str, attempts: list[str]) -> dict | None:
    watch = post.get("watch") or post.get("url") or ""
    saved = None
    if watch:
        saved = _ytdlp(
            watch,
            folder,
            f"reddit-{post['id']}",
            ["--add-header", f"User-Agent:{ua}", "-f", "bv*[height<=720]+ba/b[height<=720]/b"],
            timeout=70,
        )
        attempts.append(f"yt-dlp {watch} -> {'saved' if saved else 'miss'}")
    if not saved and post.get("fallback_url"):
        dest = folder / f"reddit-{post['id']}.mp4"
        if _ffmpeg_cut(post["fallback_url"], dest, ua):
            saved = dest
            attempts.append("v.redd.it fallback saved")
    if not saved:
        return None
    done = _finish(name, saved, watch or post.get("fallback_url") or "", "reddit-json", attempts)
    if done and done.get("status") == "downloaded":
        return done
    return None


def probe_reddit(bundle: dict, folder: Path, platform: str | None = None, name: str = "reddit_json") -> dict:
    """One v.redd.it clip. www.reddit.com is tried first with the configured User-Agent."""
    folder.mkdir(parents=True, exist_ok=True)
    ua = user_agent(bundle)
    attempts: list[str] = []
    subs = _reddit_subs(bundle, platform)[:6]
    saw_json = False
    for sub in subs:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=15&raw_json=1"
        for headers in (
            {"User-Agent": ua, "Accept": "application/json"},
            {"User-Agent": BROWSER_UA, "Accept": "application/json"},
        ):
            code, body = http_get(url, headers, timeout=12, max_bytes=1_200_000)
            attempts.append(f"{code} {len(body)} {headers['User-Agent'].split('/', 1)[0]} r/{sub}")
            posts = _reddit_posts(code, body)
            if not posts:
                continue
            saw_json = True
            for post in posts[:1]:
                saved = _save_reddit_post(post, folder, ua, name, attempts)
                if saved:
                    return saved
            break
    hosts = _redlib_hosts(attempts)[:2]
    for sub in subs[:2]:
        for url in _listing_urls(sub, hosts)[1:]:
            code, body = http_get(
                url,
                {"User-Agent": ua, "Accept": "application/json"},
                timeout=12,
                max_bytes=1_200_000,
            )
            attempts.append(f"{code} {len(body)} {url.split('?', 1)[0]}")
            posts = _reddit_posts(code, body)
            if not posts:
                continue
            saw_json = True
            saved = _save_reddit_post(posts[0], folder, ua, name, attempts)
            if saved:
                return saved
            break
    if not saw_json:
        attempts.append("reddit json blocked")
    return _row(name, "unavailable", error="; ".join(attempts[-8:]) or "no reddit listing", attempts=attempts)


def _invidious_hosts(attempts: list[str]) -> list[str]:
    code, body = http_get(
        "https://api.invidious.io/instances.json?sort_by=health&type=https",
        {"User-Agent": USER_AGENT},
        timeout=20,
        max_bytes=1_500_000,
    )
    attempts.append(f"invidious instances {code} {len(body)} bytes")
    if code != 200 or not body:
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    hosts: list[str] = []
    for row in data:
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], dict):
            continue
        meta = row[1]
        if meta.get("api") and str(meta.get("type") or "") == "https":
            hosts.append(str(row[0]))
        if len(hosts) >= 4:
            break
    return hosts


def _piped_download(query: str, folder: Path, attempts: list[str], ua: str) -> dict | None:
    bases = ("https://pipedapi.kavin.rocks", "https://pipedapi.adminforge.de")
    for base in bases:
        url = f"{base}/search?q={quote(query)}&filter=videos"
        code, body = http_get(url, {"User-Agent": ua, "Accept": "application/json"}, timeout=15)
        attempts.append(f"piped {code} {len(body)} {base}")
        if code != 200 or not body or body[:1] != b"{":
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        items = payload.get("items") if isinstance(payload, dict) else payload
        parsed = parse_invidious_search(items if isinstance(items, list) else [])
        for item in parsed[:1]:
            video_id = item["id"]
            status, stream_body = http_get(
                f"{base}/streams/{video_id}",
                {"User-Agent": ua, "Accept": "application/json"},
                timeout=15,
            )
            attempts.append(f"piped streams {status} {video_id}")
            if status != 200 or not stream_body:
                continue
            try:
                streams = json.loads(stream_body)
            except json.JSONDecodeError:
                continue
            video_streams = streams.get("videoStreams") or []
            # Prefer a URL the instance proxies. Raw googlevideo is IP-locked.
            chosen = ""
            for stream in video_streams:
                stream_url = str(stream.get("url") or "")
                if not stream_url or "googlevideo.com" in stream_url:
                    continue
                mime = str(stream.get("mimeType") or stream.get("format") or "")
                if "mp4" in mime or "avc1" in mime or not mime:
                    chosen = stream_url
                    break
            if not chosen:
                attempts.append(f"piped {video_id} has no proxied video")
                continue
            dest = folder / f"piped-{video_id}.mp4"
            if _ffmpeg_cut(chosen, dest, ua):
                return _finish("youtube_frontend", dest, f"{base}/watch?v={video_id}", "piped", attempts)
    return None


def probe_youtube(bundle: dict, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    cfg = ((bundle.get("sources") or {}).get("public_sources") or {}).get("youtube_frontends") or {}
    queries = [str(item) for item in cfg.get("queries") or []] or ["cricket match highlight India"]
    ua = user_agent(bundle)
    attempts: list[str] = []
    hosts = _invidious_hosts(attempts)
    cuts = 0
    for host in hosts[:3]:
        for query in queries[:2]:
            url = f"https://{host}/api/v1/search?q={quote(query)}&type=video"
            code, body = http_get(url, {"User-Agent": ua, "Accept": "application/json"}, timeout=20)
            attempts.append(f"search {code} {len(body)} {host}")
            if code != 200 or not body or body[:1] != b"[":
                continue
            try:
                hits = parse_invidious_search(json.loads(body))
            except json.JSONDecodeError:
                continue
            for hit in hits[:2]:
                status, info_body = http_get(
                    f"https://{host}/api/v1/videos/{hit['id']}",
                    {"User-Agent": ua, "Accept": "application/json"},
                    timeout=20,
                )
                if status != 200 or not info_body:
                    attempts.append(f"video {status} {hit['id']} {host}")
                    continue
                try:
                    info = json.loads(info_body)
                except json.JSONDecodeError:
                    continue
                itag = pick_h264_itag(info)
                if not itag:
                    attempts.append(f"no h264 itag {hit['id']}")
                    continue
                local = f"https://{host}/latest_version?id={hit['id']}&itag={itag}&local=true"
                dest = folder / f"invidious-{hit['id']}.mp4"
                cuts += 1
                ok = _ffmpeg_cut(local, dest, ua)
                attempts.append(f"local itag {itag} {hit['id']} -> {'saved' if ok else 'miss'}")
                if ok:
                    done = _finish("youtube_frontend", dest, f"https://{host}/watch?v={hit['id']}", "invidious-local", attempts)
                    if done:
                        return done
                if cuts >= 2:
                    break
            if cuts >= 2:
                break
        if cuts >= 2:
            break
    piped = _piped_download(queries[0], folder, attempts, ua) if cuts < 2 else None
    if piped:
        return piped
    return _row(
        "youtube_frontend",
        "unavailable",
        error="; ".join(attempts[-8:]) or "no invidious or piped video",
        attempts=attempts,
    )


def fetch_public_report(bundle: dict, output: Path) -> dict:
    """Run each cookie-free source once. Source misses do not fail the report."""
    output.mkdir(parents=True, exist_ok=True)
    probes = (
        ("x_public", probe_x),
        ("tiktok_public", probe_tiktok),
        ("reddit_json", probe_reddit),
        ("youtube_frontend", probe_youtube),
    )
    sources = []
    for name, probe in probes:
        folder = output / name
        try:
            row = probe(bundle, folder)
        except Exception as exc:
            row = _row(name, "unavailable", error=f"{type(exc).__name__}: {exc}")
        row["name"] = name
        if row.get("status") not in ("downloaded", "unavailable"):
            row["status"] = "unavailable"
        sources.append(row)
    return {"ok": True, "sources": sources}


def public_file_for_platform(bundle: dict, platform: str, folder: Path) -> dict | None:
    """One cookie-free file for a content-plan platform, or None when it misses."""
    if platform == "x":
        row = probe_x(bundle, folder)
        if row.get("status") == "downloaded":
            return row
        reddit = probe_reddit(bundle, folder, platform="x", name="reddit_json")
        return reddit if reddit.get("status") == "downloaded" else row
    if platform == "tiktok":
        row = probe_tiktok(bundle, folder)
        if row.get("status") == "downloaded":
            return row
        reddit = probe_reddit(bundle, folder, platform="tiktok", name="reddit_json")
        return reddit if reddit.get("status") == "downloaded" else row
    if platform == "youtube":
        row = probe_youtube(bundle, folder)
        if row.get("status") == "downloaded":
            return row
        reddit = probe_reddit(bundle, folder, platform="youtube", name="reddit_json")
        return reddit if reddit.get("status") == "downloaded" else row
    return None
