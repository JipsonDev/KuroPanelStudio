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
    reference_heights = {}
    for page in pages:
        if page["name"] in enabled:
            width, height = int(page["width"]), int(page["height"])
            reference_heights[width] = max(height, reference_heights.get(width, 0))
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
        settings["chapter_fit_height"] = reference_heights[width]
        size = (width, reference_heights[width])
        if size not in prepared:
            prepared[size] = prepare_watermark(size, config)
        mark = prepared[size]
        settings.update(positions=[], chapter_resolved=True)
        output[page["name"]] = settings
        if mark is not None and mark.height <= height and mark.width <= width:
            if manual and config["keep_inside"]:
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
    horizontal = config["anchor"].split("-")[-1]
    sides = ("left", "right") if horizontal == "left" else ("right", "left")
    modes = sides if config["distribution"] == "alternating" else (horizontal,)
    adaptive = config["distribution"] == "adaptive"
    if adaptive:
        modes = tuple(dict.fromkeys((horizontal, "left", "right", "center")))
    intervals = {}
    for side in modes:
        available = []
        for page, offset, size, _manual in rows:
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
                available.append((start + offset, end + offset, x, page["name"], offset))
        intervals[side] = available

    if config["auto_count"]:
        width = max(page["width"] for page, _, _, _ in rows)
        interval = max(config["interval_px"], step) if config["interval_px"] else max(1600, width * 2.5, max_height * 8, step)
        requested = max(1, min(600, round(total / interval)))
    else:
        requested = config["repeat_count"]
    count = min(requested, 1 + max(0, total - max_height) // step)
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
        for side in (modes if adaptive else (modes[i % len(modes)],)):
            for a, b, x, name, offset in intervals[side]:
                low = max(a, floor, round(ideal - drift))
                high = min(b, round(ideal + drift))
                if low <= high:
                    y = max(low, min(round(ideal), high))
                    choices.append((abs(y - ideal), y, x, name, offset))
            # Try another column only within this band, never move its mark
            # to the next band's beginning when dialogue blocks its side.
            if choices:
                break
        if not choices:
            continue
        _, y, x, name, offset = min(choices)
        output[name]["positions"].append((x, y - offset))
        floor = y + step

    # Resolve the automatic chapter slots first, independently of manual
    # edits. Subtracting manual counts and replanning changed the pitch and
    # alternating sides on every other page as soon as one mark was moved.
    obstacles = []
    for page, offset, (mw, mh), manual in rows:
        if manual is None:
            continue
        points = [(int(x), int(y)) for x, y in manual]
        output[page["name"]]["positions"] = points
        for x, y in points:
            # Unconstrained marks may be clipped by their own page. Reserve
            # only the visible portion, never phantom space on the next one.
            if x + mw <= 0 or x >= page["width"]:
                continue
            low, high = max(0, y), min(page["height"], y + mh)
            if low < high:
                obstacles.append((offset + low, offset + high))
    if obstacles:
        for page, offset, (_mw, mh), manual in rows:
            if manual is not None:
                continue
            # Keep all remaining slots where they were. Only omit a mark
            # whose gap conflicts with an explicitly positioned neighbour.
            output[page["name"]]["positions"] = [
                (x, y) for x, y in output[page["name"]]["positions"]
                if all(offset + y + mh + gap <= low or offset + y >= high + gap
                       for low, high in obstacles)
            ]
    return output
