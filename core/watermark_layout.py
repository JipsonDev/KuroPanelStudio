"""Deterministic placement in free vertical intervals, without pixel scanning."""
from __future__ import annotations


def free_intervals(x, bounds, mark_size, regions):
    """Allowed integer top coordinates, with clearance around reading regions."""
    width, height = mark_size
    padding = max(16, min(width, height) // 4)
    low, high = bounds
    blocked = sorted(
        (max(low, r["y"] - height - padding + 1),
         min(high, r["y"] + r["height"] + padding - 1))
        for r in regions
        if r["width"] > 0 and r["height"] > 0
        and x + width + padding > r["x"]
        and x < r["x"] + r["width"] + padding
    )
    free = []
    cursor = low
    for start, end in blocked:
        if end < cursor or start > high:
            continue
        if start > cursor:
            free.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= high:
        free.append((cursor, high))
    return free


def balanced_positions(xs, ideals, bounds, mark_size, regions, minimum_gap):
    """Place every requested mark or return None if that count cannot fit.

    A backwards feasibility pass reserves space for later marks before the
    forwards pass chooses the nearest available position to each ideal.
    """
    if not xs:
        return []
    free = {x: free_intervals(x, bounds, mark_size, regions) for x in set(xs)}
    step = mark_size[1] + minimum_gap
    latest = [0] * len(xs)
    ceiling = bounds[1]
    for index in range(len(xs) - 1, -1, -1):
        candidates = [min(end, ceiling) for start, end in free[xs[index]] if start <= ceiling]
        if not candidates:
            return None
        latest[index] = max(candidates)
        ceiling = latest[index] - step
    floor = bounds[0]
    placed = []
    for index, (x, ideal) in enumerate(zip(xs, ideals)):
        candidates = [max(start, floor, min(round(ideal), end, latest[index]))
                      for start, end in free[x]
                      if max(start, floor) <= min(end, latest[index])]
        if not candidates:
            return None
        y = min(candidates, key=lambda candidate: (abs(candidate - ideal), candidate))
        placed.append((x, y))
        floor = y + step
    return placed
