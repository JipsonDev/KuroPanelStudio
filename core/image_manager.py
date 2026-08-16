"""Lazy image access and an intentionally small thumbnail cache."""
from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap

from core.performance_manager import ByteLRUCache
from core.psd_manager import is_psd, load_source_image, state_signature


class ImageManager:
    def __init__(self, cache_limit: int = 32, memory_limit_mb: int = 384) -> None:
        self.cache_limit = cache_limit
        total_bytes = max(64, int(memory_limit_mb)) * 1024 * 1024
        thumbnail_bytes = min(32 * 1024 * 1024, total_bytes // 8)
        self._thumbnails = ByteLRUCache[QImage](thumbnail_bytes)
        self._pages = ByteLRUCache[QImage](max(32 * 1024 * 1024, total_bytes - thumbnail_bytes))
        self._cache_lock = threading.RLock()
        self._active_path: object | None = None
        self._active_image: QImage | None = None

    def clear(self) -> None:
        with self._cache_lock:
            self._thumbnails.clear()
            self._pages.clear()
            self._active_path = None
            self._active_image = None

    def invalidate(self, path: Path) -> None:
        """Drop every cached render/thumbnail for one externally changed file."""
        target = Path(path).resolve()

        def belongs_to_path(key: object) -> bool:
            candidate = key[0] if isinstance(key, tuple) and key else key
            try:
                return Path(candidate).resolve() == target
            except (TypeError, ValueError):
                return False

        with self._cache_lock:
            self._thumbnails.remove_if(belongs_to_path)
            self._pages.remove_if(belongs_to_path)
            if self._active_path is not None and belongs_to_path(self._active_path):
                self._active_path = None
                self._active_image = None

    def set_memory_limit_mb(self, value: int) -> None:
        total_bytes = max(64, int(value)) * 1024 * 1024
        thumbnail_bytes = min(32 * 1024 * 1024, total_bytes // 8)
        with self._cache_lock:
            self._thumbnails.resize(thumbnail_bytes)
            self._pages.resize(max(32 * 1024 * 1024, total_bytes - thumbnail_bytes))

    @staticmethod
    def _pil_qimage(image) -> QImage:
        rgb = image.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        return QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()

    def thumbnail_image(self, path: Path | None, size: int = 38) -> QImage | None:
        """Decode a tiny first-panel preview; safe outside the GUI thread."""
        if path is None or not path.exists():
            return None
        with self._cache_lock:
            cached = self._thumbnails.get((path, int(size)))
        if cached is not None:
            return cached
        if is_psd(path):
            pil = load_source_image(path)
            preview_height = min(pil.height, max(1, int(pil.width * 1.35)))
            pil = pil.crop((0, 0, pil.width, preview_height))
            pil.thumbnail((size, max(1, int(size * 1.35))))
            image = self._pil_qimage(pil)
            with self._cache_lock:
                self._thumbnails.put((path, int(size)), image, int(image.sizeInBytes()))
            return image
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            preview_height = min(source_size.height(), max(1, int(source_size.width() * 1.35)))
            reader.setClipRect(QRect(0, 0, source_size.width(), preview_height))
            reader.setScaledSize(QSize(size, int(size * 1.35)))
        image = reader.read()
        if image.isNull():
            return None
        with self._cache_lock:
            self._thumbnails.put((path, int(size)), image, int(image.sizeInBytes()))
        return image

    def thumbnail(self, path: Path | None, size: int = 38) -> QPixmap | None:
        image = self.thumbnail_image(path, size)
        return QPixmap.fromImage(image) if image is not None else None

    def active_image(self, path: Path | None, source_states: dict[str, dict] | None = None) -> QImage | None:
        """Decode the full-resolution active page in a worker thread."""
        if path is None or not path.exists():
            return None
        with self._cache_lock:
            cache_key = (path, state_signature(source_states))
            if self._active_path == cache_key and self._active_image is not None:
                return self._active_image
            cached = self._pages.get(cache_key)
            if cached is not None:
                self._active_path, self._active_image = cache_key, cached
                return cached
        if is_psd(path):
            image = self._pil_qimage(load_source_image(path, source_states))
        else:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                raise RuntimeError(reader.errorString() or f"No se pudo abrir {path.name}.")
        with self._cache_lock:
            self._active_path = cache_key
            self._active_image = image
            self._pages.put(cache_key, image, int(image.sizeInBytes()))
        return image

    def cached_active_image(
        self, path: Path | None, source_states: dict[str, dict] | None = None,
    ) -> QImage | None:
        """Return a decoded page immediately without touching the filesystem."""
        if path is None:
            return None
        cache_key = (path, state_signature(source_states))
        with self._cache_lock:
            if self._active_path == cache_key and self._active_image is not None:
                return self._active_image
            return self._pages.get(cache_key)

    def prefetch_image(
        self, path: Path | None, source_states: dict[str, dict] | None = None,
    ) -> QImage | None:
        """Decode a likely next page without replacing the active fast path."""
        if path is None or not path.exists():
            return None
        cache_key = (path, state_signature(source_states))
        with self._cache_lock:
            cached = self._pages.get(cache_key)
        if cached is not None:
            return cached
        if is_psd(path):
            image = self._pil_qimage(load_source_image(path, source_states))
        else:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                return None
        with self._cache_lock:
            self._pages.put(cache_key, image, int(image.sizeInBytes()))
        return image

    def preview_image(self, path: Path | None, max_width: int = 480) -> QImage | None:
        """Decode a reduced complete strip for progressive first paint."""
        if (
            path is None or not path.exists() or is_psd(path)
            or path.suffix.casefold() not in {".jpg", ".jpeg", ".webp"}
        ):
            return None
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        target_width = max(160, int(max_width))
        if not size.isValid() or size.width() <= target_width:
            return None
        # Decode only the first visible section. Scaling the complete 60k-px
        # strip was slower than reading the original PNG because libpng still
        # had to traverse every scanline.
        source_height = min(size.height(), max(900, int(round(size.width() * 1.6))))
        reader.setClipRect(QRect(0, 0, size.width(), source_height))
        target_height = max(1, int(round(source_height * target_width / size.width())))
        reader.setScaledSize(QSize(target_width, target_height))
        image = reader.read()
        return None if image.isNull() else image

    def cache_stats(self) -> dict[str, dict[str, int]]:
        return {"thumbnails": self._thumbnails.stats(), "pages": self._pages.stats()}

    def retain_pages(self, paths: set[Path] | list[Path] | tuple[Path, ...]) -> int:
        """Release decoded pages outside the active profile's working set.

        Cache keys also contain the PSD layer-state signature, therefore the
        comparison is deliberately made against the path portion only.  The
        current page is always retained even if a caller passes an empty set.
        """
        retained = {Path(path).resolve() for path in paths}
        with self._cache_lock:
            if self._active_path is not None:
                active_key = self._active_path
                active_path = active_key[0] if isinstance(active_key, tuple) else active_key
                try:
                    retained.add(Path(active_path).resolve())
                except (TypeError, ValueError, OSError):
                    pass

            def outside_working_set(key: object) -> bool:
                candidate = key[0] if isinstance(key, tuple) and key else key
                try:
                    return Path(candidate).resolve() not in retained
                except (TypeError, ValueError, OSError):
                    return True

            return self._pages.remove_if(outside_working_set)

    def trim_memory(self) -> None:
        """Drop inactive pages/thumbnails while retaining the visible page."""
        with self._cache_lock:
            self._thumbnails.clear()
            self._pages.clear()
            if self._active_path is not None and self._active_image is not None:
                self._pages.put(
                    self._active_path, self._active_image, int(self._active_image.sizeInBytes()),
                )

    def active_pixmap(self, path: Path | None) -> QPixmap | None:
        """Load the active page as one original-resolution canvas image.

        The editor holds only this active image in memory; it does not pre-load
        the rest of the chapter. That preserves the full vertical strip and
        makes scrolling/typing immediate without a reduced-quality preview.
        """
        if path is None or not path.exists():
            return None
        image = self.active_image(path)
        return QPixmap.fromImage(image) if image is not None else None
