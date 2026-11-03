"""Deterministic machine-verifiable vision benchmark fixtures.

The images are generated from stdlib only so the benchmark never depends on a
remote image, font package, or mutable fixture. Prompts reveal the output schema
but not the answer values that must be read from pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import struct
import zlib

from amodex.providers.capabilities import ModelCapability

VISION_SUITE_VERSION = "vision-live-v1"
VISION_CASE_PASS_SCORE = 0.80
VISION_PROMOTION_AGGREGATE = 0.85
VISION_PROMOTION_PASS_RATE = 0.80


@dataclass(frozen=True, slots=True)
class VisionBenchmarkCase:
    case_id: str
    capability: ModelCapability
    prompt: str
    expected: dict[str, object]
    image_png: bytes
    weight: float = 1.0


_FONT_5X7: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "4": ("00100", "01100", "10100", "11111", "00100", "00100", "00100"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    " ": ("00000",) * 7,
}


class _Canvas:
    def __init__(self, width: int = 512, height: int = 320) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray([255, 255, 255] * width * height)

    def _set(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset : offset + 3] = bytes(rgb)

    def rect(self, x: int, y: int, w: int, h: int, rgb: tuple[int, int, int]) -> None:
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                self._set(xx, yy, rgb)

    def circle(self, cx: int, cy: int, radius: int, rgb: tuple[int, int, int]) -> None:
        r2 = radius * radius
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                    self._set(x, y, rgb)

    def triangle(self, cx: int, top_y: int, size: int, rgb: tuple[int, int, int]) -> None:
        for dy in range(size):
            half = int((dy / max(size - 1, 1)) * (size / 2))
            y = top_y + dy
            for x in range(cx - half, cx + half + 1):
                self._set(x, y, rgb)

    def arrow_right(self, x: int, y: int, length: int, thickness: int, rgb: tuple[int, int, int]) -> None:
        head = max(24, thickness * 3)
        self.rect(x, y - thickness // 2, length - head, thickness, rgb)
        tip_x = x + length
        base_x = tip_x - head
        for dx in range(head + 1):
            half = int((1.0 - dx / max(head, 1)) * head)
            xx = base_x + dx
            for yy in range(y - half, y + half + 1):
                self._set(xx, yy, rgb)

    def text(self, x: int, y: int, value: str, scale: int = 8, rgb: tuple[int, int, int] = (18, 18, 18)) -> None:
        cursor = x
        for char in value:
            glyph = _FONT_5X7[char]
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        self.rect(cursor + gx * scale, y + gy * scale, scale, scale, rgb)
            cursor += 6 * scale

    def png(self) -> bytes:
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)
            start = y * stride
            raw.extend(self.pixels[start : start + stride])

        def chunk(kind: bytes, payload: bytes) -> bytes:
            body = kind + payload
            return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def _case_position_color() -> VisionBenchmarkCase:
    c = _Canvas()
    c.circle(90, 78, 42, (220, 35, 35))
    c.rect(390, 220, 78, 78, (35, 80, 220))
    return VisionBenchmarkCase(
        case_id="vision-position-color-v1",
        capability=ModelCapability.VISION,
        prompt=(
            "Inspect the image pixels. Return only JSON with exactly these keys: "
            "red_circle and blue_square. Each value must be one of top-left, top-right, "
            "bottom-left, bottom-right, or center, describing the object's position."
        ),
        expected={"red_circle": "top-left", "blue_square": "bottom-right"},
        image_png=c.png(),
        weight=1.1,
    )


def _case_ocr() -> VisionBenchmarkCase:
    c = _Canvas()
    c.text(52, 122, "AMODEX 4827", scale=7)
    return VisionBenchmarkCase(
        case_id="vision-ocr-v1",
        capability=ModelCapability.VISION,
        prompt="Read the large text in the image. Return only JSON with exactly one key named text.",
        expected={"text": "AMODEX 4827"},
        image_png=c.png(),
        weight=1.3,
    )


def _case_count() -> VisionBenchmarkCase:
    c = _Canvas()
    green = (30, 170, 70)
    orange = (235, 125, 25)
    for x in (100, 230, 360):
        c.triangle(x, 58, 72, green)
    for x in (165, 330):
        c.circle(x, 235, 34, orange)
    return VisionBenchmarkCase(
        case_id="vision-count-v1",
        capability=ModelCapability.VISION,
        prompt=(
            "Count the requested colored shapes. Return only JSON with integer keys "
            "green_triangles and orange_circles."
        ),
        expected={"green_triangles": 3, "orange_circles": 2},
        image_png=c.png(),
        weight=1.2,
    )


def _case_direction() -> VisionBenchmarkCase:
    c = _Canvas()
    c.rect(120, 54, 270, 34, (120, 45, 180))
    c.arrow_right(110, 205, 290, 26, (25, 25, 25))
    return VisionBenchmarkCase(
        case_id="vision-direction-v1",
        capability=ModelCapability.VISION,
        prompt=(
            "Inspect the arrow and purple bar. Return only JSON with keys arrow and purple_bar. "
            "arrow must be left, right, up, or down. purple_bar must be above-arrow or below-arrow."
        ),
        expected={"arrow": "right", "purple_bar": "above-arrow"},
        image_png=c.png(),
        weight=1.1,
    )


def _case_spatial_relation() -> VisionBenchmarkCase:
    c = _Canvas()
    yellow = (238, 200, 40)
    purple = (135, 55, 190)
    cyan = (30, 180, 205)
    c.circle(150, 112, 38, yellow)
    c.rect(300, 74, 76, 76, purple)
    c.circle(150, 245, 34, cyan)
    return VisionBenchmarkCase(
        case_id="vision-spatial-relation-v1",
        capability=ModelCapability.VISION,
        prompt=(
            "Compare the colored shapes. Return only JSON with keys purple_square and cyan_circle. "
            "Use one relation from left-of-yellow-circle, right-of-yellow-circle, "
            "above-yellow-circle, or below-yellow-circle for each value."
        ),
        expected={
            "purple_square": "right-of-yellow-circle",
            "cyan_circle": "below-yellow-circle",
        },
        image_png=c.png(),
        weight=1.3,
    )


def vision_benchmark_cases() -> tuple[VisionBenchmarkCase, ...]:
    return (
        _case_position_color(),
        _case_ocr(),
        _case_count(),
        _case_direction(),
        _case_spatial_relation(),
    )


def score_vision_case(case: VisionBenchmarkCase, text: str) -> tuple[float, bool, dict[str, object]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return 0.0, False, {"checker": "vision-json-fields-v1", "reason": "invalid-json"}
    if not isinstance(parsed, dict):
        return 0.0, False, {"checker": "vision-json-fields-v1", "reason": "not-object"}

    def normalize(value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.strip().casefold().split())
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return int(value) if float(value).is_integer() else float(value)
        return value

    matched = 0
    field_results: dict[str, bool] = {}
    for key, expected in case.expected.items():
        ok = key in parsed and normalize(parsed[key]) == normalize(expected)
        field_results[key] = ok
        matched += int(ok)
    score = matched / max(len(case.expected), 1)
    passed = score >= VISION_CASE_PASS_SCORE
    return score, passed, {
        "checker": "vision-json-fields-v1",
        "matched_fields": matched,
        "total_fields": len(case.expected),
        "field_results": field_results,
        "unexpected_fields": sorted(str(key) for key in parsed.keys() if key not in case.expected)[:16],
    }
