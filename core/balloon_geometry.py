"""Conservative closed balloon geometry shared by layout and text grouping."""
from __future__ import annotations

import cv2
import numpy as np


def closed_balloon_text_candidates(rgb: np.ndarray) -> list[tuple[int, ...]]:
    """Find flat closed balloons with plausible ink for a detailed YOLO pass.

    Returns balloon and ink rectangles, never an accepted detection. Empty
    balloons, page backgrounds, textured artwork and long frame strokes are
    excluded before invoking the detector at a more useful scale.
    """
    source = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)
    height, width = source.shape[:2]
    gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), .6), 30, 90)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(edges), 8)
    candidates = []
    for label in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[label])
        if (x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
                or min(w, h) < 48 or area < 1600 or w * h > width * height * .7):
            continue
        component = np.where(labels[y:y+h, x:x+w] == label, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        interior = np.zeros_like(component)
        cv2.drawContours(interior, contours, -1, 255, cv2.FILLED)
        interior_area = np.count_nonzero(interior)
        if area < interior_area * .6:
            continue
        local = source[y:y+h, x:x+w]
        samples = local[component > 0].astype(np.float32)
        background = np.median(samples, axis=0)
        if np.percentile(np.max(np.abs(samples - background), axis=1), 90) > 10:
            continue
        inset = cv2.erode(interior, np.ones((7, 7), np.uint8)) > 0
        difference = np.max(np.abs(local.astype(np.float32) - background), axis=2)
        ink = np.where(inset & (difference > 24), 255, 0).astype(np.uint8)
        n, ink_labels, ink_stats, _ = cv2.connectedComponentsWithStats(ink, 8)
        glyphs = np.zeros_like(ink)
        accepted = 0
        for item in range(1, n):
            ix, iy, iw, ih, pixels = (int(value) for value in ink_stats[item])
            if (pixels < 5 or pixels > interior_area * .1 or max(iw, ih) > max(w, h) * .6
                    or max(iw, ih) / max(1, min(iw, ih)) > 12):
                continue
            glyphs[ink_labels == item] = 255
            accepted += 1
        ink_area = np.count_nonzero(ink)
        kept = np.count_nonzero(glyphs)
        if accepted < 3 or kept < 30 or kept < ink_area * .85 or kept > interior_area * .3:
            continue
        tx, ty, tw, th = cv2.boundingRect(glyphs)
        candidates.append((x, y, w, h, x + tx, y + ty, tw, th))
    return candidates


def enclosed_balloon_mask(rgb: np.ndarray, selection: np.ndarray | None = None) -> np.ndarray | None:
    """Find an enclosed background, filling glyph holes but never a crop frame.

    A selection asks for a contour containing at least 90% of that text box.
    Without one, prefer the largest enclosed central region for typesetting.
    None means insufficient contour evidence, not a rectangular balloon.
    """
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("La imagen del globo debe ser RGB.")
    height, width = rgb.shape[:2]
    if min(height, width) < 12:
        return None
    source = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)
    gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 30, 90)
    # Close only tiny contour gaps, on the barriers rather than the background:
    # closing background pixels would bridge thin outlines into the page.
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(edges), 8)
    selected = selection > 0 if selection is not None else None
    if selected is not None and (selection.shape != (height, width) or not np.any(selected)):
        return None
    reference_area = np.count_nonzero(selected) if selected is not None else height * width
    best, best_score = None, 0
    for label in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[label])
        if x == 0 or y == 0 or x + w == width or y + h == height:
            continue
        if area < max(48, reference_area * .12):
            continue
        component = np.where(labels[y:y + h, x:x + w] == label, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(component)
        cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
        filled_area = np.count_nonzero(filled)
        # Thin rings between the two sides of an outline are not backgrounds.
        if filled_area < reference_area * .22 or area < filled_area * .35:
            continue
        mask = np.zeros((height, width), np.uint8)
        mask[y:y + h, x:x + w] = filled
        if selected is not None:
            if np.count_nonzero(selected & (mask > 0)) < reference_area * .9:
                continue
        elif not mask[height // 2, width // 2]:
            continue
        if area > best_score:
            best, best_score = mask, area
    return best
