"""Shared, renderer-independent text layout decisions.

The canvas and the exporter use different rasterizers (Qt and Pillow), but
they must not make independent decisions about line breaks or point size.
This module owns those decisions and stores a small snapshot on every layer.
"""
from __future__ import annotations

from hashlib import sha1
from typing import Callable

from core.linguistic_composer import detect_language, linguistic_wrap


LAYOUT_STYLE_KEYS = (
    "font_family", "font_file", "font_size", "font_weight", "italic",
    "text_case", "auto_fit", "line_spacing", "language", "hyphenation",
    "orphan_control", "hanging_punctuation", "scale_x", "scale_y",
    "text_margin", "vertical_text", "alignment", "vertical_alignment",
    "balloon_fit", "balloon_shape", "balloon_padding", "auto_scale",
    "max_horizontal_compression", "stroke_width", "stroke2_enabled",
    "stroke2_width", "fit_once",
)


def layout_signature(text: str, width: int | float, height: int | float, style: dict) -> str:
    """Return a stable key for a composition snapshot.

    Effects deliberately do not participate: changing a glow or gradient must
    not invalidate line breaking. Geometry and all typographic measurements do.
    """
    payload = (
        str(text), round(float(width), 3), round(float(height), 3),
        tuple((key, repr(style.get(key))) for key in LAYOUT_STYLE_KEYS),
    )
    return sha1(repr(payload).encode("utf-8", "surrogatepass")).hexdigest()


def _minimum_lines(text: str) -> int:
    return max(1, len(str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")))


def _maximum_lines(text: str) -> int:
    value = str(text)
    # A practical guard against pathological pasted chapters. A box never
    # needs hundreds of visual rows; overflow is a better answer in that case.
    return max(_minimum_lines(value), min(96, max(len(value), len(value.split()))))


def fit_rectangular_text(
    text: str,
    width: float,
    height: float,
    requested_size: int,
    minimum_size: int,
    auto_fit: bool,
    line_height_for_size: Callable[[int], float],
    measure_for_size: Callable[[int, str], float],
    *,
    language: str = "auto",
    hyphenate: bool = True,
    orphan_control: bool = True,
    hanging_punctuation: bool = True,
    signature: str = "",
) -> dict:
    """Compose a rectangular layer while preserving every explicit newline.

    In manual mode the requested font size is immutable. The function may wrap
    a paragraph, but a hard break is always a hard boundary and is never joined
    to an adjacent line. Automatic mode only changes the size when explicitly
    enabled by the user or by the one-shot *Ajustar ahora* action.
    """
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    available_width = max(1.0, float(width))
    available_height = max(1.0, float(height))
    requested = max(int(minimum_size), int(requested_size))
    first_line_count = _minimum_lines(value)
    last_line_count = _maximum_lines(value)
    measured: dict[tuple[int, str], float] = {}

    def measure(size: int, line: str) -> float:
        key = (int(size), str(line))
        if key not in measured:
            measured[key] = float(measure_for_size(int(size), str(line)))
        return measured[key]

    def attempt(size: int, require_height: bool) -> dict | None:
        line_height = max(1.0, float(line_height_for_size(int(size))))
        height_limit = max(first_line_count, int(available_height // line_height))
        max_lines = min(last_line_count, height_limit) if require_height else last_line_count
        best: tuple[float, dict] | None = None
        for count in range(first_line_count, max_lines + 1):
            lines = linguistic_wrap(
                value, [available_width] * count,
                lambda line, current=size: measure(current, line),
                language=language, hyphenate=hyphenate,
                orphan_control=orphan_control, hanging=hanging_punctuation,
            )
            if lines is None:
                continue
            used_height = line_height * len(lines)
            overflow = used_height > available_height + 0.5
            widths = [measure(size, line) for line in lines]
            overflow = overflow or any(line_width > available_width + 0.5 for line_width in widths)
            ragged = sum(
                ((available_width - min(available_width, line_width)) / available_width) ** 2
                for line_width in widths
            ) / max(1, len(widths))
            # Prefer fewer rows when quality is similar; this prevents a wide
            # dialogue box from being fragmented into many tiny phrases.
            score = ragged + len(lines) * 0.006
            result = {
                "text": "\n".join(lines), "lines": list(lines),
                "font_size": int(size), "line_height": line_height,
                "overflow": bool(overflow), "scale_x": 1.0, "scale_y": 1.0,
                "language": detect_language(value, language),
                "signature": signature,
            }
            if not require_height:
                # Manual editing only needs the first legal wrapping. This is
                # the smallest row count and avoids evaluating dozens of
                # invisible alternatives on every keystroke.
                return result
            if best is None or score < best[0]:
                best = (score, result)
        return best[1] if best is not None else None

    if auto_fit:
        low, high = max(6, int(minimum_size)), requested
        best_layout = None
        while low <= high:
            size = (low + high) // 2
            candidate = attempt(size, True)
            if candidate is not None and not candidate["overflow"]:
                best_layout = candidate
                low = size + 1
            else:
                high = size - 1
        if best_layout is not None:
            return best_layout
    else:
        fixed = attempt(requested, False)
        if fixed is not None:
            return fixed

    lines = value.split("\n") or [value]
    return {
        "text": "\n".join(lines), "lines": lines,
        "font_size": requested,
        "line_height": max(1.0, float(line_height_for_size(requested))),
        "overflow": True, "scale_x": 1.0, "scale_y": 1.0,
        "language": detect_language(value, language), "signature": signature,
    }


def valid_snapshot(region: dict, text: str, style: dict) -> dict | None:
    snapshot = region.get("layout_snapshot")
    if not isinstance(snapshot, dict):
        return None
    expected = layout_signature(text, region.get("width", 1), region.get("height", 1), style)
    if snapshot.get("signature") != expected or not isinstance(snapshot.get("lines"), list):
        return None
    return dict(snapshot)


def balloon_layout_signature(region: dict, text: str, style: dict) -> str:
    """Include the image location, since moving a box changes its balloon mask."""
    base = layout_signature(text, region.get("width", 1), region.get("height", 1), style)
    position = (round(float(region.get("x", 0)), 3), round(float(region.get("y", 0)), 3))
    return sha1(repr((base, position)).encode("utf-8", "surrogatepass")).hexdigest()


def valid_balloon_snapshot(region: dict, text: str, style: dict) -> dict | None:
    snapshot = region.get("balloon_layout_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("overflow"):
        return None
    if snapshot.get("signature") != balloon_layout_signature(region, text, style):
        return None
    lines, intervals = snapshot.get("lines"), snapshot.get("intervals")
    if not isinstance(lines, list) or not isinstance(intervals, list) or len(lines) != len(intervals):
        return None
    if not lines or any(not isinstance(line, str) for line in lines):
        return None
    layout_rect = snapshot.get("layout_rect", (
        region.get("x", 0), region.get("y", 0), region.get("width", 1), region.get("height", 1),
    ))
    if not isinstance(layout_rect, (list, tuple)) or len(layout_rect) != 4:
        return None
    try:
        width = float(layout_rect[2])
        if width <= 0 or float(layout_rect[3]) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    if any(
        not isinstance(interval, (list, tuple)) or len(interval) != 2
        or not (0 <= float(interval[0]) < float(interval[1]) <= width)
        for interval in intervals
    ):
        return None
    if not (4 <= int(snapshot.get("font_size", 0)) <= 500):
        return None
    return dict(snapshot)
