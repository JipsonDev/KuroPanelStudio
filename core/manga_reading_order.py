"""Infer manga reading blocks from the geometry of detected text boxes."""
from statistics import median


def _height(box: dict) -> int:
    return max(1, int(box["height"]))


def _top(box: dict) -> int:
    return int(box["y"])


def _bottom(box: dict) -> int:
    return _top(box) + _height(box)


def _right(box: dict) -> int:
    return int(box["x"]) + max(1, int(box.get("width", 0)))


def _bands(boxes: list[dict]) -> list[list[dict]]:
    """Separate vertically distant sets before looking for columns."""
    bands: list[list[dict]] = []
    lowest = None
    for box in sorted(boxes, key=lambda item: (_top(item), -_height(item), -_right(item))):
        # Measure the gap against the last box above it. A tall box in
        # the next band cannot erase a separation above its top edge.
        if lowest is None or _top(box) - _bottom(lowest) > max(24, .70 * _height(lowest)):
            bands.append([box])
            lowest = box
        else:
            bands[-1].append(box)
            if _bottom(box) > _bottom(lowest):
                lowest = box
    return bands


def _rows(boxes: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for box in sorted(boxes, key=lambda item: (_top(item), -_height(item), -_right(item))):
        if rows:
            anchor = rows[-1][0]
            shorter = min(_height(anchor), _height(box))
            overlap = min(_bottom(anchor), _bottom(box)) - _top(box)
            # Anchor each row to its first box to avoid chaining down the page.
            if overlap * 2 >= shorter and _top(box) - _top(anchor) <= max(12, shorter * .65):
                rows[-1].append(box)
                continue
        rows.append([box])
    return rows


def _order_band(boxes: list[dict]) -> list[dict]:
    rows = _rows(boxes)
    if len(rows) >= 2:
        by_x = sorted(boxes, key=lambda box: (int(box["x"]), _right(box)))
        extent = max(map(_right, by_x)) - int(by_x[0]["x"])
        typical_width = median(_right(box) - int(box["x"]) for box in by_x)
        minimum_gap = max(24, 1.5 * typical_width, .18 * extent)
        rightmost = _right(by_x[0])
        split = 0
        widest_gap = 0
        for index, box in enumerate(by_x[1:], 1):
            boundary = int(box["x"])
            gap = boundary - rightmost
            rightmost = max(rightmost, _right(box))
            if gap < minimum_gap or gap <= widest_gap:
                continue
            # A panel can contain a single balloon. Require a wider gutter
            # before treating it as a block of its own.
            if min(index, len(by_x) - index) == 1 and gap < min(2 * typical_width, .42 * extent):
                continue
            left, right = by_x[:index], by_x[index:]
            left_top, right_top = min(map(_top, left)), min(map(_top, right))
            left_bottom, right_bottom = max(map(_bottom, left)), max(map(_bottom, right))
            overlap = min(left_bottom, right_bottom) - max(left_top, right_top)
            if overlap * 2 < min(left_bottom - left_top, right_bottom - right_top):
                continue
            # Fully paired rows, such as a regular grid, still read row by row.
            if all(
                min(int(item["x"]) for item in row) < boundary <= max(int(item["x"]) for item in row)
                for row in rows
            ):
                continue
            split, widest_gap = index, gap
        if split:
            return order_manga(by_x[split:]) + order_manga(by_x[:split])

    return [box for row in rows for box in sorted(row, key=lambda item: (-_right(item), _top(item)))]


def order_manga(boxes: list[dict]) -> list[dict]:
    """Read down each band, completing clearly separated blocks right to left."""
    return [box for band in _bands(boxes) for box in _order_band(band)]
