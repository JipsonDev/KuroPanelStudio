"""Project state independent from the GUI."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from core.psd_manager import inspect_psd, is_psd


@dataclass
class Page:
    name: str
    path: Path | None = None
    width: int = 0
    height: int = 0
    size_label: str = "—"
    source_layers: list[dict] = field(default_factory=list)


@dataclass
class ProjectManager:
    pages: list[Page] = field(default_factory=list)
    active_index: int = 0

    @property
    def active_page(self) -> Page:
        if not self.pages:
            raise IndexError("No hay imágenes cargadas en el proyecto.")
        return self.pages[self.active_index]

    def set_active(self, index: int) -> Page:
        if not self.pages:
            raise IndexError("No hay imágenes cargadas en el proyecto.")
        self.active_index = max(0, min(index, len(self.pages) - 1))
        return self.active_page

    def load_folder(self, folder: str) -> None:
        pages = self.scan_folder(Path(folder))
        if pages:
            self.pages = pages
            self.active_index = 0

    @classmethod
    def scan_folder(cls, folder: Path, progress=None, cancelled=None) -> list[Page]:
        """Read page metadata without decoding pixels; safe for a worker thread."""
        extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".psd", ".psb"}
        paths = [path for path in sorted(folder.iterdir()) if path.suffix.lower() in extensions]
        pages: list[Page] = []
        total = max(1, len(paths))
        for index, path in enumerate(paths, start=1):
            if cancelled and cancelled():
                break
            pages.append(cls._page_from_path(path))
            if progress:
                progress(int(index * 100 / total))
        return pages

    @staticmethod
    def _page_from_path(path: Path) -> Page:
        layers: list[dict] = []
        if is_psd(path):
            width, height, layers = inspect_psd(path)
        else:
            # Image.open only parses the raster header here. Calling
            # load_source_image converted every complete webtoon page to RGB
            # merely to obtain its dimensions, delaying the first page by the
            # total decode time of the whole chapter.
            with Image.open(path) as image:
                width, height = image.size
        return Page(path.name, path, width, height, f"{path.stat().st_size / 1024 / 1024:.1f} MB", layers)
