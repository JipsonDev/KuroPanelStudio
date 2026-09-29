"""Complete ink cut by a text box, without expanding that box into a fill."""
import cv2
import numpy as np


def complete_boundary_glyphs(rgb, mask, selection, locator=None):
    """Follow small ink components across box edges on uniform backgrounds.

    Only components overlapping the selected text locator are eligible. Long
    outlines, components reaching the context edge, and textured backgrounds
    are excluded. The added pixels are visible in the editable mask preview.
    """
    if not np.any(selection):
        return mask
    x, y, w, h = cv2.boundingRect(selection)
    selected = selection > 0
    samples = rgb[selected].astype(np.float32)
    background = np.median(samples, axis=0)
    distances = np.linalg.norm(samples - background, axis=1)
    # Require a dominant flat colour, not a gradient or screentone.
    if np.mean(distances < 10) < .68:
        return mask
    pad = max(6, min(24, int(min(w, h) * .3)))
    height, width = selection.shape
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(width, x + w + pad), min(height, y + h + pad)
    distance = np.linalg.norm(rgb.astype(np.float32) - background, axis=2)
    ink = np.where(distance > 22, 255, 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    reference = mask if locator is None else cv2.bitwise_or(mask, locator)
    reference = (reference > 0) & selected
    if not np.any(reference):
        return mask
    accepted = np.zeros_like(mask)
    anchors = []
    candidates = np.unique(labels[reference & (ink > 0)])
    for label in candidates:
        if label == 0 or label >= count:
            continue
        cx, cy, cw, ch, area = (int(v) for v in stats[label])
        if (cx <= x1 or cy <= y1 or cx + cw >= x2 or cy + ch >= y2
                or area < 2 or area > w * h * .22
                or cw > max(24, w * .6) or ch > max(24, h * 1.8)):
            continue
        component = labels == label
        overlap = np.count_nonzero(component & reference)
        if overlap < max(2, area * .2):
            continue
        # Complete only ink crossing the original search boundary, leaving
        # interior classifications to the existing text-mask pipeline.
        if np.any(component & ~selected):
            accepted[component] = 255
            anchors.append((cx, cy, cw, ch, area))
    # Punctuation and accents can be disconnected from the main stroke.
    # Recover only small, vertically aligned islands next to an anchored glyph.
    for label in range(1, count):
        cx, cy, cw, ch, area = (int(v) for v in stats[label])
        if (cx <= x1 or cy <= y1 or cx + cw >= x2 or cy + ch >= y2
                or area < 2 or area > 100):
            continue
        for ax, ay, aw, ah, anchor_area in anchors:
            gap = max(ay - (cy + ch), cy - (ay + ah), 0)
            if (ax - 2 <= cx + cw / 2 <= ax + aw + 2
                    and cw <= max(5, aw * 1.5) and ch <= max(5, ah * .5)
                    and area <= max(9, anchor_area * .55)
                    and 0 < gap <= max(4, min(aw * 1.3, ah * .35))):
                accepted[labels == label] = 255
                break
    if np.any(accepted):
        # A small halo covers antialiasing instead of leaving black fringes.
        accepted = cv2.dilate(accepted, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    return cv2.bitwise_or(mask, accepted)


def complete_anchored_glyph_fragments(rgb, mask, selection):
    """Finish tiny gaps in a glyph already covered by the cleaning mask.

    The dark component must be almost entirely masked, text-sized, and fully
    inside a light selection. This handles residual strokes without admitting
    disconnected punctuation, balloon outlines or impact rays.
    """
    if rgb.shape[:2] != mask.shape or mask.shape != selection.shape:
        return mask
    if not np.any(mask) or not np.any(selection):
        return mask
    sx, sy, sw, sh = cv2.boundingRect(selection)
    selected = selection > 0
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if float(np.median(gray[selected])) < 190:
        return mask
    dark = (gray < 135).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    additions = np.zeros_like(mask)
    max_area = max(160, int(sw * sh * .06))
    max_width = max(24, min(85, int(sw * .25)))
    max_height = max(40, min(120, int(sh * .90)))
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        if not (20 <= area <= max_area and width <= max_width and height <= max_height):
            continue
        if (x < sx + 3 or y < sy + 3 or
                x + width > sx + sw - 3 or y + height > sy + sh - 3):
            continue
        component = labels[y:y + height, x:x + width] == label
        covered = np.count_nonzero(component & (mask[y:y + height, x:x + width] > 0))
        missing = area - covered
        if not (0 < missing <= max(6, int(area * .12)) and covered >= area * .85):
            continue
        patch = additions[y:y + height, x:x + width]
        patch[component & (mask[y:y + height, x:x + width] == 0)] = 255
    if not np.any(additions):
        return mask
    additions = cv2.dilate(
        additions, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    )
    safe_interior = cv2.erode(
        selection, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    )
    return cv2.bitwise_or(mask, cv2.bitwise_and(additions, safe_interior))
