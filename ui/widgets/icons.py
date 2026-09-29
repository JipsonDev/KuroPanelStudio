"""Small, dependency-free Lucide-style SVG icons used across the interface."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


PATHS = {
    "text": '<path d="M4 6V4h16v2M12 4v16"/>',
    "script": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
    "tools": '<path d="m14.7 6.3 3 3L7 20l-4-4Z"/><path d="m16 3 5 5"/>',
    "sparkles": '<path d="m12 3-1.5 5.5L5 10l5.5 1.5L12 17l1.5-5.5L19 10l-5.5-1.5Z"/><path d="m19 15-.7 2.3L16 18l2.3.7L19 21l.7-2.3L22 18l-2.3-.7Z"/>',
    "effects": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M19.8 4.2l-2.1 2.1M6.3 17.7l-2.1 2.1"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.8 1.8 0 0 0 .35 2l.06.06-2.12 2.12-.06-.06a1.8 1.8 0 0 0-2-.35 1.8 1.8 0 0 0-1.1 1.65V21h-3v-.09A1.8 1.8 0 0 0 10.4 19a1.8 1.8 0 0 0-2 .35l-.06.06-2.12-2.12.06-.06a1.8 1.8 0 0 0 .35-2 1.8 1.8 0 0 0-1.65-1.1H5v-3h.09A1.8 1.8 0 0 0 7 10.03a1.8 1.8 0 0 0-.35-2l-.06-.06 2.12-2.12.06.06a1.8 1.8 0 0 0 2 .35A1.8 1.8 0 0 0 11.87 4.6V4h3v.09a1.8 1.8 0 0 0 1.1 1.65 1.8 1.8 0 0 0 2-.35l.06-.06 2.12 2.12-.06.06a1.8 1.8 0 0 0-.35 2 1.8 1.8 0 0 0 1.65 1.1H21v3h-.09A1.8 1.8 0 0 0 19.4 15Z"/>',
    "chip": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3M9 15l1.5-6 1.5 6M9.5 13h2M15 9v6"/>',
    "moon": '<path d="M20.5 15.5A8.5 8.5 0 1 1 8.5 3.5 6.8 6.8 0 0 0 20.5 15.5Z"/>',
    "crown": '<path d="m3 7 4 4 5-7 5 7 4-4-2 12H5Z"/><path d="M5 21h14"/>',
    "scan": '<path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2"/><path d="M7 12h10"/>',
    "languages": '<path d="M5 8h12M11 4c3 3 3 9 0 12M9 17h10M15 13c3 2 4 5 4 7M4 20l4-8 4 8M5.5 17h5"/>',
    "eraser": '<path d="m7 21-4-4 11-11 4 4-11 11Z"/><path d="m14 6 3-3 4 4-3 3M6 18h15"/>',
    "brush": '<path d="m14.5 4.5 5 5L9 20H4v-5Z"/><path d="m13 6 5 5M4 20c2-3 4-2 5 0"/>',
    "pipette": '<path d="m19 3 2 2-3.5 3.5 1 1-2 2-1-1L8 18H4v-4l7.5-7.5-1-1 2-2 1 1Z"/><path d="m6 14 4 4"/>',
    "workflow": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><path d="M10 6.5h3a2 2 0 0 1 2 2V14"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
    "images": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m21 15-5-5L5 20"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "eye": '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>',
    "eye-off": '<path d="m3 3 18 18"/><path d="M10.6 6.2A11 11 0 0 1 12 6c6.5 0 10 6 10 6a18 18 0 0 1-3 3.8M6.5 6.5C3.6 8.4 2 12 2 12s3.5 6 10 6c1.2 0 2.3-.2 3.3-.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "unlock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 7-2.6"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>',
    "arrow-up": '<path d="m6 10 6-6 6 6M12 4v16"/>',
    "arrow-down": '<path d="m6 14 6 6 6-6M12 20V4"/>',
    "hand": '<path d="M18 11V7a2 2 0 0 0-4 0v3M14 10V5a2 2 0 0 0-4 0v5M10 10V7a2 2 0 0 0-4 0v7c0 4 3 7 7 7h1c3.3 0 6-2.7 6-6v-4a2 2 0 0 0-4 0Z"/>',
    "mouse-pointer": '<path d="m5 3 14 8-6 2-3 6Z"/><path d="m13 13 5 5"/>',
    "zoom-in": '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4M11 8v6M8 11h6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4M8 11h6"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M3 16v3a2 2 0 0 0 2 2h3"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>', "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "more": '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
    "save": '<path d="M4 3h13l3 3v15H4Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6"/>',
    "clipboard": '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1M9 10h6M9 14h6"/>',
    "logo": '<path d="m4 8 4-4 4 4-4 4Z"/><path d="m12 8 4-4 4 4-4 4Z"/><path d="m8 16 4-4 4 4-4 4Z"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5M12 7v5l3 2"/>',
    "undo": '<path d="M9 7 4 12l5 5"/><path d="M5 12h8a6 6 0 0 1 6 6"/>',
    "redo": '<path d="m15 7 5 5-5 5"/><path d="M19 12h-8a6 6 0 0 0-6 6"/>',
    "refresh": '<path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M6.1 8.5A7 7 0 0 1 18.7 7L20 12M4 12l1.3 5A7 7 0 0 0 17.9 15.5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.7 2.2c-.8.5-1.2.9-1.2 1.8M12 17h.01"/>',
    "minus": '<path d="M5 12h14"/>',
    "square": '<rect x="5" y="5" width="14" height="14" rx="1"/>',
    "x": '<path d="m6 6 12 12M18 6 6 18"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "stamp": '<path d="M8 13h8l2 4H6Z"/><path d="M9 13V9a3 3 0 0 1 6 0v4"/><path d="M5 21h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',
    "alert": '<path d="M12 3 2.8 20h18.4Z"/><path d="M12 9v4M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
}


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#AAB8C5", size: int = 18) -> QIcon:
    """Return an implicitly-shared icon without rasterizing the SVG again.

    Layer lists can request hundreds of identical eye/lock icons while a page
    is loaded. QIcon is implicitly shared, so reusing it is both safe and much
    cheaper than constructing a QSvgRenderer for every row.
    """
    path = PATHS.get(name, PATHS["sparkles"])
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
