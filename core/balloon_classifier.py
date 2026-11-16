"""Conservative visual classification of closed speech balloon outlines."""
from __future__ import annotations

import cv2
import numpy as np

from core.balloon_geometry import enclosed_balloon_mask
from core.balloon_typesetter import balloon_search_rect


BALLOON_KINDS = ("dialogue", "shout", "thought", "caption", "sfx")

# The look of each kind when the project has no font role for it. Only these
# keys are reset when a box changes kind, so effects the user adds by hand
# to a box keep working until its kind changes again.
KIND_LOOK_KEYS = (
    "font_weight", "italic", "text_case", "text_color", "stroke_width", "stroke_color",
    "stroke2_enabled", "stroke2_width", "stroke2_color", "balloon_fit",
)
KIND_LOOKS = {
    "dialogue": {},
    "shout": {"font_weight": 900, "text_case": "upper", "stroke_width": 2, "stroke_color": "#FFFFFF"},
    "thought": {"italic": True, "text_color": "#333333", "stroke_width": 1, "stroke_color": "#FFFFFF"},
    "caption": {"font_weight": 700, "stroke_width": 0},
    "sfx": {
        "font_weight": 900, "text_case": "upper", "text_color": "#FFFFFF",
        "stroke_width": 4, "stroke_color": "#111111",
        "stroke2_enabled": True, "stroke2_width": 9, "stroke2_color": "#FFFFFF",
        "balloon_fit": False,
    },
}


def is_sound_effect(rgb: np.ndarray) -> bool:
    """One saturated fill colour bordered by a white outline: drawn lettering over art."""
    if rgb.ndim != 3 or min(rgb.shape[:2]) < 64:
        return False
    rgb = np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)
    scale = 320 / max(rgb.shape[:2])
    if scale < 1:
        rgb = cv2.resize(rgb, (max(1, round(rgb.shape[1] * scale)), max(1, round(rgb.shape[0] * scale))),
                         interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    white = rgb.min(axis=2) > 220
    fill = (hsv[..., 1] > 110) & (hsv[..., 2] > 50)
    if fill.mean() < .10 or not .04 <= white.mean() <= .5:
        return False
    hues = hsv[..., 0][fill]
    dominant = np.bincount(hues // 10, minlength=19).max() / max(1, hues.size)
    ring = cv2.dilate(fill.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~fill
    bordered = np.count_nonzero(white & ring) / max(1, np.count_nonzero(ring))
    return bool(dominant >= .6 and bordered >= .35)


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
    # Coloured lettering with a white rim is a sound effect drawn on the
    # art, even where its outline happens to close like a balloon.
    if is_sound_effect(rgb[y:y + bh, x:x + bw]):
        return "sfx"
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
    # A thought cloud is round overall but its rim is a chain of bumps: many
    # shallow inlets spread around it, where a dialogue bubble has at most
    # the one or two beside its tail.
    if solidity >= .83 and extent < .82 and _rim_bumps(contour, max(shape_width, shape_height)) >= 6:
        return "thought"
    # A panel/caption has four nearly straight edges and fills its bounds.
    if solidity >= .9 and extent >= .84 and 4 <= vertices <= 6:
        return "caption"
    # Rounded dialogue bubbles may have a small tail, so allow some concavity.
    if solidity >= .83 and extent < .87:
        return "dialogue"
    return None


def _rim_bumps(contour: np.ndarray, size: int) -> int:
    hull = cv2.convexHull(contour, returnPoints=False)
    if hull is None or len(hull) < 4:
        return 0
    try:
        defects = cv2.convexityDefects(contour, np.sort(hull, axis=0))
    except cv2.error:
        return 0
    if defects is None:
        return 0
    depths = np.asarray(defects).reshape(-1, 4)[:, 3] / 256.0
    return int(np.count_nonzero((depths >= size * .012) & (depths <= size * .12)))
