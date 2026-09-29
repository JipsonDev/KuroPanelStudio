"""Plan watermarks once along the ordered chapter, then project onto pages."""
from __future__ import annotations

from core.watermark_layout import free_intervals
from core.watermark_manager import normalized_watermark, prepare_watermark


def chapter_watermark_settings(pages: list[dict], value: dict) -> dict[str, dict]:
    """Pages contain name, width, height and optional reading regions.

    No page pixels are loaded or stitched. Repeated marks share one vertical
    coordinate system; page boundaries only prevent splitting a logo in two.
    Explicit manual positions remain authoritative and reserve global space.
    """
    config = normalized_watermark(value)
    enabled = set(config["enabled_pages"])
    output = {}
    prepared = {}
    rows = []
    total = 0
    for page in pages:
        width, height = int(page["width"]), int(page["height"])
        if width <= 0 or height <= 0:
            continue
        offset = total
        total += height
        if page["name"] not in enabled:
            continue
        settings = {**config, "avoid_regions": page.get("regions", []), "positions": None}
        manual = config["page_positions"].get(page["name"])
        if not config["repeat"]:
            settings["positions"] = manual
            output[page["name"]] = settings
            continue
        size = (width, height)
        if size not in prepared:
            prepared[size] = prepare_watermark(size, config)
        mark = prepared[size]
        settings.update(positions=[], chapter_resolved=True)
        output[page["name"]] = settings
        if mark is not None:
            if manual:
                # Older loading previews could save several marks clamped to
                # the same thumbnail edge. Replan invalid/overlapping sets;
                # clamping them again would permanently reproduce the pile-up.
                valid = []
                for x, y in manual:
                    if (not 0 <= x <= width - mark.width
                            or not 0 <= y <= height - mark.height
                            or any(abs(x - px) < mark.width and abs(y - py) < mark.height
                                   for px, py in valid)):
                        manual = None
                        break
                    valid.append((x, y))
            rows.append((page, offset, mark.size, manual))
    if not rows or not config["repeat"]:
        return output

    max_height = max(size[1] for _, _, size, _ in rows)
    gap = config["minimum_gap"]
    step = max_height + gap
    margin = min(config["margin_y"], max(0, (total - max_height) // 2))
    obstacles = []
    manual_count = 0
    for page, offset, (mw, mh), manual in rows:
        if manual is None:
            continue
        points = [(max(0, min(int(x), page["width"] - mw)),
                   max(0, min(int(y), page["height"] - mh))) for x, y in manual]
        output[page["name"]]["positions"] = points
        manual_count += len(points)
        for _, y in points:
            # All auto marks stay at least gap pixels from manual marks,
            # including those across a page boundary and in another column.
            obstacles.append(dict(x=0, y=offset + y, width=1, height=mh))

    horizontal = config["anchor"].split("-")[-1]
    sides = ("left", "right") if horizontal == "left" else ("right", "left")
    modes = sides if config["distribution"] == "alternating" else (horizontal,)
    intervals = {}
    for side in modes:
        available = []
        for page, offset, size, manual in rows:
            if manual is not None:
                continue
            mw, mh = size
            width, height = page["width"], page["height"]
            x = (config["margin_x"] if side == "left" else width - mw - config["margin_x"]
                 if side == "right" else (width - mw) // 2)
            x = max(0, min(x + config["offset_x"], width - mw))
            # No margin is repeated at internal seams. A mark stays whole on
            # its page while the separation is enforced in chapter coordinates.
            low, high = max(0, margin - offset), min(height - mh, total - margin - mh - offset)
            regions = output[page["name"]]["avoid_regions"] if config["avoid_text"] else []
            regions = normalized_watermark({"avoid_regions": regions})["avoid_regions"]
            for start, end in free_intervals(x, (low, high), size, regions):
                spans = [(start + offset, end + offset)]
                for obstacle in obstacles:
                    block_start = obstacle["y"] - max_height - gap + 1
                    block_end = obstacle["y"] + obstacle["height"] + gap - 1
                    remaining = []
                    for a, b in spans:
                        if b < block_start or a > block_end:
                            remaining.append((a, b))
                        else:
                            if a < block_start:
                                remaining.append((a, block_start - 1))
                            if b > block_end:
                                remaining.append((block_end + 1, b))
                    spans = remaining
                available.extend((a, b, x, page["name"], offset) for a, b in spans if a <= b)
        intervals[side] = available

    if config["auto_count"]:
        width = max(page["width"] for page, _, _, _ in rows)
        interval = max(1600, width * 2.5, max_height * 8, step)
        requested = max(1, min(600, round(total / interval)))
    else:
        requested = config["repeat_count"]
    count = min(max(0, requested - manual_count), 1 + max(0, total - max_height) // step)
    # Keep each requested mark in its own chapter band. Previously a global
    # feasibility pass packed every displaced mark into the first available
    # region, often the top of the next page. An obstructed band now loses its
    # mark instead of handing it to another band or restarting the distribution.
    if count == 0:
        return output
    pitch = total / count
    drift = pitch * .25
    floor = 0
    for i in range(count):
        ideal = (i + .5) * pitch - max_height / 2
        # Offsets may adjust a slot, but never collapse all slots at an edge.
        ideal += max(-drift, min(config["offset_y"], drift))
        choices = []
        for a, b, x, name, offset in intervals[modes[i % len(modes)]]:
            low = max(a, floor, round(ideal - drift))
            high = min(b, round(ideal + drift))
            if low <= high:
                y = max(low, min(round(ideal), high))
                choices.append((abs(y - ideal), y, x, name, offset))
        if not choices:
            continue
        _, y, x, name, offset = min(choices)
        output[name]["positions"].append((x, y - offset))
        floor = y + step
    return output
