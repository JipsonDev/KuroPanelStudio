"""One dialogue font size for a whole chapter.

Each balloon is fitted on its own and takes the largest size that fits, so a
short line in a big balloon ends up much larger than the dialogue around it.
Here the sizes the balloons were actually fitted at are collected and every
dialogue box is limited to the chapter's typical size. Balloons that need a
smaller size to fit still shrink; no box becomes larger than it was asked to be.
"""
from __future__ import annotations

from statistics import median


def _dialogue(region: dict) -> bool:
    style = region.get("style") if isinstance(region.get("style"), dict) else {}
    text = str(region.get("applied_text") or region.get("translation") or region.get("text") or "")
    # Shouts, captions and SFX keep their own sizes; only speech is unified.
    return (bool(text.strip()) and not style.get("sfx_enabled", False) and style.get("auto_fit", True)
            and str(region.get("balloon_kind") or "dialogue") in {"dialogue", "thought"})


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
            style["font_size"] = min(current, size) if style.get("chapter_font_size") is None else size
            style["chapter_font_size"] = size
            style["auto_fit"] = True
            region["style"] = style
            region.pop("balloon_layout_snapshot", None)
            changed += 1
    return changed
