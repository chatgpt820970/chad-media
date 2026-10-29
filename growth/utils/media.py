"""Download, scan, and fit a clip to 9:16 with blurred padding.

The subject is scaled to fit. Cropping is only applied to the blurred background
fill, and to a small optional zoom that is skipped when the frame is letterboxed
or already cut off.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import cv2
except ImportError:
    cv2 = None

from growth.utils.quality import copyright_reason, corner_logo_regions, edge_cutoff, letterbox
from growth.utils.retry import MinorFailure
from growth.utils.ytdlp import Candidate, download_command, safe_id

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_W = 1080
TARGET_H = 1920


@dataclass
class Prepared:
    source_id: str
    width: int
    height: int
    duration: float
    ocr_text: str = ""
    corner_hits: tuple[str, ...] = ()
    edge_cutoff: bool = False
    scanners_unavailable: bool = False
    output_seconds: float = 0
    local_path: str = ""
    zoom: bool = False


def escape_drawtext(text: str) -> str:
    cleaned = []
    for char in text:
        if char.isascii() and (char.isalnum() or char in " '"):
            cleaned.append(char)
        else:
            cleaned.append(" ")
    compact = " ".join("".join(cleaned).split())[:42]
    return compact.replace("'", "").replace(":", "\\:").replace("%", "")


def vertical_graph(hook: str, enable: str, zoom: bool) -> str:
    """Fit the whole frame inside 1080x1920. The blurred layer is the only cover-crop."""
    zoom_chain = ""
    if zoom:
        zoom_chain = "scale=trunc(iw*1.04/2)*2:trunc(ih*1.04/2)*2,crop=trunc(iw/1.04/2)*2:trunc(ih/1.04/2)*2,"
    text = ""
    safe = escape_drawtext(hook)
    if safe:
        font = f"fontfile={FONT}:" if Path(FONT).is_file() else ""
        text = (
            f",drawtext={font}text='{safe}':fontsize=52:fontcolor=white:borderw=3:"
            f"bordercolor=black:x=(w-text_w)/2:y=h*0.08:enable='{enable}'"
        )
    return (
        f"[0:v]{zoom_chain}split[fg][bg];"
        f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,crop={TARGET_W}:{TARGET_H},boxblur=24:2[blur];"
        f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fit];"
        f"[blur][fit]overlay=(W-w)/2:(H-h)/2{text},setsar=1[v]"
    )


def foreground_is_padded(graph: str) -> bool:
    """The foreground branch fits inside the frame. It is not cover-cropped."""
    if "force_original_aspect_ratio=decrease[fit]" not in graph:
        return False
    if "boxblur" not in graph or "overlay=(W-w)/2:(H-h)/2" not in graph:
        return False
    foreground = graph.split("[fg]scale=", 1)[1].split("[fit]", 1)[0]
    return "crop=" not in foreground


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MinorFailure(str(exc)[:300]) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed")[-400:]
        raise MinorFailure(detail)
    return result


def probe(path: Path) -> tuple[int, int, float]:
    if shutil.which("ffprobe") is None:
        raise MinorFailure("ffprobe unavailable")
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        60,
    )
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or [{}]
    stream = streams[0] if streams else {}
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    return width, height, duration


def _raw_frame(path: Path, second: float, dest: Path) -> tuple[bytes, int, int]:
    width, height, _duration = probe(path)
    if width < 4 or height < 4:
        raise MinorFailure("probe returned no frame size")
    if shutil.which("ffmpeg") is None:
        raise MinorFailure("ffmpeg unavailable")
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, second):.2f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-y",
            str(dest),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        raise MinorFailure("frame extract failed")
    data = dest.read_bytes()
    expected = width * height * 3
    if len(data) < expected:
        raise MinorFailure("short frame")
    return data[:expected], width, height


def _ocr_corners(path: Path, second: float, work: Path, stamp: str) -> str:
    if shutil.which("tesseract") is None or shutil.which("ffmpeg") is None:
        return ""
    crops = {
        "tl": "crop=iw*0.22:ih*0.18:0:0",
        "tr": "crop=iw*0.22:ih*0.18:iw*0.78:0",
        "bl": "crop=iw*0.22:ih*0.18:0:ih*0.82",
        "br": "crop=iw*0.22:ih*0.18:iw*0.78:ih*0.82",
        "band": "crop=iw*0.7:ih*0.12:iw*0.15:ih*0.84",
    }
    chunks: list[str] = []
    for name, crop in crops.items():
        image = work / f"{stamp}-{name}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{max(0.0, second):.2f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                crop,
                "-y",
                str(image),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if not image.is_file():
            continue
        ocr = subprocess.run(
            ["tesseract", str(image), "stdout", "--psm", "7"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        chunks.append(ocr.stdout or "")
    return "\n".join(chunks)


def opencv_corner_hits(image: Path) -> list[str]:
    """Edge-density logos via opencv, when the package is installed."""
    if cv2 is None or not image.is_file():
        return []
    gray = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return []
    edges = cv2.Canny(gray, 80, 160)
    height, width = edges.shape[:2]
    if width < 8 or height < 8:
        return []
    fraction = 0.16

    def density(x0: int, y0: int, x1: int, y1: int) -> float:
        view = edges[y0:y1, x0:x1]
        if view.size == 0:
            return 0.0
        return float((view > 0).mean())

    cx0, cy0 = int(width * 0.3), int(height * 0.3)
    center = density(cx0, cy0, int(width * 0.7), int(height * 0.7))
    corner_w = max(2, int(width * fraction))
    corner_h = max(2, int(height * fraction))
    regions = {
        "top-left": (0, 0, corner_w, corner_h),
        "top-right": (width - corner_w, 0, width, corner_h),
        "bottom-left": (0, height - corner_h, corner_w, height),
        "bottom-right": (width - corner_w, height - corner_h, width, height),
    }
    hits: list[str] = []
    for name, box in regions.items():
        score = density(*box)
        if score >= 0.08 and score >= center * 2.2 + 0.03:
            hits.append(name)
    return hits


def _png_frame(path: Path, second: float, dest: Path) -> None:
    if shutil.which("ffmpeg") is None:
        return
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, second):.2f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-y",
            str(dest),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )


def scan_video(path: Path, work: Path) -> tuple[str, list[str], bool, bool, bool]:
    """Return ocr text, corner hits, edge cutoff, letterbox, and whether scanners were missing."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None or shutil.which("tesseract") is None:
        return "", [], False, False, True
    width, _height, duration = probe(path)
    del width
    stamps = [min(1.0, max(0.0, duration * 0.15))]
    if duration > 4:
        stamps.append(min(duration * 0.55, max(0.0, duration - 0.4)))
    ocr_parts: list[str] = []
    hits: list[str] = []
    cutoff = False
    boxed = False
    for index, second in enumerate(stamps):
        raw_path = work / f"frame-{index}.rgb"
        frame, frame_w, frame_h = _raw_frame(path, second, raw_path)
        ocr_parts.append(_ocr_corners(path, second, work, f"ocr-{index}"))
        for name in corner_logo_regions(frame, frame_w, frame_h):
            if name not in hits:
                hits.append(name)
        png = work / f"frame-{index}.png"
        _png_frame(path, second, png)
        for name in opencv_corner_hits(png):
            if name not in hits:
                hits.append(name)
        cutoff = cutoff or edge_cutoff(frame, frame_w, frame_h)
        boxed = boxed or letterbox(frame, frame_w, frame_h)
    return "\n".join(ocr_parts), hits, cutoff, boxed, False


def _cookies(url: str, env: dict[str, str]) -> str | None:
    host = url.lower()
    pairs = (
        ("youtube.com", "YT_COOKIES_FILE"),
        ("youtu.be", "YT_COOKIES_FILE"),
        ("tiktok.com", "TIKTOK_COOKIES_FILE"),
        ("instagram.com", "INSTAGRAM_COOKIES_FILE"),
        ("x.com", "X_COOKIES_FILE"),
        ("twitter.com", "X_COOKIES_FILE"),
    )
    for needle, key in pairs:
        if needle in host:
            path = (env.get(key) or "").strip()
            if path and Path(path).is_file():
                return path
            return None
    return None


def _clip_seconds(duration: float, config: dict) -> float:
    wanted = float(config["clip_seconds"])
    low = float(config["clip_min_seconds"])
    high = float(config["clip_max_seconds"])
    wanted = min(high, max(low, wanted))
    if duration > 0:
        wanted = min(wanted, max(0.0, duration - 0.2))
    return wanted


def produce(candidate: Candidate, hook: str, config: dict, work: Path, env: dict[str, str]) -> Prepared:
    """Download, scan, and render one candidate. Raises MinorFailure to skip it."""
    work.mkdir(parents=True, exist_ok=True)
    token = safe_id(candidate.source_id)
    source = work / f"{token}.mp4"
    if shutil.which("yt-dlp") is None:
        raise MinorFailure("yt-dlp unavailable")
    _run(download_command(candidate.url, str(source), _cookies(candidate.url, env)), 180)
    if not source.is_file():
        matches = list(work.glob(f"{token}.*"))
        if not matches:
            raise MinorFailure("download saved nothing")
        source = matches[0]
    width, height, duration = probe(source)
    ocr_text, hits, cutoff, boxed, unavailable = scan_video(source, work)
    output = _clip_seconds(duration, config)
    blocked = copyright_reason(
        candidate.title,
        candidate.description,
        duration,
        float(config.get("max_source_seconds") or 4800),
    )
    allow_zoom = bool(config.get("zoom", True)) and not cutoff and not boxed
    rendered = work / f"{token}-out.mp4"
    if (
        not blocked
        and not unavailable
        and not hits
        and not cutoff
        and min(width, height) >= int(config["min_resolution"])
    ):
        if duration < float(config["clip_min_seconds"]):
            return Prepared(
                source_id=candidate.source_id,
                width=width,
                height=height,
                duration=duration,
                ocr_text=ocr_text,
                corner_hits=tuple(hits),
                edge_cutoff=cutoff,
                scanners_unavailable=unavailable,
                output_seconds=output,
            )
        enable = str(config.get("hook_enable") or "lt(t,1.5)")
        graph = vertical_graph(hook, enable, allow_zoom)
        start = 0.0
        if duration > output + 1.5:
            start = min(duration - output - 0.3, max(0.8, duration * 0.08))
        _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.2f}",
                "-i",
                str(source),
                "-t",
                f"{output:.2f}",
                "-filter_complex",
                graph,
                "-map",
                "[v]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(rendered),
            ],
            180,
        )
    return Prepared(
        source_id=candidate.source_id,
        width=width,
        height=height,
        duration=duration,
        ocr_text=ocr_text,
        corner_hits=tuple(hits),
        edge_cutoff=cutoff,
        scanners_unavailable=unavailable,
        output_seconds=output,
        local_path=str(rendered) if rendered.is_file() else "",
        zoom=allow_zoom,
    )
