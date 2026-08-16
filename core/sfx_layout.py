"""Automatic line composition for sound effects inside editable boxes."""
from __future__ import annotations

from functools import lru_cache
import math
import re
from typing import Callable

from core.linguistic_composer import CJK_CLOSING, CJK_OPENING, detect_language


def _tokens(text: str, language: str) -> tuple[list[str], str]:
    normalized = " ".join(str(text).split())
    if language in {"zh", "ja"} or (language == "ko" and not re.search(r"\s", normalized)) or not re.search(r"\s", normalized):
        return list(normalized.replace(" ", "")), ""
    return normalized.split(), " "


def _balanced_partition(
    tokens: list[str], separator: str, line_count: int,
    measure: Callable[[str], float], language: str,
) -> list[str] | None:
    if not tokens or line_count < 1 or line_count > len(tokens):
        return None
    total_width = max(1.0, float(measure(separator.join(tokens))))
    target = total_width / line_count

    @lru_cache(maxsize=None)
    def solve(position: int, line: int):
        if position == len(tokens):
            return (0.0, ()) if line == line_count else None
        if line >= line_count or len(tokens) - position < line_count - line:
            return None
        best = None
        maximum_end = len(tokens) - (line_count - line - 1)
        for end in range(position + 1, maximum_end + 1):
            candidate = separator.join(tokens[position:end])
            if language in {"zh", "ja", "ko"} and separator == "" and end < len(tokens):
                if candidate[-1] in CJK_OPENING or tokens[end] in CJK_CLOSING:
                    continue
            tail = solve(end, line + 1)
            if tail is None:
                continue
            width = float(measure(candidate))
            cost = tail[0] + ((width - target) / target) ** 2
            result = (cost, (candidate, *tail[1]))
            if best is None or result[0] < best[0]:
                best = result
        return best

    result = solve(0, 0)
    return list(result[1]) if result is not None else None


def automatic_sfx_lines(
    text: str,
    available_width: float,
    available_height: float,
    measure: Callable[[str], float],
    line_height: float,
    language: str = "auto",
) -> list[str]:
    """Arrange text to match the box aspect without distorting its glyphs.

    Explicit line breaks always win. Otherwise several balanced arrangements
    are evaluated and the block whose proportions best match the box is used.
    """
    value = str(text)
    if "\n" in value or "\r" in value:
        return value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = " ".join(value.split())
    if not normalized:
        return [""]
    detected = detect_language(normalized, language)
    tokens, separator = _tokens(normalized, detected)
    if len(tokens) <= 1:
        return [normalized]

    width = max(1.0, float(available_width))
    height = max(1.0, float(available_height))
    row_height = max(1.0, float(line_height))
    max_lines = min(8, len(tokens), max(1, int(height / max(1.0, row_height * 0.62))))
    box_aspect = width / height
    best: tuple[float, list[str]] | None = None
    for count in range(1, max_lines + 1):
        lines = _balanced_partition(tokens, separator, count, measure, detected)
        if not lines:
            continue
        line_widths = [max(1.0, float(measure(line))) for line in lines]
        block_width = max(line_widths)
        block_height = row_height * len(lines)
        fit = min(1.0, width / block_width, height / block_height)
        rendered_aspect = max(1e-6, block_width / block_height)
        aspect_cost = abs(math.log(max(1e-6, rendered_aspect / box_aspect)))
        occupied = min(1.0, block_width * fit / width) * min(1.0, block_height * fit / height)
        ragged = (max(line_widths) - min(line_widths)) / max(line_widths) if len(lines) > 1 else 0.0
        score = aspect_cost + (1.0 - occupied) * 0.36 + ragged * 0.18 + (count - 1) * 0.015
        if best is None or score < best[0]:
            best = (score, lines)
    return best[1] if best is not None else [normalized]
