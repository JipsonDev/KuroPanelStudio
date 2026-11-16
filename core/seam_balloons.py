"""Balloons cut by the join between two consecutive pages of a strip.

A tall webtoon strip is saved as several pages, and a balloon that crosses
the cut is detected as two boxes: the bottom of one page and the top of the
next. Read separately, each half holds the halves of the same lines and the
OCR returns fragments. Here both halves are pasted back together, read once,
and the text goes to the larger half; the other half keeps its box (so it is
still cleaned) with no text of its own.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from PIL import Image

SEAM_PARTNER_KEY = "seam_partner"
EDGE_MARGIN = 6
MIN_OVERLAP = 0.5


def _box(region: dict) -> tuple[int, int, int, int]:
    return (int(region.get("x", 0)), int(region.get("y", 0)),
            max(1, int(region.get("width", 0))), max(1, int(region.get("height", 0))))


def seam_pairs(pages: list[tuple[str, tuple[int, int], list[dict]]],
               margin: int = EDGE_MARGIN, min_overlap: float = MIN_OVERLAP) -> list[tuple]:
    """Return ``((upper_key, upper_region), (lower_key, lower_region))`` pairs.

    ``pages`` are consecutive pages in reading order with their size; a box
    must touch the bottom edge of one page, its partner the top edge of the
    next, and they must share at least ``min_overlap`` of the narrower width.
    Each box joins one partner at most: the best-overlapping one.
    """
    pairs = []
    for (upper_key, (upper_width, upper_height), upper_regions), (lower_key, (lower_width, _), lower_regions) in zip(pages, pages[1:]):
        if upper_width != lower_width:
            continue  # pages of different widths were not cut from one strip
        bottoms = [region for region in upper_regions if sum(_box(region)[1::2]) >= upper_height - margin]
        tops = [region for region in lower_regions if _box(region)[1] <= margin]
        taken: set[int] = set()
        for upper in bottoms:
            ux, _, uw, _ = _box(upper)
            best, best_share = None, min_overlap
            for lower in tops:
                if id(lower) in taken:
                    continue
                lx, _, lw, _ = _box(lower)
                shared = min(ux + uw, lx + lw) - max(ux, lx)
                share = shared / min(uw, lw)
                if share >= best_share:
                    best, best_share = lower, share
            if best is not None:
                taken.add(id(best))
                pairs.append(((upper_key, upper), (lower_key, best)))
    return pairs


def stitch(upper_image: Image.Image, upper: dict, lower_image: Image.Image, lower: dict) -> Image.Image:
    """Paste the two halves over their common column range, as they were before the cut."""
    ux, uy, uw, uh = _box(upper)
    lx, ly, lw, lh = _box(lower)
    left, right = min(ux, lx), max(ux + uw, lx + lw)
    top_part = upper_image.convert("RGB").crop((left, uy, right, min(upper_image.height, uy + uh)))
    bottom_part = lower_image.convert("RGB").crop((left, 0, right, min(lower_image.height, ly + lh)))
    joined = Image.new("RGB", (right - left, top_part.height + bottom_part.height), "white")
    joined.paste(top_part, (0, 0))
    joined.paste(bottom_part, (0, top_part.height))
    return joined


def stitched_file(upper_path: Path, upper: dict, lower_path: Path, lower: dict,
                  directory: Path | None = None) -> Path:
    """Write the joined crop once; the same name and date keep the OCR cache valid."""
    signature = []
    for path, region in ((upper_path, upper), (lower_path, lower)):
        stat = Path(path).stat()
        signature.append(f"{Path(path).resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{_box(region)}")
    name = hashlib.sha256("\n".join(signature).encode("utf-8")).hexdigest()[:24] + ".png"
    folder = Path(directory or Path(tempfile.gettempdir()) / "kuropanel-seams")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not target.exists():
        with Image.open(upper_path) as top, Image.open(lower_path) as bottom:
            joined = stitch(top, upper, bottom, lower)
        temporary = target.with_suffix(".tmp.png")
        joined.save(temporary)
        temporary.replace(target)
    return target


def anchor_first(upper: dict, lower: dict) -> tuple[dict, dict]:
    """The half that receives the text (the taller one; the upper on a tie) and the other."""
    return (lower, upper) if _box(lower)[3] > _box(upper)[3] else (upper, lower)
