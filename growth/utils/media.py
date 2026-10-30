"""Download and scan a clip.

X can pad a landscape frame with a blurred background. YouTube and Instagram
repost only native vertical video: no pad, no crop, no burned-in text.
"""

from __future__ import annotations

import json
import re
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


def is_vertical(width: int, height: int) -> bool:
    return height > width * 1.2


def light_touch_filters(tweaks: dict) -> tuple[str, str]:
    """Slight speed, optional grade, optional mirror. No crop, pad, or text."""
    parts: list[str] = []
    if tweaks.get("mirror"):
        parts.append("hflip")
    if tweaks.get("grade", True):
        parts.append("eq=contrast=1.04:saturation=1.06:gamma=1.01")
    speed = float(tweaks.get("speed") or 1.03)
    parts.append(f"setpts=PTS/{speed:.3f}")
    return ",".join(parts), f"atempo={speed:.3f}"


def usable_trim(duration: float, clip_min: float, tweaks: dict) -> float:
    """Trim 0.3-0.5s only when the sped-up result still meets the minimum length."""
    speed = float(tweaks.get("speed") or 1.03)
    requested = float(tweaks.get("trim_seconds") or 0.4)
    room = float(duration) - float(clip_min) * speed
    if room < 0.3:
        return 0.0
    trimmed = min(requested, room, 0.5)
    if trimmed < 0.3:
        return 0.0
    return round(trimmed, 2)


def has_audio(path: Path) -> bool:
    if shutil.which("ffprobe") is None:
        return False
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return bool((result.stdout or "").strip())


SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
SCENE_TIME = re.compile(r"pts_time:([0-9.]+)")
FADE_OUT = 0.4
FADE_IN = 0.15


def pick_clean_end(duration: float, silences: list[float], scenes: list[float], window: float = 3.0, minimum: float = 0.0) -> float:
    """End on a silence gap (preferred) or scene cut inside the last `window` seconds."""
    low = max(minimum, duration - window)
    for group in (silences, scenes):
        inside = [t for t in group if low <= t <= duration - 0.05]
        if inside:
            return round(max(inside), 2)
    return round(duration, 2)


def parse_silence_starts(stderr: str, offset: float = 0.0) -> list[float]:
    return [offset + float(v) for v in SILENCE_START.findall(stderr or "")]


def parse_scene_times(stderr: str, offset: float = 0.0) -> list[float]:
    return [offset + float(v) for v in SCENE_TIME.findall(stderr or "")]


def clean_end(source: Path, duration: float, minimum: float) -> float:
    """Find a cut point near the end so nothing stops mid-word. Falls back to the full length."""
    if shutil.which("ffmpeg") is None or duration <= 0:
        return duration
    offset = max(0.0, duration - 3.5)
    silences: list[float] = []
    scenes: list[float] = []
    try:
        if has_audio(source):
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-ss", f"{offset:.2f}", "-i", str(source), "-vn",
                 "-af", "silencedetect=noise=-32dB:d=0.18", "-f", "null", "-"],
                capture_output=True, text=True, timeout=60, check=False,
            )
            silences = parse_silence_starts(out.stderr, offset)
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{offset:.2f}", "-i", str(source), "-an",
             "-vf", "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=90, check=False,
        )
        scenes = parse_scene_times(out.stderr, offset)
    except Exception:
        return duration
    return pick_clean_end(duration, silences, scenes, 3.0, minimum)


def fit_graph(video_filter: str) -> str:
    """Fit the whole picture in 1080x1920 over a blurred copy. No cover-crop of the subject."""
    return (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2[blur];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[fit];"
        "[blur][fit]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        + video_filter
        + "[v]"
    )


def apply_light_touch(source: Path, dest: Path, tweaks: dict, clip_min: float) -> float:
    """Re-encode one vertical file at 1080x1920. Returns the new duration. Raises MinorFailure.

    Trims up to 0.5s from both the start and the end, speeds up slightly, grades
    lightly. Works with or without an audio stream. No text, no stock footage.
    """
    _width, _height, duration, _start = probe(source)
    speed = float(tweaks.get("speed") or 1.03)
    trim = usable_trim(duration, clip_min, tweaks)
    head = trim
    # end on a silence gap or scene cut so no word or lyric is chopped
    end = clean_end(source, duration, head + float(clip_min) * speed)
    tail = round(max(0.0, duration - end), 2)
    if duration - head - tail < float(clip_min) * speed:
        tail = 0.0
    length = max(0.5, duration - head - tail)
    out_len = length / speed
    fade_start = max(0.0, out_len - FADE_OUT)
    video_filter, audio_filter = light_touch_filters(tweaks)
    video_filter += f",fade=t=in:st=0:d={FADE_IN},fade=t=out:st={fade_start:.2f}:d={FADE_OUT}"
    audio_filter += f",afade=t=in:st=0:d={FADE_IN},afade=t=out:st={fade_start:.2f}:d={FADE_OUT}"
    command = ["ffmpeg", "-y"]
    if head > 0:
        command.extend(["-ss", f"{head:.2f}"])
    # input-side -t so the tail is cut from the source before the speed-up
    command.extend(["-t", f"{length:.2f}"])
    command.extend(["-i", str(source)])
    command.extend(["-filter_complex", fit_graph(video_filter), "-map", "[v]"])
    if has_audio(source):
        command.extend(["-map", "0:a:0", "-af", audio_filter, "-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    _run(command, 240)
    if not dest.is_file():
        raise MinorFailure("light touch produced no file")
    _w, _h, touched, _s = probe(dest)
    return touched


def repost_pad_graph() -> str:
    """Fit a landscape frame into 9:16. No zoom and no burned-in text."""
    return (
        f"[0:v]split[fg][bg];"
        f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,crop={TARGET_W}:{TARGET_H},boxblur=24:2[blur];"
        f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fit];"
        f"[blur][fit]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )


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
    if "[fg]scale=" not in graph or "force_original_aspect_ratio=decrease[fit]" not in graph:
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
        print(f"command failed rc={result.returncode}: {' '.join(command[:3])} ... stderr: {(result.stderr or '')[-1200:]}", flush=True)
        raise MinorFailure(detail)
    return result


def bitrate_kbps(path: Path) -> int:
    """Overall bitrate in kbps, 0 when unknown."""
    if shutil.which("ffprobe") is None:
        return 0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=bit_rate", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return int(float((out.stdout or "0").strip() or 0) / 1000)
    except Exception:
        return 0


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
            "stream=width,height,start_time:format=duration",
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
    format_duration = float((payload.get("format") or {}).get("duration") or 0)
    start = float(stream.get("start_time") or 0)
    duration = format_duration
    if start > 0.5 and format_duration > start + 0.2:
        duration = format_duration - start
    return width, height, duration, start


def _raw_frame(path: Path, second: float, dest: Path) -> tuple[bytes, int, int]:
    width, height, _duration, _start = probe(path)
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
    # Scale the crop so a small broadcaster bug is large enough for OCR.
    crops = {
        "tl": "crop=iw*0.28:ih*0.22:0:0,scale=iw*3:ih*3:flags=neighbor",
        "tr": "crop=iw*0.28:ih*0.22:iw*0.72:0,scale=iw*3:ih*3:flags=neighbor",
        "bl": "crop=iw*0.28:ih*0.22:0:ih*0.78,scale=iw*3:ih*3:flags=neighbor",
        "br": "crop=iw*0.28:ih*0.22:iw*0.72:ih*0.78,scale=iw*3:ih*3:flags=neighbor",
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
    _width, _height, duration, start = probe(path)
    fractions = (0.08, 0.22, 0.38, 0.54, 0.70, 0.86)
    span = max(0.0, duration)
    stamps = []
    for fraction in fractions:
        if span <= 0:
            stamps.append(start)
            continue
        at = start + span * fraction
        stamps.append(min(at, start + max(0.0, span - 0.15)))
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


def produce(
    candidate: Candidate,
    hook: str,
    config: dict,
    work: Path,
    env: dict[str, str],
    source_file: Path | None = None,
) -> Prepared:
    """Download, scan, and render one candidate. Raises MinorFailure to skip it.

    `source_file` skips the download when the clip is already on disk. The scan,
    copyright check, and 9:16 render are the same either way.
    """
    work.mkdir(parents=True, exist_ok=True)
    token = safe_id(candidate.source_id)
    source = work / f"{token}.mp4"
    if source_file is not None and Path(source_file).is_file():
        source = Path(source_file)
    else:
        if shutil.which("yt-dlp") is None:
            raise MinorFailure("yt-dlp unavailable")
        _run(download_command(candidate.url, str(source), _cookies(candidate.url, env)), 180)
        if not source.is_file():
            matches = list(work.glob(f"{token}.*"))
            if not matches:
                raise MinorFailure("download saved nothing")
            source = matches[0]
    width, height, duration, media_start = probe(source)
    ocr_text, hits, cutoff, boxed, unavailable = scan_video(source, work)
    output = _clip_seconds(duration, config)
    blocked = copyright_reason(
        candidate.title,
        candidate.description,
        duration,
        float(config.get("max_source_seconds") or 4800),
    )
    repost = bool(config.get("repost"))
    allow_zoom = (not repost) and bool(config.get("zoom", True)) and not cutoff and not boxed
    rendered = work / f"{token}-out.mp4"
    if (
        not blocked
        and not unavailable
        and not cutoff
        and min(width, height) >= int(config["min_resolution"])
    ):
        low = float(config["clip_min_seconds"])
        high = float(config["clip_max_seconds"])
        if repost and (duration < low or duration > high or not is_vertical(width, height)):
            return Prepared(
                source_id=candidate.source_id,
                width=width,
                height=height,
                duration=duration,
                ocr_text=ocr_text,
                corner_hits=tuple(hits),
                edge_cutoff=cutoff,
                scanners_unavailable=unavailable,
                output_seconds=duration,
            )
        if repost:
            copied = subprocess.run(
                ["ffmpeg", "-y", "-i", str(source), "-c", "copy", "-movflags", "+faststart", str(rendered)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if copied.returncode != 0 or not rendered.is_file():
                print(f"ffmpeg copy failed rc={copied.returncode}: {(copied.stderr or '')[-600:]}", flush=True)
                _run(
                    ["ffmpeg", "-y", "-i", str(source), "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(rendered)],
                    240,
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
                output_seconds=duration,
                local_path=str(rendered) if rendered.is_file() else "",
                zoom=False,
            )
        if not repost and duration < low:
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
        start = media_start
        if duration > output + 1.5:
            start = media_start + min(duration - output - 0.3, max(0.8, duration * 0.08))
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
