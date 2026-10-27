"""Vertical render. Campaign logos are overlaid only after the frame is clean."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PLACEMENTS = {
    "bottom-center": "x=(W-w)/2:y=H-h-48",
    "bottom-left": "x=32:y=H-h-48",
    "bottom-right": "x=W-w-32:y=H-h-48",
    "top-center": "x=(W-w)/2:y=48",
    "top-left": "x=32:y=48",
    "top-right": "x=W-w-32:y=48",
}


def scale_filter(width: int, height: int, crop_x: float, start_note: str = "") -> str:
    del start_note
    shift = min(0.2, max(0.0, crop_x))
    # Crop a sliver off the left, then fit 9:16. The sliver differs per platform.
    return (
        f"crop=iw*({1 - shift}):ih:iw*{shift}:0,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def logo_filter(campaign: dict, width: int, height: int, crop_x: float) -> tuple[str, bool]:
    base = scale_filter(width, height, crop_x)
    logo = campaign.get("logo") or {}
    if not logo.get("required") or not logo.get("file"):
        return f"[0:v]{base}[v]", False
    placement = logo.get("placement") or "bottom-center"
    if placement not in PLACEMENTS:
        placement = "bottom-center"
    ratio = float(logo.get("width_ratio") or 0.18)
    logo_w = max(48, int(width * ratio))
    graph = (
        f"[0:v]{base}[base];"
        f"[1:v]format=rgba,scale={logo_w}:-1,colorchannelmixer=aa={float(logo.get('opacity') or 0.6)}[lg];"
        f"[base][lg]overlay={PLACEMENTS[placement]}[v]"
    )
    return graph, True


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise RuntimeError(f"ffmpeg failed: {tail}")


def render_color(path: Path, width: int, height: int, seconds: float, kind: str) -> None:
    """Build a short stand-in. corner_logo and full_frame paint a white block for the detector."""
    path.parent.mkdir(parents=True, exist_ok=True)
    color = "0x202020"
    draw = ""
    if kind == "corner_logo":
        draw = ",drawbox=x=8:y=8:w=70:h=48:color=white:t=fill"
    elif kind == "full_frame":
        draw = f",drawbox=x={int(width*0.35)}:y={int(height*0.4)}:w=80:h=48:color=white:t=fill"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:d={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-vf",
        f"format=yuv420p{draw}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    ]
    run_ffmpeg(command)


def x_overlay_text(title: str, salt: str) -> str:
    """A short original line. The same source gets the same line on a given UTC day."""
    hooks = (
        "Worth another look",
        "The part people replay",
        "From today",
        "Clean cut",
        "Still going around",
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hook = hooks[_stable_hash(f"{salt}:{day}", len(hooks))]
    words = (title or "").replace("\n", " ").strip()
    line = hook if not words else f"{hook}: {words}"
    cleaned = "".join(ch if ch.isalnum() or ch in " '" else " " for ch in line)
    return " ".join(cleaned.split())[:48]


def _stable_hash(key: str, size: int) -> int:
    if size <= 1:
        return 0
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % size


def x_overlay_graph(text: str) -> str:
    """Light bottom line so an X post is not a plain reupload. No logo."""
    base = scale_filter(720, 1280, 0.03)
    font = ""
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).is_file():
            font = candidate
            break
    safe = (text or "Clip").replace("\\", " ").replace(":", " ").replace("'", "").replace(",", " ")
    font_arg = f"fontfile='{font}':" if font else ""
    return (
        f"[0:v]{base},drawtext={font_arg}text='{safe}':fontsize=26:fontcolor=white:"
        f"borderw=2:bordercolor=black@0.65:x=(w-text_w)/2:y=h*0.84[v]"
    )


def apply_filters(source: Path, dest: Path, graph: str, logo: Path | None, seconds: float, start: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source)]
    if logo is not None:
        command.extend(["-i", str(logo)])
    command.extend(
        [
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    run_ffmpeg(command)


def blur_regions_filter(regions: list[str], width: int, height: int, fraction: float = 0.16) -> str:
    """Blur each flagged edge on a real file. The campaign logo is a later filter."""
    del width, height
    corner_w = "iw*0.16" if fraction <= 0 else f"iw*{fraction:.3f}"
    corner_h = "ih*0.16" if fraction <= 0 else f"ih*{fraction:.3f}"
    boxes = {
        "top-left": ( "0", "0", corner_w, corner_h),
        "top-right": (f"iw-{corner_w}", "0", corner_w, corner_h),
        "bottom-left": ("0", f"ih-{corner_h}", corner_w, corner_h),
        "bottom-right": (f"iw-{corner_w}", f"ih-{corner_h}", corner_w, corner_h),
        "bottom-band": ("iw*0.12", "ih*0.82", "iw*0.76", "ih*0.16"),
    }
    chosen = [region for region in regions if region in boxes]
    if not chosen:
        return "[0:v]null[v]"
    labels = "".join(f"[d{index}]" for index in range(len(chosen)))
    parts = [f"[0:v]split={len(chosen) + 1}{labels}[base]"]
    current = "[base]"
    for index, region in enumerate(chosen):
        x, y, crop_w, crop_h = boxes[region]
        parts.append(f"[d{index}]crop={crop_w}:{crop_h}:{x}:{y},boxblur=20:2[b{index}]")
        output = "[v]" if index == len(chosen) - 1 else f"[o{index}]"
        parts.append(f"{current}[b{index}]overlay={x}:{y}{output}")
        current = output
    return ";".join(parts)
