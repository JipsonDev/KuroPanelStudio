"""One dialogue font size for a whole chapter.

Each balloon is fitted on its own and takes the largest size that fits, so a
short line in a big balloon ends up much larger than the dialogue around it.
Here the sizes the balloons were actually fitted at are collected and every
dialogue box is limited to the chapter's typical size. Balloons that need a
smaller size to fit still shrink; no box becomes larger than it was asked to be.
"""
from __future__ import annotations

from statistics import median
from typing import Callable

MEASURE_SAMPLE = 40


def _dialogue(region: dict) -> bool:
    style = region.get("style") if isinstance(region.get("style"), dict) else {}
    text = str(region.get("applied_text") or region.get("translation") or region.get("text") or "")
    # Shouts, captions and SFX keep their own sizes; only speech is unified.
    return (bool(text.strip()) and not style.get("sfx_enabled", False) and style.get("auto_fit", True)
            and str(region.get("balloon_kind") or "dialogue") in {"dialogue", "thought"})


def _refresh_requested(style: dict) -> None:
    """Forget the remembered size when the size was changed since unifying.

    A font role, a box kind or the text panel may have set another size: that
    newer choice is the one to start from, not the one remembered before.
    """
    if "font_size_requested" in style and style.get("font_size") != style.get("chapter_font_applied"):
        for name in ("font_size_requested", "chapter_font_size", "chapter_font_applied"):
            style.pop(name, None)


def fitted_size(region: dict) -> int | None:
    snapshot = region.get("balloon_layout_snapshot")
    if isinstance(snapshot, dict) and not region.get("text_overflow"):
        try:
            return int(snapshot["font_size"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def chapter_font_size(pages: dict[str, list[dict]], minimum_samples: int = 3) -> int | None:
    """The typical fitted dialogue size, or None with too few fitted balloons."""
    sizes = [size for regions in pages.values() for region in regions
             if _dialogue(region) and (size := fitted_size(region))]
    if len(sizes) < minimum_samples:
        return None
    return int(round(median(sizes)))


def unify_chapter_font_size(pages: dict[str, list[dict]], size: int) -> int:
    """Cap every dialogue box at ``size``; returns how many boxes changed."""
    changed = 0
    for regions in pages.values():
        for region in regions:
            if not _dialogue(region):
                continue
            style = dict(region.get("style") or {})
            current = int(style.get("font_size", size) or size)
            if current <= size and style.get("chapter_font_size") == size:
                continue
            # Remember the size chosen before the first unification, so a
            # later measurement starts from it instead of the capped value.
            _refresh_requested(style)
            style.setdefault("font_size_requested", current)
            style["font_size"] = min(int(style["font_size_requested"]), size)
            style["chapter_font_applied"] = style["font_size"]
            style["chapter_font_size"] = size
            style["auto_fit"] = True
            region["style"] = style
            region.pop("balloon_layout_snapshot", None)
            changed += 1
    return changed


def measure_chapter_font_size(pages: list[tuple[object, list[dict]]], load_image: Callable,
                              default_style: dict | None = None, cancelled: Callable[[], bool] = lambda: False,
                              progress: Callable[[int], None] = lambda _value: None,
                              sample: int = MEASURE_SAMPLE) -> int | None:
    """Fit a spread sample of the chapter's dialogue, as exported, and take the median.

    ``pages`` holds (page, regions) in reading order; ``load_image(page)``
    returns its image. Up to ``sample`` boxes are measured, evenly spread.
    """
    from core.export_manager import fit_region_in_balloon
    boxes = [(page, region) for page, regions in pages for region in regions if _dialogue(region)]
    if len(boxes) > sample:
        step = len(boxes) / sample
        boxes = [boxes[int(index * step)] for index in range(sample)]
    sizes, image, image_page = [], None, None
    for number, (page, region) in enumerate(boxes, 1):
        if cancelled():
            return None
        if page is not image_page:
            image, image_page = load_image(page), page
        style = dict(region.get("style") or {})
        _refresh_requested(style)
        if "font_size_requested" in style:
            style["font_size"] = style["font_size_requested"]
        layout = fit_region_in_balloon(image, {**region, "style": style}, default_style)
        if layout is not None:
            sizes.append(int(layout["font_size"]))
        progress(int(number * 100 / max(1, len(boxes))))
    return int(round(median(sizes))) if len(sizes) >= 3 else None
