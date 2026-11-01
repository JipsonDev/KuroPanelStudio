"""Virtual chapter geometry, bounded page access and streaming PNG export."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import struct
import threading
import uuid
import zlib

from PIL import Image

from core.export_manager import compose_page
from core.performance_manager import ByteLRUCache
from core.project_io import page_key
from core.project_manager import Page


@dataclass(frozen=True)
class StripSpan:
    index: int
    x: int
    y: int
    width: int
    height: int


class ChapterStrip:
    """Keep original pages separate; decode only the page a crop intersects.

    Geometry comes entirely from project metadata. Different widths are
    centered on white without resampling. Page styles/regions are a snapshot
    owned by this source; all edits stay in the normal project dictionaries.
    """

    def __init__(
        self, pages: list[Page], regions: dict | None = None,
        clean_results: dict | None = None, styles: dict | None = None,
        cache_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if not pages or any(p.width <= 0 or p.height <= 0 for p in pages):
            raise ValueError("La tira necesita páginas con dimensiones válidas.")
        self.pages = tuple(pages)
        self.width = max(p.width for p in pages)
        self.height = sum(p.height for p in pages)
        self.regions = regions or {}
        self.clean_results = clean_results or {}
        self.styles = styles or {}
        self.spans = []
        offset = 0
        for index, page in enumerate(pages):
            self.spans.append(StripSpan(index, (self.width - page.width) // 2,
                                       offset, page.width, page.height))
            offset += page.height
        self._ends = [s.y + s.height for s in self.spans]
        self._cache = ByteLRUCache[Image.Image](max(0, int(cache_bytes)))
        self._lock = threading.RLock()

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def page_at(self, y: float) -> int:
        return min(len(self.pages) - 1, max(0, bisect_right(self._ends, y)))

    def intersecting(self, top: float, bottom: float) -> list[StripSpan]:
        if bottom <= top or bottom <= 0 or top >= self.height:
            return []
        start = self.page_at(max(0, top))
        end = self.page_at(min(self.height - 1, bottom - 1))
        return self.spans[start:end + 1]

    def render_page(self, index: int, *, cache: bool = True) -> Image.Image:
        with self._lock:
            image = self._cache.get(index) if cache else None
            if image is not None:
                return image
            page = self.pages[index]
            key = page_key(page)
            image = compose_page(page, self.regions.get(key, []),
                                 self.clean_results.get(key), self.styles.get(key, {}))
            if image.mode != "RGB":
                image = image.convert("RGB")
            if image.size != (page.width, page.height):
                raise ValueError(f"Cambió el tamaño de {page.name}; vuelve a abrir la tira.")
            if cache and page.width * page.height * 3 <= self._cache.max_bytes:
                self._cache.put(index, image, page.width * page.height * 3)
            return image

    def crop(self, box: tuple[int, int, int, int]) -> Image.Image:
        left, top, right, bottom = map(int, box)
        if right <= left or bottom <= top:
            raise ValueError("El recorte de la tira está vacío.")
        result = Image.new("RGB", (right - left, bottom - top), "white")
        for span in self.intersecting(top, bottom):
            x1, y1 = max(left, span.x), max(top, span.y)
            x2, y2 = min(right, span.x + span.width), min(bottom, span.y + span.height)
            if x2 <= x1 or y2 <= y1:
                continue
            page = self.render_page(span.index)
            fragment = page.crop((x1 - span.x, y1 - span.y, x2 - span.x, y2 - span.y))
            result.paste(fragment, (x1 - left, y1 - top))
        return result

    def cache_stats(self) -> dict:
        with self._lock:
            return self._cache.stats()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def export_strip_png(
    destination: Path, strip: ChapterStrip,
    progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Write PNG scanlines incrementally; never allocate a chapter raster.

    Memory is bounded by one composed source page plus a 64-row buffer and
    zlib state. Replace the destination only after the complete PNG is closed.
    """
    progress = progress or (lambda _: None)
    cancelled = cancelled or (lambda: False)
    destination = Path(destination)
    if max(strip.size) > 0x7fffffff:
        raise ValueError("Las dimensiones de la tira superan el límite PNG.")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    strip.clear()

    def check_cancelled():
        if cancelled():
            raise InterruptedError("Exportación de tira cancelada.")

    try:
        check_cancelled()
        with temporary.open("wb") as stream:
            def chunk(kind, payload):
                stream.write(struct.pack("!I", len(payload)))
                stream.write(kind)
                stream.write(payload)
                stream.write(struct.pack("!I", zlib.crc32(payload, zlib.crc32(kind)) & 0xffffffff))

            stream.write(b"\x89PNG\r\n\x1a\n")
            chunk(b"IHDR", struct.pack("!IIBBBBB", strip.width, strip.height, 8, 2, 0, 0, 0))
            compressor = zlib.compressobj(level=6)
            for span in strip.spans:
                check_cancelled()
                page = strip.render_page(span.index, cache=False)
                for top in range(0, span.height, 64):
                    check_cancelled()
                    bottom = min(span.height, top + 64)
                    band = Image.new("RGB", (strip.width, bottom - top), "white")
                    band.paste(page.crop((0, top, span.width, bottom)), (span.x, 0))
                    raw = band.tobytes()
                    stride = strip.width * 3
                    scanlines = b"".join(b"\0" + raw[i:i + stride] for i in range(0, len(raw), stride))
                    compressed = compressor.compress(scanlines)
                    if compressed:
                        chunk(b"IDAT", compressed)
                    progress(int((span.y + bottom) * 100 / strip.height))
                del page
            check_cancelled()
            chunk(b"IDAT", compressor.flush())
            chunk(b"IEND", b"")
        check_cancelled()
        temporary.replace(destination)
        progress(100)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
