"""Small RGBA PNG writer. Used for the Jackelyne placeholder wordmark."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

# 5x7 glyphs. 1 is an opaque pixel.
_FONT = {
    "J": (
        "00111",
        "00010",
        "00010",
        "00010",
        "00010",
        "10010",
        "01100",
    ),
    "A": (
        "01110",
        "10001",
        "10001",
        "11111",
        "10001",
        "10001",
        "10001",
    ),
    "C": (
        "01110",
        "10001",
        "10000",
        "10000",
        "10000",
        "10001",
        "01110",
    ),
    "K": (
        "10001",
        "10010",
        "10100",
        "11000",
        "10100",
        "10010",
        "10001",
    ),
    "Y": (
        "10001",
        "01010",
        "00100",
        "00100",
        "00100",
        "00100",
        "00100",
    ),
}


def encode_rgba_png(width: int, height: int, rgba: bytes) -> bytes:
    if len(rgba) != width * height * 4:
        raise ValueError("rgba byte length does not match width and height")

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    rows = []
    stride = width * 4
    for y in range(height):
        rows.append(b"\x00" + rgba[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )


def wordmark_png(text: str, scale: int = 8, pad: int = 16) -> bytes:
    glyphs = []
    for char in text:
        glyph = _FONT.get(char.upper())
        if glyph is None:
            raise ValueError(f"no glyph for {char!r}")
        glyphs.append(glyph)
    glyph_w = 5 * scale
    gap = scale
    width = pad * 2 + len(glyphs) * glyph_w + (len(glyphs) - 1) * gap
    height = pad * 2 + 7 * scale
    pixels = bytearray(width * height * 4)
    for index, glyph in enumerate(glyphs):
        origin_x = pad + index * (glyph_w + gap)
        origin_y = pad
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        x = origin_x + col * scale + sx
                        y = origin_y + row * scale + sy
                        offset = (y * width + x) * 4
                        pixels[offset : offset + 4] = b"\xff\xff\xff\xe6"
    return encode_rgba_png(width, height, bytes(pixels))


def write_wordmark(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wordmark_png(text))
