#!/usr/bin/env python3
"""Keyless YouTube Shorts collector.

Finds public funny and feel-good clips (never YouTube), keeps only those
that run 30 seconds to just under 3 minutes, prefers a watermark-free
download, and publishes 1080x1920 H.264 files plus a review feed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state" / "seen.json"
POSTED_PATH = ROOT / "state" / "posted.json"
FEED_PATH = ROOT / "feed" / "shorts_queue.json"
WORK = ROOT / "work"
MEDIA = WORK / "media"
SITE = WORK / "site"

MIN_SECONDS = 30.0
MAX_SECONDS = 179.0
KEEP_DAYS = 4
MAX_QUEUE = 18
RELEASE_TAG = "shorts-media"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Public accounts that currently serve 30s+ clips with no login.
# min_plays drops low-engagement posts. Lanes are mixed each run.
ACCOUNTS: tuple[dict[str, object], ...] = (
    {"user": "mrnigelng", "lane": "chinese", "min_plays": 50_000},
    {"user": "madewithlau", "lane": "chinese", "min_plays": 8_000},
    {"user": "thefoodranger", "lane": "chinese", "min_plays": 15_000},
    {"user": "khaby.lame", "lane": "funny", "min_plays": 100_000},
    {"user": "dudeperfect", "lane": "funny", "min_plays": 80_000},
    {"user": "failarmy", "lane": "funny", "min_plays": 80_000},
    {"user": "kingbach", "lane": "funny", "min_plays": 80_000},
    {"user": "benoftheweek", "lane": "funny", "min_plays": 100_000},
    {"user": "spencerx", "lane": "funny", "min_plays": 80_000},
    {"user": "yoyohoneysingh", "lane": "feelgood", "min_plays": 20_000},
    {"user": "diljitdosanjh", "lane": "feelgood", "min_plays": 20_000},
    {"user": "armaanmalik", "lane": "feelgood", "min_plays": 10_000},
    {"user": "jonitamusic", "lane": "feelgood", "min_plays": 10_000},
    {"user": "humankind", "lane": "feelgood", "min_plays": 50_000},
)

LANE_ORDER = ("chinese", "funny", "feelgood")
PLATFORM_RE = re.compile(
    r"(tiktok|douyin|instagram|musical\.ly|抖音)",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"@[A-Za-z][A-Za-z0-9._]{2,24}\b")
YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)

# Tight corner boxes where platform logos and @handles are burned in.
CORNER_BOXES = (
    (0.00, 0.78, 0.42, 0.99),
    (0.58, 0.78, 1.00, 0.99),
    (0.00, 0.00, 0.36, 0.16),
    (0.64, 0.00, 1.00, 0.16),
    (0.00, 0.40, 0.22, 0.70),
)


@dataclass
class Candidate:
    platform: str
    video_id: str
    source_url: str
    lane: str
    duration: float
    plays: int
    description: str
    width: int
    height: int

    @property
    def item_id(self) -> str:
        prefix = {"tiktok": "tt", "x": "x", "instagram": "ig"}.get(self.platform)
        if prefix is None:
            raise RuntimeError(f"unsupported platform {self.platform}")
        return f"{prefix}-{self.video_id}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(message, flush=True)


def pages_base() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "chatgpt820970/chad-media")
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}"


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(fallback))
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def http_bytes(url: str, timeout: int = 60, referer: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def run(
    cmd: list[str],
    timeout: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def which(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} is not on PATH")
    return found


def as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def probe(path: Path) -> dict[str, object]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(result.stdout or "{}")
    duration = as_float((data.get("format") or {}).get("duration"))
    video: dict[str, object] | None = None
    audio = False
    for stream in data.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") == "video" and video is None:
            video = stream
        if stream.get("codec_type") == "audio":
            audio = True
    return {
        "duration": duration,
        "width": as_int((video or {}).get("width")),
        "height": as_int((video or {}).get("height")),
        "vcodec": str((video or {}).get("codec_name") or ""),
        "audio": audio,
    }


def discover_tiktok(seen_ids: set[str]) -> list[Candidate]:
    gallery = which("gallery-dl")
    found: list[Candidate] = []
    for account in ACCOUNTS:
        user = str(account["user"])
        lane = str(account["lane"])
        min_plays = int(account["min_plays"])
        url = f"https://www.tiktok.com/@{user}/posts"
        log(f"tiktok: scanning @{user} ({lane})")
        try:
            result = run(
                [
                    gallery,
                    "-q",
                    "-o",
                    "videos=true",
                    "-o",
                    "photos=false",
                    "-o",
                    "audio=false",
                    "-o",
                    "tiktok-range=1-8",
                    "-j",
                    url,
                ],
                timeout=40,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log(f"tiktok: @{user} timed out")
            continue
        raw = (result.stdout or "").strip()
        if not raw:
            log(f"tiktok: @{user} returned no posts")
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log(f"tiktok: @{user} metadata was not JSON")
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, list) or len(item) < 3 or not isinstance(item[2], dict):
                continue
            meta = item[2]
            if meta.get("type") not in (None, "video") and meta.get("post_type") != "video":
                continue
            if meta.get("isAd") is True or "imagePost" in meta:
                continue
            duration = as_float(meta.get("duration"))
            video_id = str(meta.get("id") or "")
            if not video_id or duration < MIN_SECONDS or duration >= 180:
                continue
            author = meta.get("author") if isinstance(meta.get("author"), dict) else {}
            handle = str(author.get("uniqueId") or meta.get("user") or user)
            source_url = f"https://www.tiktok.com/@{handle}/video/{video_id}"
            if YOUTUBE_RE.search(source_url):
                continue
            plays = as_int((meta.get("stats") or {}).get("playCount") if isinstance(meta.get("stats"), dict) else 0)
            if plays < min_plays:
                continue
            candidate = Candidate(
                platform="tiktok",
                video_id=video_id,
                source_url=source_url,
                lane=lane,
                duration=duration,
                plays=plays,
                description=str(meta.get("desc") or meta.get("title") or ""),
                width=as_int(meta.get("width")),
                height=as_int(meta.get("height")),
            )
            if candidate.item_id in seen_ids:
                continue
            found.append(candidate)
    return found


def probe_x() -> str:
    """Return a short status. Timelines need a logged-in session."""
    gallery = shutil.which("gallery-dl")
    if gallery:
        try:
            result = run(
                [gallery, "-q", "--range", "1-1", "-j", "https://x.com/X/media"],
                timeout=25,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "skipped: timed out"
        text = f"{result.stdout}\n{result.stderr}"
        if "AuthRequired" in text or "authenticated cookies" in text:
            return "skipped: timeline requires a login"
        if '"error"' in text and result.returncode != 0:
            return "skipped: public timeline is not available"
    try:
        request = urllib.request.Request(
            "https://syndication.twitter.com/srv/timeline-profile/screen-name/X",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(200)
            if body.strip().startswith(b"{"):
                return "ok"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            return "skipped: public timeline requires a login or is rate-limited"
        return f"skipped: syndication HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - probe must not abort the run
        return f"skipped: {exc.__class__.__name__}"
    return "skipped: no public video list"


def probe_instagram() -> str:
    gallery = shutil.which("gallery-dl")
    if not gallery:
        return "skipped: gallery-dl is not installed"
    try:
        result = run(
            [gallery, "-q", "--range", "1-1", "-j", "https://www.instagram.com/instagram/reels/"],
            timeout=12,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "skipped: reels require a login"
    text = f"{result.stdout}\n{result.stderr}"
    if "login" in text.lower():
        return "skipped: reels require a login"
    if result.returncode != 0:
        return "skipped: public reels are not available"
    return "ok"


def interleave(candidates: list[Candidate]) -> list[Candidate]:
    buckets: dict[str, list[Candidate]] = {lane: [] for lane in LANE_ORDER}
    for candidate in candidates:
        if candidate.lane not in buckets:
            log(f"skip unknown lane {candidate.lane}")
            continue
        buckets[candidate.lane].append(candidate)
    for lane in LANE_ORDER:
        buckets[lane].sort(key=lambda item: item.plays, reverse=True)
    ordered: list[Candidate] = []
    while any(buckets[lane] for lane in LANE_ORDER):
        for lane in LANE_ORDER:
            if buckets[lane]:
                ordered.append(buckets[lane].pop(0))
    return ordered


def download_tiktok_clean(source_url: str, dest: Path) -> None:
    """Download TikTok's no-watermark play URL.

    gallery-dl's highest rendition matches the watermarked HD file.
    tikwm's `play` field is the public unauthenticated clean variant.
    `wmplay` and `hdplay` are never saved.
    """
    api = "https://www.tikwm.com/api/?hd=1&url=" + urllib.parse.quote(source_url, safe="")
    last_error: Exception | None = None
    for attempt in (1, 2):
        time.sleep(1.1)
        try:
            payload = json.loads(http_bytes(api, timeout=40, referer="https://www.tikwm.com/").decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or not data.get("play"):
                raise RuntimeError(f"no clean play URL ({payload.get('msg') if isinstance(payload, dict) else 'bad response'})")
            play = str(data["play"])
            if "wmplay" in play:
                raise RuntimeError("refusing watermarked play URL")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(http_bytes(play, timeout=120, referer="https://www.tikwm.com/"))
            if dest.stat().st_size < 100_000:
                raise RuntimeError("download was too small")
            return
        except Exception as exc:  # noqa: BLE001 - retry then surface
            last_error = exc
            log(f"download attempt {attempt} failed: {exc}")
            time.sleep(2)
    raise RuntimeError(str(last_error or "download failed"))


def encode_vertical(src: Path, dest: Path) -> None:
    info = probe(src)
    width = int(info["width"])
    height = int(info["height"])
    if width < 2 or height < 2:
        raise RuntimeError("could not read video dimensions")
    portrait = (height / width) >= 1.15
    audio = "loudnorm=I=-16:TP=-1.5:LRA=11"
    common = [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-level:v",
        "4.2",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-maxrate",
        "4M",
        "-bufsize",
        "8M",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if portrait:
        video_args = [
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-af",
            audio,
        ]
    else:
        video_args = [
            "-filter_complex",
            (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2[bg];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
            ),
            "-af",
            audio,
        ]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), *video_args, *common, str(dest)]
    result = run(cmd, timeout=300, check=False)
    if result.returncode != 0:
        # Some clips make loudnorm fail. Keep the picture and use a simpler normalizer.
        video_args = [video_args[0], video_args[1], "-af", "dynaudnorm=f=150:g=15"]
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), *video_args, *common, str(dest)]
        result = run(cmd, timeout=300, check=False)
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise RuntimeError(f"ffmpeg failed: {tail}")


def extract_frames(video: Path, item_id: str) -> list[Path]:
    info = probe(video)
    duration = float(info["duration"])
    frames: list[Path] = []
    for index, fraction in enumerate((0.15, 0.50, 0.85), start=1):
        stamp = max(0.2, duration * fraction)
        dest = MEDIA / f"{item_id}-{index}.jpg"
        run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{stamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(dest),
            ],
            timeout=60,
        )
        frames.append(dest)
    return frames


def ocr_text(image: Path) -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ""
    languages = "eng+chi_sim"
    result = run(
        [tesseract, str(image), "stdout", "--psm", "6", "-l", languages],
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        result = run(
            [tesseract, str(image), "stdout", "--psm", "6", "-l", "eng"],
            timeout=30,
            check=False,
        )
    return result.stdout or ""


def watermark_reason(frames: list[Path]) -> str | None:
    for frame in frames:
        probed = run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(frame),
            ],
            timeout=30,
        )
        width_text, height_text = (probed.stdout or "0,0").strip().split(",")
        width, height = int(width_text), int(height_text)
        if width < 2 or height < 2:
            continue
        for box in CORNER_BOXES:
            x = int(width * box[0])
            y = int(height * box[1])
            crop_w = max(8, int(width * box[2]) - x)
            crop_h = max(8, int(height * box[3]) - y)
            crop = frame.with_name(frame.stem + "-crop.jpg")
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(frame),
                    "-vf",
                    f"crop={crop_w}:{crop_h}:{x}:{y},scale=iw*3:ih*3",
                    str(crop),
                ],
                timeout=30,
            )
            text = ocr_text(crop)
            crop.unlink(missing_ok=True)
            if PLATFORM_RE.search(text):
                return "platform watermark"
            if HANDLE_RE.search(text):
                return "handle watermark"
    return None


def draft_title(description: str, lane: str) -> str:
    text = re.sub(r"#\S+", " ", description)
    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-|")
    if len(text) < 8:
        fallback = {
            "funny": "Wait for it",
            "chinese": "This one came out of nowhere",
            "feelgood": "A little warmth for today",
        }
        text = fallback.get(lane, "Worth a watch")
    sentence = re.split(r"(?<=[.!?])\s+", text)[0].strip()
    if len(sentence) < 8:
        sentence = text
    if len(sentence) > 90:
        sentence = sentence[:90].rsplit(" ", 1)[0].strip()
    return sentence or "Worth a watch"


def draft_caption(description: str, lane: str) -> str:
    text = re.sub(r"#\S+", " ", description)
    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    lead = {
        "funny": "This one earns the replay.",
        "chinese": "A sharp little bit of comedy.",
        "feelgood": "Something warm for the middle of the day.",
    }.get(lane, "Worth thirty seconds.")
    body = f"{lead} {text}".strip()
    if len(body) > 280:
        body = body[:280].rsplit(" ", 1)[0].strip()
    return body


def category_id(lane: str) -> str:
    if lane == "feelgood":
        return "10"
    if lane == "funny":
        return "23"
    if lane == "chinese":
        return "23"
    raise RuntimeError(f"unknown lane {lane}")


def media_names(item_id: str) -> list[str]:
    return [f"{item_id}.mp4", f"{item_id}-1.jpg", f"{item_id}-2.jpg", f"{item_id}-3.jpg"]


def item_id_from_media_name(name: str) -> str | None:
    frame = re.fullmatch(r"(.+)-([123])\.jpg", name)
    if frame:
        return frame.group(1)
    video = re.fullmatch(r"(.+)\.mp4", name)
    if video and not video.group(1).endswith(".src"):
        return video.group(1)
    return None


def remember(seen: dict, candidate: Candidate, outcome: str, reason: str, duration: float) -> None:
    entries = seen.setdefault("entries", {})
    entries[candidate.item_id] = {
        "source_url": candidate.source_url,
        "platform": candidate.platform,
        "lane": candidate.lane,
        "outcome": outcome,
        "reason": reason,
        "duration": round(duration, 3),
        "seen_at": iso(utc_now()),
    }


def process_candidate(candidate: Candidate, seen: dict) -> dict | None:
    raw_path = MEDIA / f"{candidate.item_id}.src.mp4"
    out_path = MEDIA / f"{candidate.item_id}.mp4"
    try:
        if candidate.platform == "tiktok":
            download_tiktok_clean(candidate.source_url, raw_path)
        else:
            raise RuntimeError(f"no downloader for {candidate.platform}")
        info = probe(raw_path)
        duration = float(info["duration"])
        if duration <= 0 or not info["vcodec"]:
            raise RuntimeError("downloaded file is not a readable video")
        if duration < MIN_SECONDS or duration >= 180:
            remember(seen, candidate, "rejected", "duration", duration)
            log(f"skip {candidate.item_id}: duration {duration:.1f}s")
            return None
        if not info["audio"]:
            remember(seen, candidate, "rejected", "no audio", duration)
            log(f"skip {candidate.item_id}: no audio")
            return None
        encode_vertical(raw_path, out_path)
        encoded = probe(out_path)
        encoded_duration = float(encoded["duration"])
        if encoded_duration < MIN_SECONDS - 0.4 or encoded_duration >= 180:
            remember(seen, candidate, "rejected", "encoded duration", encoded_duration)
            log(f"skip {candidate.item_id}: encoded duration {encoded_duration:.1f}s")
            return None
        if encoded["width"] != 1080 or encoded["height"] != 1920 or encoded["vcodec"] != "h264":
            remember(seen, candidate, "rejected", "encode shape", encoded_duration)
            log(f"skip {candidate.item_id}: bad encode {encoded}")
            return None
        if out_path.stat().st_size > 95_000_000:
            remember(seen, candidate, "rejected", "file too large", encoded_duration)
            log(f"skip {candidate.item_id}: file too large")
            return None
        frames = extract_frames(out_path, candidate.item_id)
        reason = watermark_reason(frames)
        if reason:
            remember(seen, candidate, "rejected", reason, encoded_duration)
            log(f"skip {candidate.item_id}: {reason}")
            for frame in frames:
                frame.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
            return None
        remember(seen, candidate, "queued", "", encoded_duration)
        base = pages_base()
        log(f"queued {candidate.item_id} {encoded_duration:.1f}s plays={candidate.plays} {candidate.lane}")
        return {
            "id": candidate.item_id,
            "source_url": candidate.source_url,
            "platform": candidate.platform,
            "duration": round(encoded_duration, 3),
            "mp4_url": f"{base}/shorts/v/{candidate.item_id}.mp4",
            "frame_urls": [
                f"{base}/shorts/f/{candidate.item_id}-1.jpg",
                f"{base}/shorts/f/{candidate.item_id}-2.jpg",
                f"{base}/shorts/f/{candidate.item_id}-3.jpg",
            ],
            "title": draft_title(candidate.description, candidate.lane),
            "caption": draft_caption(candidate.description, candidate.lane),
            "status": "ready",
            "lane": candidate.lane,
            "youtube_category_id": category_id(candidate.lane),
            "queued_at": iso(utc_now()),
        }
    finally:
        raw_path.unlink(missing_ok=True)


def prune_queue(items: list[dict]) -> list[dict]:
    cutoff = utc_now() - timedelta(days=KEEP_DAYS)
    kept: list[dict] = []
    for item in items:
        queued_at = str(item.get("queued_at") or "")
        try:
            stamp = datetime.strptime(queued_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            stamp = utc_now()
        if stamp >= cutoff:
            kept.append(item)
    kept.sort(key=lambda item: str(item.get("queued_at") or ""), reverse=True)
    kept = kept[:MAX_QUEUE]
    keep_ids = {str(item["id"]) for item in kept}
    if MEDIA.exists():
        for path in MEDIA.iterdir():
            if not path.is_file():
                continue
            item_id = item_id_from_media_name(path.name)
            if item_id is None or item_id not in keep_ids:
                path.unlink(missing_ok=True)
    return kept


def apply_posted(items: list[dict]) -> list[dict]:
    posted = load_json(POSTED_PATH, {"ids": []})
    posted_ids = {str(item) for item in posted.get("ids") or []}
    for item in items:
        if str(item.get("id")) in posted_ids:
            item["status"] = "posted"
        elif item.get("status") not in ("ready", "posted"):
            item["status"] = "ready"
    return items


def write_feed(items: list[dict], sources: dict[str, str]) -> None:
    save_json(
        FEED_PATH,
        {
            "generated_at": iso(utc_now()),
            "sources": sources,
            "items": items,
        },
    )


def collect(target: int, require: int) -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    seen = load_json(STATE_PATH, {"entries": {}})
    seen_ids = set((seen.get("entries") or {}).keys())
    previous = load_json(FEED_PATH, {"items": []})
    items = [item for item in previous.get("items") or [] if isinstance(item, dict) and item.get("id")]
    sources = {
        "tiktok": "ok",
        "x": probe_x(),
        "instagram": probe_instagram(),
    }
    log(f"sources: {json.dumps(sources)}")
    if not sources["x"].startswith("ok"):
        log(f"x: {sources['x']}")
    if not sources["instagram"].startswith("ok"):
        log(f"instagram: {sources['instagram']}")
    candidates = interleave(discover_tiktok(seen_ids))
    log(f"tiktok: {len(candidates)} new candidates in range")
    added = 0
    attempts = 0
    for candidate in candidates:
        if added >= target or attempts >= max(target * 5, 15):
            break
        attempts += 1
        if candidate.item_id in seen_ids:
            continue
        try:
            item = process_candidate(candidate, seen)
        except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the run
            log(f"skip {candidate.item_id}: {exc}")
            continue
        seen_ids.add(candidate.item_id)
        if item is None:
            continue
        items.append(item)
        added += 1
    items = apply_posted(prune_queue(items))
    save_json(STATE_PATH, seen)
    write_feed(items, sources)
    log(f"queue size {len(items)} (added {added})")
    if len(items) < require:
        raise SystemExit(f"queue has {len(items)} items, need at least {require}")


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["gh", *args], timeout=120, check=check)


def ensure_release() -> None:
    view = gh("release", "view", RELEASE_TAG, "--json", "tagName", check=False)
    if view.returncode == 0:
        return
    created = gh(
        "release",
        "create",
        RELEASE_TAG,
        "--title",
        "Shorts media",
        "--notes",
        "Rolling public media for the shorts queue. Old files are pruned by the shorts workflow.",
        check=False,
    )
    if created.returncode != 0 and "already_exists" not in (created.stderr or ""):
        raise RuntimeError(created.stderr or "could not create release")


def release_asset_names() -> set[str]:
    view = gh("release", "view", RELEASE_TAG, "--json", "assets", check=False)
    if view.returncode != 0:
        return set()
    data = json.loads(view.stdout or "{}")
    names: set[str] = set()
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name"):
            names.add(str(asset["name"]))
    return names


def release_sync() -> None:
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        log("release sync skipped: no GH_TOKEN")
        return
    feed = load_json(FEED_PATH, {"items": []})
    wanted: set[str] = set()
    for item in feed.get("items") or []:
        if isinstance(item, dict) and item.get("id"):
            wanted.update(media_names(str(item["id"])))
    MEDIA.mkdir(parents=True, exist_ok=True)
    ensure_release()
    remote = release_asset_names()
    local_names = {path.name for path in MEDIA.iterdir() if path.is_file()}
    for name in sorted(wanted - remote - local_names):
        raise RuntimeError(f"missing media file {name}")
    for name in sorted(wanted - local_names):
        log(f"release: download {name}")
        result = gh(
            "release",
            "download",
            RELEASE_TAG,
            "-p",
            name,
            "-D",
            str(MEDIA),
            "--clobber",
            check=False,
        )
        if result.returncode != 0 or not (MEDIA / name).exists():
            raise RuntimeError(result.stderr or f"could not download {name}")
    for name in sorted(wanted - remote):
        log(f"release: upload {name}")
        result = gh("release", "upload", RELEASE_TAG, str(MEDIA / name), "--clobber", check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or f"could not upload {name}")
    for name in sorted(release_asset_names() - wanted):
        log(f"release: delete {name}")
        gh("release", "delete-asset", RELEASE_TAG, name, "--yes", check=False)


def assemble_site() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    base = pages_base()
    try:
        index = http_bytes(base + "/", timeout=30)
        (SITE / "index.html").write_bytes(index)
    except Exception as exc:  # noqa: BLE001
        log(f"pages index unavailable ({exc}); writing a placeholder")
        (SITE / "index.html").write_text(
            "<!doctype html><title>chad-media</title>media files\n",
            encoding="utf-8",
        )
    feed_dir = SITE / "feed"
    feed_dir.mkdir()
    for name in ("candidates.json", "media_ready.json", "posted.json"):
        try:
            body = http_bytes(f"{base}/feed/{name}", timeout=60)
        except Exception as exc:  # noqa: BLE001
            log(f"keep existing feed skipped for {name}: {exc}")
            continue
        (feed_dir / name).write_bytes(body)
    ready_path = feed_dir / "media_ready.json"
    if ready_path.exists():
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        prefix = base + "/m/"
        names: set[str] = set()
        items = ready.get("items") if isinstance(ready, dict) else {}
        if isinstance(items, dict):
            for value in items.values():
                if not isinstance(value, dict):
                    continue
                urls = [value.get("video_1080_url")]
                urls += list(value.get("frame_urls") or [])
                urls += list(value.get("photo_urls") or [])
                for url in urls:
                    if isinstance(url, str) and url.startswith(prefix):
                        names.add(url[len(prefix):])
        media_dir = SITE / "m"
        media_dir.mkdir()
        for name in sorted(names):
            try:
                (media_dir / name).write_bytes(http_bytes(prefix + name, timeout=120))
            except Exception as exc:  # noqa: BLE001
                log(f"could not mirror {name}: {exc}")
        log(f"mirrored {len(names)} existing media files")
    shutil.copyfile(FEED_PATH, feed_dir / "shorts_queue.json")
    video_dir = SITE / "shorts" / "v"
    frame_dir = SITE / "shorts" / "f"
    video_dir.mkdir(parents=True)
    frame_dir.mkdir(parents=True)
    feed = load_json(FEED_PATH, {"items": []})
    for item in feed.get("items") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        item_id = str(item["id"])
        video = MEDIA / f"{item_id}.mp4"
        if not video.exists():
            raise RuntimeError(f"assemble missing {video.name}")
        shutil.copyfile(video, video_dir / video.name)
        for index in (1, 2, 3):
            frame = MEDIA / f"{item_id}-{index}.jpg"
            if not frame.exists():
                raise RuntimeError(f"assemble missing {frame.name}")
            shutil.copyfile(frame, frame_dir / frame.name)
    log(f"site ready at {SITE}")


def republish() -> None:
    release_sync()
    assemble_site()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Collect and publish the shorts queue")
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--target", type=int, default=4)
    collect_parser.add_argument("--require", type=int, default=int(os.environ.get("SHORTS_REQUIRE", "0")))
    sub.add_parser("release-sync")
    sub.add_parser("assemble-site")
    sub.add_parser("republish")
    args = parser.parse_args(argv)
    if args.command == "collect":
        collect(args.target, args.require)
        return 0
    if args.command == "release-sync":
        release_sync()
        return 0
    if args.command == "assemble-site":
        assemble_site()
        return 0
    if args.command == "republish":
        republish()
        return 0
    raise RuntimeError(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
