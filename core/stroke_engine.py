"""Pure stroke geometry and rasterization shared by canvas brush tools."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class StrokeRaster:
    x: int
    y: int
    mask: np.ndarray

    @property
    def width(self) -> int:
        return int(self.mask.shape[1])

    @property
    def height(self) -> int:
        return int(self.mask.shape[0])


class StrokeEngine:
    """Build compact antialiased masks without allocating a full page."""

    MIN_SIZE = 1
    MAX_SIZE = 200

    @classmethod
    def clamp_size(cls, value: int) -> int:
        return max(cls.MIN_SIZE, min(cls.MAX_SIZE, int(value)))

    @staticmethod
    def is_suspicious_connector(mask: np.ndarray) -> bool:
        """Identify the historical page-jump artifact in a persisted patch."""
        hard = np.where(np.asarray(mask) > 8, 255, 0).astype(np.uint8)
        if hard.ndim != 2 or not np.any(hard):
            return False
        _, _, width, height = cv2.boundingRect(hard)
        longest, shortest = max(width, height), max(1, min(width, height))
        fill = float(np.count_nonzero(hard)) / max(1, width * height)
        # A real filled retouch may be large. The broken gesture is either a
        # very elongated bar or a sparse diagonal crossing a large rectangle.
        return longest >= 384 and (longest / shortest >= 5.0 or fill < 0.22)

    @classmethod
    def rasterize(
        cls,
        points: list[tuple[float, float] | None],
        diameter: int,
        image_width: int,
        image_height: int,
        padding: int = 2,
        trusted_continuous: bool = False,
    ) -> StrokeRaster | None:
        # A viewport scroll can remap the cursor thousands of source pixels
        # between two mouse events. Never let that discontinuity become a
        # painted connector or a page-sized allocation, even if the UI layer
        # failed to insert an explicit segment separator.
        # Mouse events from a real continuous stroke arrive much closer than
        # this. A larger jump is a remapped viewport coordinate, never a line
        # the brush should connect to the page edge.
        maximum_step = max(96.0, float(cls.clamp_size(diameter) * 3))
        safe_points: list[tuple[float, float] | None] = []
        previous: tuple[float, float] | None = None
        for point in points:
            if point is None:
                safe_points.append(None)
                previous = None
                continue
            if (
                not trusted_continuous and previous is not None
                and float(np.hypot(point[0] - previous[0], point[1] - previous[1])) > maximum_step
            ):
                # The point after the jump belongs to the scrolled viewport,
                # not to the stroke that started before it. Discard the tail.
                break
            safe_points.append(point)
            previous = point
        valid_points = [point for point in safe_points if point is not None]
        if not valid_points or image_width <= 0 or image_height <= 0:
            return None
        diameter = cls.clamp_size(diameter)
        radius = diameter / 2.0
        padding = max(0, int(padding))
        xs = [point[0] for point in valid_points]
        ys = [point[1] for point in valid_points]
        x1 = max(0, int(np.floor(min(xs) - radius - padding)))
        y1 = max(0, int(np.floor(min(ys) - radius - padding)))
        x2 = min(image_width, int(np.ceil(max(xs) + radius + padding + 1)))
        y2 = min(image_height, int(np.ceil(max(ys) + radius + padding + 1)))
        if x2 <= x1 or y2 <= y1:
            return None

        mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        segments: list[list[tuple[float, float]]] = []
        current_segment: list[tuple[float, float]] = []
        for point in safe_points:
            if point is None:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
                continue
            current_segment.append(point)
        if current_segment:
            segments.append(current_segment)

        radius = max(1, int(round(diameter / 2.0)))
        spacing = max(0.8, diameter * 0.25)
        for segment in segments:
            dabs: list[tuple[float, float]] = [segment[0]]
            for start, end in zip(segment, segment[1:]):
                distance = float(np.hypot(end[0] - start[0], end[1] - start[1]))
                steps = max(1, int(np.ceil(distance / spacing)))
                for step in range(1, steps + 1):
                    ratio = step / steps
                    dabs.append((
                        start[0] + (end[0] - start[0]) * ratio,
                        start[1] + (end[1] - start[1]) * ratio,
                    ))
            for x, y in dabs:
                center = (int(round(x - x1)), int(round(y - y1)))
                cv2.circle(mask, center, radius, 255, -1, cv2.LINE_AA)
        return StrokeRaster(x1, y1, np.ascontiguousarray(mask))

    @staticmethod
    def paint(rgb: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
        if rgb.shape[:2] != mask.shape:
            raise ValueError("La máscara del pincel no coincide con el parche.")
        alpha = mask.astype(np.float32)[:, :, None] / 255.0
        color_array = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
        return np.clip(
            rgb.astype(np.float32) * (1.0 - alpha) + color_array * alpha,
            0,
            255,
        ).astype(np.uint8)

    @staticmethod
    def erase_mask_entries(entries: list[dict], stroke: StrokeRaster | dict) -> bool:
        """Erase only the overlap of a compact brush stroke from mask entries."""
        if isinstance(stroke, StrokeRaster):
            sx, sy, stroke_mask = stroke.x, stroke.y, stroke.mask
        else:
            sx, sy = int(stroke.get("x", 0)), int(stroke.get("y", 0))
            stroke_mask = np.asarray(stroke.get("mask"), dtype=np.uint8)
        if stroke_mask.ndim != 2 or not np.any(stroke_mask):
            return False
        sh, sw = stroke_mask.shape
        changed = False
        for entry in entries:
            target_mask = np.asarray(entry.get("mask"), dtype=np.uint8)
            if target_mask.ndim != 2:
                continue
            ex, ey = int(entry.get("x", 0)), int(entry.get("y", 0))
            eh, ew = target_mask.shape
            ix1, iy1 = max(sx, ex), max(sy, ey)
            ix2, iy2 = min(sx + sw, ex + ew), min(sy + sh, ey + eh)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            erase = stroke_mask[iy1 - sy:iy2 - sy, ix1 - sx:ix2 - sx] > 8
            target = target_mask[iy1 - ey:iy2 - ey, ix1 - ex:ix2 - ex]
            if np.any(target[erase]):
                target[erase] = 0
                changed = True
        return changed
