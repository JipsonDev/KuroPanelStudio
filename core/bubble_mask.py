"""Colour-connected balloon reconstruction inspired by Bubble_Mask.atn.

Photoshop Grow follows adjacent similar colours, not a fixed dilation. Here
an eroded core validates each connected background before its glyph holes are
filled. No convex hull can bridge neighbouring balloons or concave outlines.
"""
from __future__ import annotations

import cv2
import numpy as np


def colour_connected_balloon_mask(rgb: np.ndarray, selection: np.ndarray):
    """Return (glyph mask, flat fill) or None when contour evidence is absent.

    Only complete, closed, flat balloons qualify. Open/gradient/textured areas
    continue through the existing neural-mask pipeline. A tight text selection
    admits connected glyphs just beyond its edge, never a whole nearby balloon.
    """
    if rgb.shape[:2] != selection.shape or not np.any(selection):
        return None
    height, width = selection.shape
    selected = selection > 0
    sx, sy, sw, sh = cv2.boundingRect(selection)
    samples = rgb[selected]
    if samples.shape[0] < 64:
        return None
    # Test dominant colours separately, including grey/tinted/dark balloons.
    quantized = (samples.astype(np.int32) // 8)
    codes = quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]
    histogram = np.bincount(codes, minlength=32768)
    merged = np.zeros_like(selection)
    chosen_colour = None
    for code in np.argsort(histogram)[-3:][::-1]:
        if histogram[code] < max(32, samples.shape[0] * .1):
            continue
        colour = np.median(samples[codes == code], axis=0)
        difference = np.max(np.abs(rgb.astype(np.int16) - colour), axis=2)
        # ATN Grow tolerance = 3. Keep tiny antialiasing holes for reconstruction.
        background = np.where(difference <= 3, 255, 0).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(background, 8)
        for label in np.unique(labels[selected]):
            if label == 0 or label >= count:
                continue
            x, y, w, h, area = (int(v) for v in stats[label])
            if (x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
                    or area < 128):
                continue
            component = np.where(labels[y:y+h, x:x+w] == label, 255, 0).astype(np.uint8)
            contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            silhouette = np.zeros_like(component)
            cv2.drawContours(silhouette, contours, -1, 255, cv2.FILLED)
            interior_area = np.count_nonzero(silhouette)
            if (area < interior_area * .58
                    or interior_area < max(200, np.count_nonzero(selected) * .18)
                    or min(w, h) < min(sw, sh) * .55):
                continue
            local_selection = selected[y:y+h, x:x+w]
            overlap = np.count_nonzero((silhouette > 0) & local_selection)
            if overlap < min(np.count_nonzero(selected) * .5, interior_area * .6):
                continue
            distance = cv2.distanceTransform(cv2.copyMakeBorder(component, 1, 1, 1, 1, cv2.BORDER_CONSTANT), cv2.DIST_L2, 5)[1:-1, 1:-1]
            # Two 25px contractions in the ATN discard thin islands. Scale
            # the core test for small editor crops instead of deleting them.
            contract = max(3, min(50, min(w, h) * .08))
            if float(distance.max()) < contract:
                continue
            # A quantized slice of a gradient is not a closed balloon. Require
            # a contrasting enclosing border along most of the silhouette.
            full = np.zeros_like(selection)
            full[y:y+h, x:x+w] = silhouette
            ring = cv2.subtract(cv2.dilate(full, np.ones((5, 5), np.uint8)), full) > 0
            if np.mean(difference[ring] > 20) < .65:
                continue
            inner_distance = cv2.distanceTransform(cv2.copyMakeBorder(silhouette, 1, 1, 1, 1, cv2.BORDER_CONSTANT), cv2.DIST_L2, 5)[1:-1, 1:-1]
            local_diff = difference[y:y+h, x:x+w]
            holes = np.where((silhouette > 0) & (local_diff > 3), 255, 0).astype(np.uint8)
            n, hole_labels, hole_stats, _ = cv2.connectedComponentsWithStats(holes, 8)
            text = np.zeros_like(holes)
            # A whole-balloon selection is allowed to recover all inner dots;
            # a line selection follows only components near that line.
            reach = max(6, min(24, int(min(sw, sh) * .3)))
            allowed = np.zeros_like(selection)
            allowed[max(0, sy-reach):min(height, sy+sh+reach), max(0, sx-reach):min(width, sx+sw+reach)] = 255
            allowed = allowed[y:y+h, x:x+w] > 0
            for item in range(1, n):
                hx, hy, hw, hh, pixels = (int(v) for v in hole_stats[item])
                component_pixels = hole_labels == item
                if pixels < 2 or pixels > interior_area * .2:
                    continue
                if np.any(component_pixels & (inner_distance < 2.5)):
                    continue
                if np.count_nonzero(component_pixels & allowed) < pixels * .95:
                    continue
                # Reject line-art spanning much of the balloon.
                if (hw > w * .7 and hh < 6) or (hh > h * .7 and hw < 6):
                    continue
                text[component_pixels] = 255
            if not np.any(text) or np.count_nonzero(text) > interior_area * .35:
                continue
            # A feather on bare ink leaves grey fringes. First cover the full
            # glyph and its antialiasing, then keep a two-pixel contour inset.
            text = cv2.dilate(text, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
            text[inner_distance < 2.0] = 0
            if chosen_colour is not None and np.max(np.abs(chosen_colour - colour)) > 3:
                # One returned fill must never repaint differently coloured
                # neighbours; let the existing per-region fallback handle it.
                return None
            chosen_colour = colour
            np.maximum(merged[y:y+h, x:x+w], text, out=merged[y:y+h, x:x+w])
    if chosen_colour is None or not np.any(merged):
        return None
    return merged, tuple(int(v) for v in chosen_colour)
