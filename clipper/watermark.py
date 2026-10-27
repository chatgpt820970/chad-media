"""Find burned-in logos and platform marks, then cover them or skip the clip.

The campaign logo is not part of this step. Callers apply it only after a clip
comes back clean.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

HANDLE = re.compile(r"@[A-Za-z0-9._]{2,30}")
PLATFORM_MARK = re.compile(
    r"\b(tiktok|instagram|instagr\.am|capcut|douyin|likee|kwai|reels)\b",
    re.IGNORECASE,
)

EDGE_REGIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "bottom-band")


def scan_text(text: str) -> list[str]:
    if not text:
        return []
    found = HANDLE.findall(text)
    found.extend(match.group(0) for match in PLATFORM_MARK.finditer(text))
    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def _regions(width: int, height: int, fraction: float) -> dict[str, tuple[int, int, int, int]]:
    corner_w = max(1, int(width * fraction))
    corner_h = max(1, int(height * fraction))
    return {
        "top-left": (0, 0, corner_w, corner_h),
        "top-right": (width - corner_w, 0, width, corner_h),
        "bottom-left": (0, height - corner_h, corner_w, height),
        "bottom-right": (width - corner_w, height - corner_h, width, height),
        "bottom-band": (
            int(width * 0.12),
            int(height * 0.82),
            int(width * 0.88),
            int(height * 0.98),
        ),
        "center": (int(width * 0.30), int(height * 0.30), int(width * 0.70), int(height * 0.70)),
    }


def _luma(frame: bytes, width: int) -> list[int]:
    values: list[int] = []
    for index in range(0, len(frame), 3):
        red, green, blue = frame[index], frame[index + 1], frame[index + 2]
        values.append((red + green + blue) // 3)
    if len(values) % width:
        raise ValueError("frame width does not match the buffer")
    return values


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _ink(luma: list[int], width: int, box: tuple[int, int, int, int], baseline: int) -> float:
    x0, y0, x1, y1 = box
    total = 0
    ink = 0
    height = len(luma) // width
    y0 = max(0, min(height, y0))
    y1 = max(0, min(height, y1))
    x0 = max(0, min(width, x0))
    x1 = max(0, min(width, x1))
    for y in range(y0, y1):
        row = y * width
        for x in range(x0, x1):
            total += 1
            if abs(luma[row + x] - baseline) > 40:
                ink += 1
    if total == 0:
        return 0.0
    return ink / total


def detect_buffer(frame: bytes, width: int, height: int, settings: dict | None = None, ocr_text: dict | None = None) -> list[dict]:
    """Return watermark findings. Edge findings can be covered. A center mark cannot."""
    settings = settings or {}
    fraction = float(settings.get("corner_fraction") or 0.16)
    trigger = float(settings.get("ink_ratio_trigger") or 0.05)
    boxes = _regions(width, height, fraction)
    luma = _luma(frame, width)
    center_box = boxes["center"]
    center_pixels = []
    for y in range(center_box[1], center_box[3]):
        row = y * width
        center_pixels.extend(luma[row + x] for x in range(center_box[0], center_box[2]))
    baseline = _median(center_pixels)
    center_ink = _ink(luma, width, center_box, baseline)
    findings: list[dict] = []
    if center_ink >= trigger:
        findings.append(
            {"region": "center", "kind": "visual", "coverable": False, "ink": round(center_ink, 4)}
        )
    for name in EDGE_REGIONS:
        ink = _ink(luma, width, boxes[name], baseline)
        if ink >= trigger and ink >= center_ink * 2.5 + 0.02:
            findings.append({"region": name, "kind": "visual", "coverable": True, "ink": round(ink, 4)})
    for region, text in (ocr_text or {}).items():
        marks = scan_text(text)
        if not marks:
            continue
        coverable = region in EDGE_REGIONS
        findings.append(
            {
                "region": region,
                "kind": "ocr",
                "coverable": coverable,
                "marks": marks,
            }
        )
    return findings


def cover_regions(frame: bytes, width: int, height: int, regions: list[str], settings: dict | None = None) -> bytes:
    """Paint flagged edges with the center color so a corner logo is covered."""
    settings = settings or {}
    fraction = float(settings.get("corner_fraction") or 0.16)
    boxes = _regions(width, height, fraction)
    luma = _luma(frame, width)
    center = boxes["center"]
    samples = []
    for y in range(center[1], center[3]):
        row = y * width
        samples.extend(luma[row + x] for x in range(center[0], center[2]))
    tone = _median(samples)
    output = bytearray(frame)
    for name in regions:
        box = boxes.get(name)
        if box is None or name == "center":
            continue
        x0, y0, x1, y1 = box
        for y in range(y0, y1):
            for x in range(x0, x1):
                offset = (y * width + x) * 3
                output[offset : offset + 3] = bytes((tone, tone, tone))
    return bytes(output)


def resolve_watermark(frame: bytes, width: int, height: int, settings: dict | None = None, ocr_text: dict | None = None) -> dict:
    """Cover edge marks once. Skip the clip if a mark is still there or sits in the center."""
    settings = settings or {}
    policy = settings.get("policy") or "cover_then_skip"
    findings = detect_buffer(frame, width, height, settings, ocr_text=ocr_text)
    if not findings:
        return {"status": "clean", "findings": [], "frame": frame}
    if policy == "skip" or any(not item["coverable"] for item in findings):
        return {"status": "skipped", "findings": findings, "frame": frame}
    covered = cover_regions(
        frame,
        width,
        height,
        [item["region"] for item in findings if item["coverable"]],
        settings,
    )
    again = detect_buffer(covered, width, height, settings)
    if again:
        return {"status": "skipped", "findings": findings + again, "frame": covered}
    return {"status": "covered", "findings": findings, "frame": covered}


def solid_frame(width: int, height: int, color: tuple[int, int, int] = (32, 32, 32)) -> bytes:
    pixel = bytes(color)
    return pixel * (width * height)


def paint_rect(
    frame: bytes,
    width: int,
    height: int,
    x: int,
    y: int,
    rect_w: int,
    rect_h: int,
    color: tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    output = bytearray(frame)
    pixel = bytes(color)
    for row in range(y, min(height, y + rect_h)):
        for col in range(x, min(width, x + rect_w)):
            offset = (row * width + col) * 3
            output[offset : offset + 3] = pixel
    return bytes(output)


def synthetic_frame(kind: str, width: int = 180, height: int = 320) -> bytes:
    frame = solid_frame(width, height)
    if kind in (None, "", "clean"):
        return frame
    if kind == "corner_logo":
        return paint_rect(frame, width, height, 4, 4, 36, 28)
    if kind == "full_frame":
        return paint_rect(frame, width, height, int(width * 0.35), int(height * 0.40), 50, 36)
    raise ValueError(f"unknown watermark fixture {kind}")


def ocr_image(path: Path) -> str:
    binary = shutil.which("tesseract")
    if not binary:
        return ""
    result = subprocess.run(
        [binary, str(path), "stdout", "--psm", "6"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout or ""
