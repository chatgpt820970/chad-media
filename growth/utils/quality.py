"""Pure quality gates: resolution, logos, edge cutoff, copyright, duplicates.

Frame checks operate on RGB bytes so tests do not need ffmpeg. The live scanner
in `media.py` fills the same inputs from ffmpeg, tesseract, and opencv.
"""

from __future__ import annotations

import math
import re

PIRACY = re.compile(
    r"(?i)\b("
    r"full\s+episode|full\s+movie|full\s+film|full\s+match|complete\s+episode|"
    r"entire\s+movie|hdrip|webrip|web-?dl|bluray|blu-ray|camrip|dvdrip|"
    r"torrent|watch\s+online\s+free|full\s+show"
    r")\b"
)
EPISODE_CODE = re.compile(r"(?i)\bS\d{1,2}\s*E\d{1,2}\b")
WATERMARK = re.compile(
    r"(?i)\b("
    r"tiktok|instagram|instagr\.am|capcut|douyin|likee|kwai|vigo|inshot|vmate|watermark|"
    r"icc|ipl|bcci|espn|nba|fifa|hotstar|jiocinema|jio\s*cinema|sonyliv|sony\s*liv|"
    r"star\s*sports|starsports|world\s*cup|disney\s*\+|willow\.?tv"
    r")\b|"
    r"(?<![\w])@[\w.]{3,}"
)
SAFETY = re.compile(r"(?i)\b(nude|nudes|naked|nsfw|porn|xxx|onlyfans|hentai|gore|beheading)\b")
PROMO = re.compile(
    r"(?i)\b("
    r"sponsored|sponsor|promotion|advertisement|"
    r"giveaway|discount|use\s+code|promo\s+code|link\s+in\s+bio|promo|"
    r"shop\s+now|buy\s+now|affiliate|limited\s+offer"
    r")\b|\bads?\b"
)

FEATURE_SECONDS = 90 * 60
CORNER_FRACTION = 0.16


def resolution_reason(width: int | None, height: int | None, minimum: int = 720) -> str | None:
    """Reject anything under 720p. The shorter side is the 720p measure for vertical video."""
    if not width or not height:
        return None
    if min(width, height) < minimum:
        return f"below {minimum}p ({width}x{height})"
    return None


def copyright_reason(title: str, description: str, duration: float | None, max_source_seconds: float) -> str | None:
    text = f"{title or ''}\n{description or ''}"
    if SAFETY.search(text):
        return "safety block"
    if PIRACY.search(text):
        return "copyright-risk full copy"
    if duration is not None and duration >= FEATURE_SECONDS:
        return "copyright-risk feature length"
    if duration is not None and duration > max_source_seconds:
        return "copyright-risk source too long"
    if EPISODE_CODE.search(text) and duration is not None and duration > 20 * 60:
        return "copyright-risk full episode"
    return None


def promo_reason(text: str) -> str | None:
    if PROMO.search(text or ""):
        return "ad or promo"
    return None


def watermark_reason(ocr_text: str) -> str | None:
    if WATERMARK.search(ocr_text or ""):
        return "third-party watermark or logo"
    return None


def _luma(frame: bytes, width: int, height: int) -> list[int]:
    expected = width * height * 3
    if len(frame) != expected or width < 4 or height < 4:
        raise ValueError("frame does not match width and height")
    values: list[int] = []
    for index in range(0, len(frame), 3):
        values.append((frame[index] + frame[index + 1] + frame[index + 2]) // 3)
    return values


def _box(width: int, height: int, name: str, fraction: float) -> tuple[int, int, int, int]:
    corner_w = max(2, int(width * fraction))
    corner_h = max(2, int(height * fraction))
    boxes = {
        "top-left": (0, 0, corner_w, corner_h),
        "top-right": (width - corner_w, 0, width, corner_h),
        "bottom-left": (0, height - corner_h, corner_w, height),
        "bottom-right": (width - corner_w, height - corner_h, width, height),
        "center": (int(width * 0.3), int(height * 0.3), int(width * 0.7), int(height * 0.7)),
    }
    return boxes[name]


def _ink(luma: list[int], width: int, height: int, box: tuple[int, int, int, int], baseline: int) -> float:
    x0, y0, x1, y1 = box
    x0 = max(0, min(width, x0))
    x1 = max(0, min(width, x1))
    y0 = max(0, min(height, y0))
    y1 = max(0, min(height, y1))
    total = 0
    ink = 0
    for y in range(y0, y1):
        row = y * width
        for x in range(x0, x1):
            total += 1
            if abs(luma[row + x] - baseline) > 40:
                ink += 1
    if total == 0:
        return 0.0
    return ink / total


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def corner_logo_regions(frame: bytes, width: int, height: int) -> list[str]:
    """Flag a corner whose ink is much hotter than the center. The subject in the center is ignored."""
    luma = _luma(frame, width, height)
    center = _box(width, height, "center", CORNER_FRACTION)
    center_pixels = []
    for y in range(center[1], center[3]):
        row = y * width
        center_pixels.extend(luma[row + x] for x in range(center[0], center[2]))
    baseline = _median(center_pixels)
    center_ink = _ink(luma, width, height, center, baseline)
    hits: list[str] = []
    for name in ("top-left", "top-right", "bottom-left", "bottom-right"):
        ink = _ink(luma, width, height, _box(width, height, name, CORNER_FRACTION), baseline)
        if ink >= 0.08 and ink >= center_ink * 2.5 + 0.04:
            hits.append(name)
    return hits


def _mean(luma: list[int], width: int, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    total = 0
    count = 0
    for y in range(y0, y1):
        row = y * width
        for x in range(x0, x1):
            total += luma[row + x]
            count += 1
    if count == 0:
        return 0.0
    return total / count


def _edge_density(luma: list[int], width: int, height: int, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    diffs = 0
    total = 0
    for y in range(y0, max(y0, y1 - 1)):
        row = y * width
        below = (y + 1) * width
        for x in range(x0, max(x0, x1 - 1)):
            total += 1
            here = luma[row + x]
            if abs(here - luma[row + x + 1]) > 28 or abs(here - luma[below + x]) > 28:
                diffs += 1
    if total == 0:
        return 0.0
    return diffs / total


def edge_cutoff(frame: bytes, width: int, height: int) -> bool:
    """True when content is sliced by the frame on three or more sides.

    Near-black bars are letterboxing, not cutoff. Those frames are padded, not rejected.
    """
    luma = _luma(frame, width, height)
    thickness = max(1, int(min(width, height) * 0.03))
    boxes = (
        (0, 0, width, thickness),
        (0, height - thickness, width, thickness),
        (0, 0, thickness, height),
        (width - thickness, 0, width, height),
    )
    busy = 0
    for box in boxes:
        if _mean(luma, width, box) > 24 and _edge_density(luma, width, height, box) > 0.18:
            busy += 1
    return busy >= 3


def letterbox(frame: bytes, width: int, height: int) -> bool:
    luma = _luma(frame, width, height)
    thickness = max(2, int(height * 0.08))
    top = _mean(luma, width, (0, 0, width, thickness))
    bottom = _mean(luma, width, (0, height - thickness, width, height))
    return top < 18 and bottom < 18


def choose_count(total: int, share: float, low: float, high: float) -> int:
    """Pick how many posts land in the primary lane, inside [low, high] when that band exists."""
    if total <= 0:
        return 0
    ideal = total * share
    lo = max(0, min(total, math.ceil(total * low - 1e-9)))
    hi = max(0, min(total, math.floor(total * high + 1e-9)))
    if lo > hi:
        return max(0, min(total, int(round(ideal))))
    ranked = sorted(range(lo, hi + 1), key=lambda count: (abs(count - ideal), -count))
    return ranked[0]


def split_share(total: int, share: float) -> tuple[int, int]:
    primary = int(total * share + 0.5)
    primary = max(0, min(total, primary))
    return primary, total - primary


def media_reasons(width: int, height: int, duration: float | None, ocr_text: str, corner_hits: list[str], cutoff: bool, scanners_unavailable: bool, minimum: int) -> list[str]:
    reasons: list[str] = []
    if scanners_unavailable:
        reasons.append("scanners unavailable")
        return reasons
    resolution = resolution_reason(width, height, minimum)
    if resolution:
        reasons.append(resolution)
    if not width or not height:
        reasons.append("missing dimensions")
    # a detected corner logo/watermark is a reject (never cropped around); Gemini double-checks
    if corner_hits:
        reasons.append(f"corner logo or watermark detected ({', '.join(corner_hits)})")
    if watermark_reason(ocr_text):
        reasons.append("third-party watermark or logo")
    if cutoff:
        reasons.append("content cut off at the frame edge")
    return reasons


def rgb_frame(width: int, height: int, color: tuple[int, int, int] = (30, 30, 30)) -> bytes:
    return bytes(color) * (width * height)


def paint_rect(
    frame: bytes,
    width: int,
    height: int,
    x: int,
    y: int,
    rect_w: int,
    rect_h: int,
    color: tuple[int, int, int],
) -> bytes:
    output = bytearray(frame)
    pixel = bytes(color)
    for row in range(max(0, y), min(height, y + rect_h)):
        for col in range(max(0, x), min(width, x + rect_w)):
            offset = (row * width + col) * 3
            output[offset : offset + 3] = pixel
    return bytes(output)


def paint_busy_border(width: int, height: int, thickness: int) -> bytes:
    frame = bytearray(rgb_frame(width, height, (40, 40, 40)))
    for y in range(height):
        for x in range(width):
            border = x < thickness or y < thickness or x >= width - thickness or y >= height - thickness
            if not border:
                continue
            tone = 255 if (x + y) % 2 == 0 else 0
            offset = (y * width + x) * 3
            frame[offset : offset + 3] = bytes((tone, tone, tone))
    return bytes(frame)
