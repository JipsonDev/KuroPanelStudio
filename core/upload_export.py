"""Export a chapter ready to upload: numbered slices of one height, in a .zip.

Pages are composed one at a time and streamed as one continuous strip, so a
60 000 px chapter never sits in memory whole. Each cut is moved, within
``search`` of the requested height, to the quietest row nearby (a gutter or
a plain background) so it does not pass through a balloon or a face.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image

FORMATS = {"JPG": ("jpg", "JPEG"), "WEBP": ("webp", "WEBP"), "PNG": ("png", "PNG")}


@dataclass
class UploadOptions:
    slice_height: int = 2000
    format_name: str = "JPG"
    quality: int = 90
    width: int = 0          # 0 keeps the width of the first page
    make_zip: bool = True
    search: float = 0.2     # how far a cut may move, as a share of the height
    prefix: str = ""


def safe_export_folder(folder: Path, pages, extension: str) -> Path:
    """Never write over the source images: export beside them in «Exportado»."""
    folder = Path(folder)
    sources = {Path(page.path).resolve() for page in pages if getattr(page, "path", None)}
    for page in pages:
        if getattr(page, "path", None) and (folder / f"{Path(page.name).stem}{extension}").resolve() in sources:
            return folder / "Exportado"
    return folder


def _row_quietness(rows: np.ndarray) -> np.ndarray:
    """Lower is quieter: spread of each row plus change from the row above."""
    gray = rows.astype(np.int16).mean(axis=2)
    spread = gray.std(axis=1)
    change = np.abs(np.diff(gray, axis=0, prepend=gray[:1])).mean(axis=1)
    return spread + change


TEXT_MARGIN = 12


def best_cut(buffer: np.ndarray, target: int, search: int,
             avoid: list[tuple[int, int]] | None = None) -> int:
    """Row index to cut at, near ``target``; prefers the row closest to it among equals.

    ``avoid`` holds row ranges (text boxes) a cut must not cross while any
    other row in the window is allowed.
    """
    low, high = max(1, target - search), min(len(buffer) - 1, target + search)
    if high <= low:
        return min(target, len(buffer))
    scores = _row_quietness(buffer[low:high + 1])
    distance = np.abs(np.arange(low, high + 1) - target) / max(1, search)
    scores = scores + distance * 2.0
    blocked = np.zeros(len(scores), bool)
    for top, bottom in avoid or ():
        start, end = max(low, top - TEXT_MARGIN), min(high, bottom + TEXT_MARGIN)
        if end >= start:
            blocked[start - low:end - low + 1] = True
    if not blocked.all():
        return int(low + np.argmin(np.where(blocked, np.inf, scores)))
    # A text block taller than the window: cut just before or after it,
    # as long as the slice stays between 40 % and 200 % of the height.
    edges = [edge for top, bottom in avoid or () for edge in (top - TEXT_MARGIN, bottom + TEXT_MARGIN)
             if target * .4 <= edge <= min(len(buffer) - 1, target * 2)
             and not any(other_top - TEXT_MARGIN < edge < other_bottom + TEXT_MARGIN
                         for other_top, other_bottom in avoid or ())]
    if edges:
        return int(min(edges, key=lambda edge: abs(edge - target)))
    return int(low + np.argmin(scores))


def _save(image: Image.Image, path: Path, options: UploadOptions) -> None:
    _extension, pil_format = FORMATS[options.format_name]
    if pil_format == "JPEG":
        image.convert("RGB").save(path, format="JPEG", quality=options.quality, subsampling=0)
    elif pil_format == "WEBP":
        image.save(path, format="WEBP", quality=options.quality, method=4)
    else:
        image.save(path, format="PNG", compress_level=6)


def export_for_upload(images: Iterable[Image.Image], destination: Path, options: UploadOptions,
                      progress: Callable[[int], None] = lambda _value: None,
                      cancelled: Callable[[], bool] = lambda: False, total: int = 0) -> list[Path]:
    """Write numbered slices (001, 002…) and, optionally, a .zip with them.

    ``images`` yields the composed pages in reading order, each optionally
    as ``(image, [(top, bottom), …])`` with the text rows a cut must avoid. Returns the files
    written; the .zip, if made, is the last entry.
    """
    if options.format_name not in FORMATS:
        raise ValueError(f"Formato no compatible: {options.format_name}")
    height = max(400, int(options.slice_height))
    search = max(0, int(height * options.search))
    extension = FORMATS[options.format_name][0]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    buffer: np.ndarray | None = None
    width = int(options.width) or 0

    def flush(rows: np.ndarray) -> None:
        path = destination / f"{options.prefix}{len(written) + 1:03d}.{extension}"
        _save(Image.fromarray(rows), path, options)
        written.append(path)

    avoid: list[tuple[int, int]] = []  # text rows, in buffer coordinates
    for index, item in enumerate(images, 1):
        if cancelled():
            return written
        image, ranges = item if isinstance(item, tuple) else (item, ())
        page = image.convert("RGB")
        width = width or page.width
        scale = 1.0
        if page.width != width:  # pages of another width are scaled to the strip
            scale = width / page.width
            page = page.resize((width, max(1, round(page.height * scale))), Image.LANCZOS)
        rows = np.asarray(page)
        offset = 0 if buffer is None else len(buffer)
        avoid.extend((offset + int(top * scale), offset + int(bottom * scale)) for top, bottom in ranges)
        buffer = rows if buffer is None else np.concatenate([buffer, rows])
        while len(buffer) >= height * 2 + search:
            cut = best_cut(buffer, height, search, avoid)
            flush(buffer[:cut])
            buffer = buffer[cut:]
            avoid = [(top - cut, bottom - cut) for top, bottom in avoid if bottom - cut > 0]
        progress(int(index * 95 / max(1, total)) if total else 50)
    while buffer is not None and len(buffer) >= height + search:
        cut = best_cut(buffer, height, search, avoid)
        flush(buffer[:cut])
        buffer = buffer[cut:]
        avoid = [(top - cut, bottom - cut) for top, bottom in avoid if bottom - cut > 0]
    if buffer is not None and len(buffer):
        # A short tail joins the previous slice instead of becoming a sliver.
        if written and len(buffer) < height * .25:
            with Image.open(written[-1]) as previous:
                merged = np.concatenate([np.asarray(previous.convert("RGB")), buffer])
            written.pop().unlink()
            flush(merged)
        else:
            flush(buffer)
    if options.make_zip and written:
        archive = destination.parent / f"{destination.name}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path in written:
                bundle.write(path, arcname=path.name)
        written.append(archive)
    progress(100)
    return written
