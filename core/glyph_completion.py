"""Complete ink cut by a text box, without expanding that box into a fill."""
import cv2
import numpy as np


def complete_soft_text_halos(rgb, mask, selection):
    """Complete faint fringes immediately beside confirmed ink on flat colour.

    Compression can leave disconnected, nearly background-coloured dots after
    ordinary glyph dilation. Admit only small low-contrast components within
    six pixels of approved ink. Never grow a whole selection or follow a line
    attached to unapproved high-contrast artwork.
    """
    if rgb.shape[:2] != mask.shape or mask.shape != selection.shape:
        return mask
    if not np.any(mask) or not np.any(selection):
        return mask
    # Work on the selected crop, not the whole page/context for every glyph.
    sx, sy, sw, sh = cv2.boundingRect(selection)
    image = rgb[sy:sy + sh, sx:sx + sw].astype(np.int16)
    hard = mask[sy:sy + sh, sx:sx + sw] > 0
    selected = selection[sy:sy + sh, sx:sx + sw] > 0
    background_pixels = selected & ~hard
    if np.count_nonzero(background_pixels) < max(96, np.count_nonzero(selected) * .15):
        return mask
    background = np.median(image[background_pixels], axis=0)
    difference = np.max(np.abs(image - background), axis=2)
    # A real flat-colour peak, rather than a broad gradient or screentone.
    if np.mean(difference[background_pixels] <= 3) < .68:
        return mask
    safe = cv2.erode(
        selected.astype(np.uint8), np.ones((5, 5), np.uint8),
        borderType=cv2.BORDER_CONSTANT, borderValue=0,
    ) > 0
    near = cv2.dilate(
        hard.astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)),
    ) > 0
    art_guard = cv2.dilate(
        ((difference > 48) & ~hard).astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    ) > 0
    soft = ((difference > 3) & (difference <= 48) & ~hard & selected).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(soft, 8)
    additions = np.zeros_like(soft)
    max_area = max(96, min(320, int(sw * sh * .03)))
    max_side = max(24, min(72, int(min(sw, sh) * .8)))
    eligible = near & safe & ~art_guard
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        if area > max_area or max(width, height) > max_side:
            continue
        component = labels[y:y + height, x:x + width] == label
        local_eligible = eligible[y:y + height, x:x + width]
        # Reject whole components crossing a border, instead of trimming them
        # into smaller, seemingly plausible fragments.
        if np.count_nonzero(component & local_eligible) < area:
            continue
        additions[y:y + height, x:x + width][component] = 255
    if not np.any(additions):
        return mask
    result = mask.copy()
    np.maximum(result[sy:sy + sh, sx:sx + sw], additions,
               out=result[sy:sy + sh, sx:sx + sw])
    return result


def complete_outlined_lettering(rgb, mask, selection):
    """Finish a lettering outline that leaves the text box over artwork.

    Outlined captions and effects sit directly on the drawing, and a tight
    detector box cuts through their border. Boundary protection then trims
    the mask, and the reconstruction keeps dark specks of that border. Follow
    only near-black or near-white components that the mask already covers by
    half, stay close to the box and never reach the context frame; balloon
    outlines, panel frames and artwork fail the coverage test.
    """
    if rgb.shape[:2] != mask.shape or mask.shape != selection.shape:
        return mask
    if not np.any(mask) or not np.any(selection):
        return mask
    height, width = mask.shape
    sx, sy, sw, sh = cv2.boundingRect(selection)
    pad = max(6, min(20, int(round(min(sw, sh) * .15))))
    x1, y1 = max(1, sx - pad), max(1, sy - pad)
    x2, y2 = min(width - 1, sx + sw + pad), min(height - 1, sy + sh + pad)
    if x2 <= x1 or y2 <= y1:
        return mask
    masked = mask > 0
    additions = np.zeros_like(mask)
    polarities = (rgb.max(axis=2) <= 70, rgb.min(axis=2) >= 215)
    for polarity in polarities:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(polarity.astype(np.uint8), 8)
        for label in np.unique(labels[masked]):
            if label == 0 or label >= count:
                continue
            cx, cy, cw, ch, area = (int(v) for v in stats[label])
            if cx < x1 or cy < y1 or cx + cw > x2 or cy + ch > y2:
                continue
            if area < 24 or area > sw * sh * .5:
                continue
            component = labels[cy:cy + ch, cx:cx + cw] == label
            covered = np.count_nonzero(component & masked[cy:cy + ch, cx:cx + cw])
            if covered < area * .5 or area - covered < 4:
                continue
            additions[cy:cy + ch, cx:cx + cw][component] = 255
    if not np.any(additions):
        return mask
    additions = cv2.dilate(additions, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    reach = np.zeros_like(mask)
    reach[y1:y2, x1:x2] = 255
    return cv2.bitwise_or(mask, cv2.bitwise_and(additions, reach))


def cover_lettering_halo(rgb, mask, selection):
    """Extend a mask over the glow or border painted around lettering on artwork.

    When the mask stops inside a white or coloured border, the reconstruction
    samples that border and paints a pale blob in the shape of the text. A
    halo is a band of one colour hugging the mask that ends within a short
    reach; a balloon or garment of the same colour continues past that reach
    and is left alone.
    """
    if rgb.shape[:2] != mask.shape or mask.shape != selection.shape:
        return mask
    if not np.any(mask) or not np.any(selection):
        return mask

    def disc(radius):
        return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))

    _, _, sw, sh = cv2.boundingRect(selection)
    reach = max(8, min(22, int(round(min(sw, sh) * .18))))
    masked = mask > 0
    ring = (cv2.dilate(mask, disc(4)) > 0) & ~masked
    limit = cv2.dilate(mask, disc(reach))
    far = (cv2.dilate(mask, disc(reach + 10)) > 0) & (limit == 0)
    if np.count_nonzero(ring) < 32 or np.count_nonzero(far) < 32:
        return mask
    halo = np.median(rgb[ring].astype(np.int16), axis=0)
    alike = np.max(np.abs(rgb.astype(np.int16) - halo), axis=2) <= 26
    if np.mean(alike[ring]) < .6 or np.mean(alike[far]) > .35:
        return mask
    candidate = alike & (limit > 0) & ~masked
    count, labels = cv2.connectedComponents((candidate | masked).astype(np.uint8), 8)
    attached = np.unique(labels[masked])
    grown = candidate & np.isin(labels, attached[attached != 0])
    edge = (limit > 0) & (cv2.erode(limit, disc(2)) == 0)
    if not np.any(grown) or (np.any(edge) and np.mean(grown[edge]) > .3):
        return mask
    grown = cv2.dilate(grown.astype(np.uint8) * 255, disc(2))
    return cv2.bitwise_or(mask, cv2.bitwise_and(grown, limit))


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

    The contrasting component must be almost entirely masked, text-sized, and
    inside a flat selection. This handles faint/colored strokes without admitting
    disconnected punctuation, balloon outlines or impact rays.
    """
    if rgb.shape[:2] != mask.shape or mask.shape != selection.shape:
        return mask
    if not np.any(mask) or not np.any(selection):
        return mask
    sx, sy, sw, sh = cv2.boundingRect(selection)
    selected = selection > 0
    samples = rgb[selected].astype(np.float32)
    background = np.median(samples, axis=0)
    if np.mean(np.max(np.abs(samples - background), axis=1) <= 10) < .68:
        return mask
    difference = np.max(np.abs(rgb.astype(np.float32) - background), axis=2)
    dark = (difference > 12).astype(np.uint8)
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
