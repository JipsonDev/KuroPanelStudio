"""Conservative visual classification of closed speech balloon outlines."""
from __future__ import annotations

import cv2
import numpy as np

from core.balloon_geometry import enclosed_balloon_mask
from core.balloon_typesetter import balloon_search_rect


BALLOON_KINDS = ("dialogue", "shout", "caption")


def classify_balloon(rgb: np.ndarray, box: tuple[int, int, int, int]) -> str | None:
    """Classify the closed contour around a text box, or abstain if uncertain.

    The box is only a seed: its aspect ratio cannot identify a balloon. Open
    outlines, page margins and artwork therefore return None.
    """
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return None
    height, width = rgb.shape[:2]
    x, y, bw, bh = (int(value) for value in box)
    if min(bw, bh) < 12 or not (0 <= x < width and 0 <= y < height):
        return None
    sx, sy, sw, sh = balloon_search_rect(width, height, (x, y, bw, bh))
    crop = np.ascontiguousarray(rgb[sy:sy + sh, sx:sx + sw, :3], dtype=np.uint8)
    if min(crop.shape[:2]) < 12:
        return None
    seed = np.zeros((sh, sw), np.uint8)
    inset_x, inset_y = max(1, bw // 4), max(1, bh // 4)
    left, top = max(0, x - sx + inset_x), max(0, y - sy + inset_y)
    right, bottom = min(sw, x - sx + bw - inset_x), min(sh, y - sy + bh - inset_y)
    if right <= left or bottom <= top:
        return None
    seed[top:bottom, left:right] = 255
    mask = enclosed_balloon_mask(crop, seed)
    if mask is None:
        return None
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    if area < max(100.0, bw * bh * .25) or perimeter <= 0:
        return None
    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    if hull_area <= 0:
        return None
    solidity = area / hull_area
    _bx, _by, shape_width, shape_height = cv2.boundingRect(contour)
    extent = area / max(1.0, shape_width * shape_height)
    vertices = len(cv2.approxPolyDP(contour, .015 * perimeter, True))

    # Radiating speech balloons have alternating points and deep inlets.
    if solidity < .83 and vertices >= 8:
        return "shout"
    # A panel/caption has four nearly straight edges and fills its bounds.
    if solidity >= .9 and extent >= .84 and 4 <= vertices <= 6:
        return "caption"
    # Rounded dialogue bubbles may have a small tail, so allow some concavity.
    if solidity >= .83 and extent < .87:
        return "dialogue"
    return None
