from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget

from core.image_manager import ImageManager
from core.project_manager import Page
from ui.widgets.controls import ModernButton, SectionTitle


class ProjectImageList(QListWidget):
    folder_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path:
                    self.folder_dropped.emit(path)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)


class ImageListItem(QWidget):
    STATUS_FIELDS = (
        ("detected", "D", "Cajas detectadas"),
        ("ocr", "O", "OCR completado"),
        ("cleaned", "L", "Imagen limpiada"),
        ("translated", "T", "Traducción completada"),
        ("typeset", "R", "Página rotulada"),
    )

    def __init__(self, page: Page) -> None:
        super().__init__()
        layout = QHBoxLayout(self); layout.setContentsMargins(4, 2, 4, 2); layout.setSpacing(7)
        details = QVBoxLayout(); details.setContentsMargins(0, 0, 0, 0); details.setSpacing(3)
        name = QLabel(page.name); name.setToolTip(str(page.path) if page.path else page.name)
        details.addWidget(name)
        state_row = QHBoxLayout(); state_row.setContentsMargins(0, 0, 0, 0); state_row.setSpacing(3)
        self.status_badges: dict[str, QLabel] = {}
        for key, short, tooltip in self.STATUS_FIELDS:
            badge = QLabel(short)
            badge.setObjectName("PageStatusBadge")
            badge.setProperty("done", False)
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedSize(17, 15)
            badge.setToolTip(tooltip)
            state_row.addWidget(badge)
            self.status_badges[key] = badge
        state_row.addStretch()
        details.addLayout(state_row)
        layout.addLayout(details, 1)
        more = ModernButton("⋮"); more.setFixedSize(24, 26); more.setToolTip("Opciones de imagen"); layout.addWidget(more)

    def set_status(self, status: dict[str, bool]) -> None:
        for key, badge in self.status_badges.items():
            badge.setProperty("done", bool(status.get(key, False)))
            badge.style().unpolish(badge)
            badge.style().polish(badge)


class ImagesPanel(QFrame):
    page_selected = Signal(int)
    folder_dropped = Signal(str)
    thumbnail_range_requested = Signal(int, int)

    def __init__(self, pages: list[Page], image_manager: ImageManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.pages = pages
        self.image_manager = image_manager
        layout = QVBoxLayout(self); layout.setContentsMargins(11, 10, 11, 9); layout.setSpacing(5)
        self.title = SectionTitle(f"Lista de imágenes ({len(pages)})")
        layout.addWidget(self.title)
        self.folder = QLabel("Sin capítulo cargado")
        self.folder.setObjectName("Muted"); layout.addWidget(self.folder)
        self.legend = QLabel("D Detectada · O OCR · L Limpia · T Traducida · R Rotulada")
        self.legend.setObjectName("Caption")
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        self.list = ProjectImageList()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.list.currentRowChanged.connect(self.page_selected)
        self.list.folder_dropped.connect(self.folder_dropped)
        layout.addWidget(self.list, 1)
        self.footer = QLabel("Capítulo sin imágenes cargadas")
        self.footer.setObjectName("Muted"); layout.addWidget(self.footer)
        self.refresh_pages(pages)

    def refresh_pages(self, pages: list[Page]) -> None:
        self.pages = pages
        self.title.setText(f"LISTA DE IMÁGENES ({len(pages)})")
        self.footer.setText(f"Capítulo actual: {len(pages)} imágenes")
        if pages and pages[0].path:
            folder_path = pages[0].path.parent
            self.folder.setText(f"Carpeta: {folder_path.name}")
            self.folder.setToolTip(str(folder_path))
        else:
            self.folder.setText("Sin capítulo cargado")
            self.folder.setToolTip("")
        self.list.blockSignals(True)
        self.list.clear()
        for page in pages:
            item = QListWidgetItem()
            widget = ImageListItem(page)
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        if pages:
            self.list.setCurrentRow(0)
            self.list.scrollToTop()
        self.list.blockSignals(False)
    def request_visible_thumbnails(self) -> None:
        """Compatibility no-op: page thumbnails are intentionally disabled."""
        return

    def showEvent(self, event) -> None:
        super().showEvent(event)

    def set_thumbnail(self, index: int, image) -> None:
        """Compatibility no-op retained for older callers/project sessions."""
        return

    def set_statuses(self, statuses: list[dict[str, bool]]) -> None:
        completed = 0
        for index in range(self.list.count()):
            widget = self.list.itemWidget(self.list.item(index))
            status = statuses[index] if index < len(statuses) else {}
            if isinstance(widget, ImageListItem):
                widget.set_status(status)
            if status.get("typeset", False):
                completed += 1
        self.footer.setText(f"Capítulo actual: {len(self.pages)} imágenes · {completed} rotuladas")

    def _show_menu(self, position) -> None:
        menu = QMenu(self)
        menu.addAction("Duplicar")
        menu.addAction("Cambiar nombre")
        menu.addSeparator()
        menu.addAction("Eliminar")
        menu.exec(self.list.mapToGlobal(position))
