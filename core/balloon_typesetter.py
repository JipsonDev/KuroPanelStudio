"""Speech-balloon interior detection and balanced line composition."""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable

import cv2
import numpy as np

from core.linguistic_composer import linguistic_wrap
from core.balloon_geometry import enclosed_balloon_mask


def effective_balloon_padding(width: int, height: int, requested: int, stroke: int = 0) -> int:
    """Keep glyphs clear of the outline at both small and full page resolutions."""
    shorter = max(1, min(int(width), int(height)))
    minimum = max(2, int(round(shorter * 0.055)), int(stroke) * 2 + 4)
    return min(max(int(requested), minimum), max(2, shorter // 4))


def usable_mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    """Return the actual usable interior bounds, with exclusive right/bottom."""
    binary = np.asarray(mask) > 0
    rows = np.flatnonzero(np.any(binary, axis=1))
    columns = np.flatnonzero(np.any(binary, axis=0))
    if not len(rows) or not len(columns):
        return 0, 0, binary.shape[1], binary.shape[0]
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def balloon_search_rect(
    page_width: int, page_height: int, box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Search around an OCR box without treating that box as the balloon edge."""
    x, y, width, height = (int(value) for value in box)
    horizontal = max(96, width * 2, height * 2)
    vertical = max(96, height * 3, width)
    left = max(0, x - horizontal)
    top = max(0, y - vertical)
    right = min(int(page_width), x + width + horizontal)
    bottom = min(int(page_height), y + height + vertical)
    return left, top, max(1, right - left), max(1, bottom - top)


def detect_full_balloon(
    search_rgb: np.ndarray, text_box: tuple[int, int, int, int], padding: int,
) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
    """Find a closed balloon containing the box centre in a larger page crop.

    Coordinates are relative to ``search_rgb``. Return None when the outline
    is uncertain so callers can keep their existing local-box fallback.
    """
    height, width = search_rgb.shape[:2]
    x, y, box_width, box_height = (int(value) for value in text_box)
    if min(width, height, box_width, box_height) < 12:
        return None
    # The OCR rectangle can touch an outline. Its central half is a much more
    # reliable seed, and selecting it also disambiguates adjacent balloons.
    seed = np.zeros((height, width), np.uint8)
    inset_x = max(1, box_width // 4)
    inset_y = max(1, box_height // 4)
    x1, y1 = max(0, x + inset_x), max(0, y + inset_y)
    x2, y2 = min(width, x + box_width - inset_x), min(height, y + box_height - inset_y)
    if x2 <= x1 or y2 <= y1:
        return None
    seed[y1:y2, x1:x2] = 255
    enclosed = enclosed_balloon_mask(search_rgb, seed)
    if enclosed is None:
        return None
    left, top, right, bottom = usable_mask_bounds(enclosed)
    if right - left < 12 or bottom - top < 12:
        return None
    mask = enclosed[top:bottom, left:right].copy()
    inset = max(1, int(padding))
    mask = cv2.erode(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (inset * 2 + 1, inset * 2 + 1)),
        borderType=cv2.BORDER_CONSTANT, borderValue=0,
    )
    if not np.any(mask):
        return None
    return (left, top, right - left, bottom - top), mask


def text_layout_geometry(
    search_rgb: np.ndarray,
    text_box: tuple[int, int, int, int],
    padding: int,
    manual_box: bool = False,
) -> tuple[tuple[int, int, int, int], np.ndarray]:
    """Resolve the layout area relative to a page crop.

    An untouched OCR box may use the full balloon. Once a user moves or
    resizes the box, the same balloon mask is clipped to that box so the
    handles actually control where the text can be drawn.
    """
    x, y, width, height = (int(value) for value in text_box)
    full = detect_full_balloon(search_rgb, text_box, padding)
    if full is not None and not manual_box:
        return full
    if full is not None:
        (bx, by, bw, bh), full_mask = full
        left, top = max(x, bx), max(y, by)
        right, bottom = min(x + width, bx + bw), min(y + height, by + bh)
        if right > left and bottom > top:
            constrained = np.zeros((height, width), np.uint8)
            constrained[top - y:bottom - y, left - x:right - x] = full_mask[
                top - by:bottom - by, left - bx:right - bx
            ]
            constrained = cv2.bitwise_and(constrained, _safe_rectangle(height, width, padding))
            if np.count_nonzero(constrained) >= max(24, width * height * .08):
                return (x, y, width, height), constrained
    local = search_rgb[y:y + height, x:x + width]
    return (x, y, width, height), detect_balloon_interior(local, padding)


def detect_balloon_interior(rgb: np.ndarray, padding: int = 8) -> np.ndarray:
    """Return the connected, background-like area surrounding the box centre.

    The text box is only a search window. Flooding from several central seeds
    prevents impact rays, tails and nearby artwork from becoming usable text
    area. A conservative rectangular fallback keeps ordinary captions usable.
    """
    source = np.ascontiguousarray(rgb, dtype=np.uint8)
    if source.ndim != 3 or source.shape[2] < 3:
        raise ValueError("La imagen del globo debe ser RGB.")
    height, width = source.shape[:2]
    if width < 12 or height < 12:
        return _safe_rectangle(height, width, padding)

    enclosed = enclosed_balloon_mask(source)
    if enclosed is not None:
        inset = max(0, int(padding))
        if inset:
            enclosed = cv2.erode(
                enclosed, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (inset * 2 + 1, inset * 2 + 1)),
                borderType=cv2.BORDER_CONSTANT, borderValue=0,
            )
        if np.any(enclosed):
            return enclosed

    lab = cv2.cvtColor(source[:, :, :3], cv2.COLOR_RGB2LAB).astype(np.float32)
    cy, cx = height // 2, width // 2
    radius_y, radius_x = max(2, height // 12), max(2, width // 12)
    centre = lab[max(0, cy - radius_y):min(height, cy + radius_y + 1), max(0, cx - radius_x):min(width, cx + radius_x + 1)]
    reference = np.median(centre.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(lab - reference, axis=2)
    central_distance = distance[max(0, cy - radius_y):min(height, cy + radius_y + 1), max(0, cx - radius_x):min(width, cx + radius_x + 1)]
    tolerance = max(11.0, min(42.0, float(np.percentile(central_distance, 90)) + 12.0))
    candidate = np.where(distance <= tolerance, 255, 0).astype(np.uint8)

    gray = cv2.cvtColor(source[:, :, :3], cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 45, 135)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    candidate[edges > 0] = 0
    candidate = cv2.morphologyEx(
        candidate, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2,
    )

    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    selected_label = 0
    best_score = -1.0
    seed_points = ((cx, cy), (cx - width // 12, cy), (cx + width // 12, cy))
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        contains_seed = any(
            0 <= sy < height and 0 <= sx < width and int(labels[sy, sx]) == label
            for sx, sy in seed_points
        )
        if not contains_seed:
            continue
        score = area
        if score > best_score:
            selected_label, best_score = label, score
    if selected_label == 0:
        return _safe_rectangle(height, width, padding)
    interior = np.where(labels == selected_label, 255, 0).astype(np.uint8)
    if np.count_nonzero(interior) < width * height * 0.22:
        return _safe_rectangle(height, width, padding)

    # Original glyphs are holes in the background-colour component, but they
    # describe content to replace, not forbidden geometry for the new text.
    # Filling holes preserves the outer balloon contour while recovering the
    # full usable interior behind the source lettering.
    contours, _ = cv2.findContours(interior, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        filled = np.zeros_like(interior)
        cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
        interior = filled

    inset = max(1, int(padding))
    if inset:
        kernel_size = inset * 2 + 1
        interior = cv2.erode(
            interior, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
            iterations=1, borderType=cv2.BORDER_CONSTANT, borderValue=0,
        )
    return interior if np.any(interior) else _safe_rectangle(height, width, padding)


def _safe_rectangle(height: int, width: int, padding: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    inset = max(1, min(int(padding), max(1, min(width, height) // 4)))
    if width > inset * 2 and height > inset * 2:
        mask[inset:height - inset, inset:width - inset] = 255
    return mask


def symmetric_shape_intervals(
    shape_type: str, width: int, height: int, line_count: int,
    line_height: float, padding: int = 8,
) -> list[tuple[int, int]]:
    """Compute perfectly centered, symmetric line width boundaries for a geometric shape."""
    count = max(1, int(line_count))
    total_height = max(1.0, float(line_height) * count)
    if total_height > height:
        return []
    top = (height - total_height) / 2.0
    cy = height / 2.0
    cx = width / 2.0
    effective_pad = max(2, min(int(padding), max(2, min(width, height) // 5)))
    radius_y = max(1.0, (height / 2.0) - effective_pad)
    radius_x = max(1.0, (width / 2.0) - effective_pad)
    intervals: list[tuple[int, int]] = []

    shape = str(shape_type).lower()
    for line in range(count):
        centre_y = top + (line + 0.5) * float(line_height)
        dy = abs(centre_y - cy) / radius_y

        if shape in ("ellipse", "oval"):
            factor = (1.0 - min(0.999, dy * dy)) ** 0.5 if dy < 1.0 else 0.0
        elif shape in ("diamond", "shout", "rhombus"):
            factor = max(0.0, 1.0 - min(1.0, dy))
        else:  # rectangle or default
            factor = 1.0

        half_w = max(8.0, radius_x * factor)
        left = max(0, int(round(cx - half_w)))
        right = min(width, width - left)
        if right <= left + 4:
            return []
        intervals.append((left, right))
    return intervals


def line_intervals(
    mask: np.ndarray, line_count: int, line_height: float,
    bounds: tuple[int, int, int, int] | None = None,
    row_limits: tuple[np.ndarray, np.ndarray] | None = None,
) -> list[tuple[int, int]]:
    """Measure full glyph bands around the detected interior's own centre."""
    binary = np.asarray(mask) > 0
    height, width = binary.shape
    left_bound, top_bound, right_bound, bottom_bound = bounds or usable_mask_bounds(mask)
    centre_x = (left_bound + right_bound) / 2.0
    centre_y = (top_bound + bottom_bound) / 2.0
    left_by_row, right_by_row = row_limits or _central_row_limits(binary, centre_x)
    count = max(1, int(line_count))
    total_height = max(1.0, float(line_height) * count)
    if total_height > bottom_bound - top_bound:
        return []
    top = centre_y - total_height / 2.0
    intervals: list[tuple[int, int]] = []
    band_half = max(1, int(math.ceil(float(line_height) * 0.48)))
    for line in range(count):
        centre = top + (line + 0.5) * float(line_height)
        y1, y2 = max(0, int(round(centre)) - band_half), min(height, int(round(centre)) + band_half + 1)
        if y1 < top_bound or y2 > bottom_bound:
            return []
        left_val = int(np.max(left_by_row[y1:y2]))
        right_val = int(np.min(right_by_row[y1:y2]))
        if right_val - left_val < max(8, (right_bound - left_bound) * 0.08):
            return []
        half_width = min(centre_x - left_val, right_val - centre_x)
        sym_left = max(0, int(math.ceil(centre_x - half_width)))
        sym_right = min(width, int(math.floor(centre_x + half_width)))
        if sym_right - sym_left < 8:
            return []
        intervals.append((sym_left, sym_right))
    return intervals


def _central_row_limits(binary: np.ndarray, centre_x: float) -> tuple[np.ndarray, np.ndarray]:
    """Measure the safe central run once, then reuse it for every line band."""
    height, width = binary.shape
    middle = max(0, min(width - 1, int(round(centre_x - 0.5))))
    left_half = binary[:, :middle + 1][:, ::-1]
    right_half = binary[:, middle:]
    left_break = np.argmax(~left_half, axis=1)
    right_break = np.argmax(~right_half, axis=1)
    left = middle - np.where(np.all(left_half, axis=1), middle + 1, left_break) + 1
    right = middle + np.where(np.all(right_half, axis=1), width - middle, right_break)
    missing_centre = ~binary[:, middle]
    left[missing_centre] = width
    right[missing_centre] = 0
    return left, right


def _tokens(text: str) -> tuple[list[str], str]:
    normalized = " ".join(str(text).replace("\n", " ").split())
    words = normalized.split(" ") if normalized else []
    if len(words) > 1:
        return words, " "
    return list(normalized), ""


def balanced_wrap(
    text: str,
    widths: list[float],
    measure: Callable[[str], float],
) -> list[str] | None:
    """Globally balance breaks while respecting each line's local width."""
    tokens, separator = _tokens(text)
    line_total = len(widths)
    if not tokens or line_total <= 0:
        return [] if not tokens else None

    @lru_cache(maxsize=None)
    def solve(position: int, line: int):
        if position == len(tokens):
            return (0.0, ()) if line == line_total else None
        if line >= line_total:
            return None
        remaining_tokens = len(tokens) - position
        remaining_lines = line_total - line
        if remaining_tokens < remaining_lines:
            return None
        best = None
        width = max(1.0, float(widths[line]))
        for end in range(position + 1, len(tokens) + 1):
            if len(tokens) - end < remaining_lines - 1:
                break
            candidate = separator.join(tokens[position:end])
            used = float(measure(candidate))
            if used > width:
                break
            tail = solve(end, line + 1)
            if tail is None:
                continue
            empty_ratio = max(0.0, (width - used) / width)
            cost = tail[0] + empty_ratio * empty_ratio
            if end == len(tokens):
                cost *= 0.72
            if end - position == 1 and len(candidate) <= 2:
                cost += 0.18
            result = (cost, (candidate, *tail[1]))
            if best is None or result[0] < best[0]:
                best = result
        return best

    solved = solve(0, 0)
    return list(solved[1]) if solved is not None else None


def fit_balanced_text(
    text: str,
    mask: np.ndarray,
    requested_size: int,
    minimum_size: int,
    auto_fit: bool,
    line_height_for_size: Callable[[int], float],
    measure_for_size: Callable[[int, str], float],
    language: str = "auto",
    hyphenate: bool = True,
    orphan_control: bool = True,
    hanging_punctuation: bool = True,
    max_horizontal_compression: int = 0,
    auto_scale: bool = True,
    shape_type: str = "auto",
    padding: int = 8,
) -> dict:
    """Find the largest balanced layout fitting the detected interior."""
    start = max(int(minimum_size), int(requested_size))
    bounds = usable_mask_bounds(mask)
    centre_x = (bounds[0] + bounds[2]) / 2.0
    centre_y = (bounds[1] + bounds[3]) / 2.0
    row_limits = _central_row_limits(np.asarray(mask) > 0, centre_x)
    measurement_cache: dict[tuple[int, str], float] = {}

    def measured(size: int, value: str) -> float:
        key = (int(size), str(value))
        result = measurement_cache.get(key)
        if result is None:
            result = float(measure_for_size(size, value))
            measurement_cache[key] = result
        return result

    def attempt_size(size: int) -> dict | None:
        compressions = [1.0]
        if auto_scale:
            minimum = max(0.80, 1.0 - max(0, min(20, int(max_horizontal_compression))) / 100.0)
            compressions.extend(value for value in (0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.80) if value >= minimum)
        vertical_scales = (1.0, 0.96, 0.92, 0.88) if auto_scale else (1.0,)
        # Prefer the least destructive transform. Once the text fits at the
        # current font size there is no reason to evaluate every stronger
        # compression merely to gain a slightly different balance score.
        transforms = [(1.0, 1.0)]
        transforms.extend((1.0, scale_x) for scale_x in compressions[1:])
        transforms.extend(
            (scale_y, scale_x)
            for scale_y in vertical_scales[1:]
            for scale_x in compressions
        )
        geometry_cache: dict[float, list[tuple[list[tuple[int, int]], list[int]]]] = {}
        for scale_y, scale_x in transforms:
            line_height = max(1.0, float(line_height_for_size(size)) * scale_y)
            interval_candidates = geometry_cache.get(scale_y)
            if interval_candidates is None:
                max_lines = max(1, min(24, int(mask.shape[0] // line_height)))
                interval_candidates = []
                for count in range(1, max_lines + 1):
                    if shape_type in ("ellipse", "diamond", "oval", "shout", "rhombus"):
                        intervals = symmetric_shape_intervals(
                            shape_type, mask.shape[1], mask.shape[0], count, line_height, padding,
                        )
                        safe_intervals = line_intervals(mask, count, line_height, bounds, row_limits)
                        intervals = [
                            (max(left, safe_left), min(right, safe_right))
                            for (left, right), (safe_left, safe_right) in zip(intervals, safe_intervals)
                        ] if len(safe_intervals) == len(intervals) else []
                    else:
                        intervals = line_intervals(mask, count, line_height, bounds, row_limits)
                    if any(right - left < 8 for left, right in intervals):
                        intervals = []
                    interval_candidates.append((intervals, [right - left for left, right in intervals]))
                geometry_cache[scale_y] = interval_candidates
            best_transform = None
            for intervals, widths in interval_candidates:
                if not intervals:
                    continue
                measure = lambda value, s=size, sx=scale_x: measured(s, value) * sx
                lines = linguistic_wrap(
                    text, widths, measure, language, hyphenate,
                    orphan_control, hanging_punctuation,
                )
                if lines is None:
                    continue
                # Hanging punctuation may be ignored by the linguistic width
                # scorer; the painted glyphs must still stay inside the mask.
                if any(measure(line) > width - 1 for line, width in zip(lines, widths)):
                    continue
                score = sum(
                    ((width - measure(line)) / max(1.0, width)) ** 2
                    for line, width in zip(lines, widths)
                ) + (1.0 - scale_x) * 1.2 + (1.0 - scale_y) * 1.5
                attempt = {
                    "text": "\n".join(lines), "lines": lines, "intervals": intervals,
                    "font_size": size, "line_height": line_height, "overflow": False,
                    "balance_score": score, "scale_x": scale_x, "scale_y": scale_y,
                    "language": language, "center_x": centre_x, "center_y": centre_y,
                }
                if best_transform is None or score < best_transform["balance_score"]:
                    best_transform = attempt
            if best_transform is not None:
                return best_transform
        return None

    if auto_fit:
        # Fitting is monotonic for a fixed mask: once a size fits, smaller
        # sizes fit as well. A binary search replaces the previous exhaustive
        # walk through every point size (often 40+ full linguistic layouts).
        low, high = int(minimum_size), start
        best_layout = None
        while low <= high:
            size = (low + high) // 2
            candidate = attempt_size(size)
            if candidate is not None:
                best_layout = candidate
                low = size + 1
            else:
                high = size - 1
        if best_layout is not None:
            return best_layout
    else:
        fixed_layout = attempt_size(start)
        if fixed_layout is not None:
            return fixed_layout
    return {
        "text": text, "lines": str(text).splitlines() or [str(text)], "intervals": [],
        "font_size": start, "line_height": line_height_for_size(start),
        "overflow": True, "balance_score": float("inf"), "scale_x": 1.0,
        "scale_y": 1.0, "language": language,
        "center_x": centre_x, "center_y": centre_y,
    }
