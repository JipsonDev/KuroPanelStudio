from __future__ import annotations

from collections import deque

from PySide6.QtCore import QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QBoxLayout, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QSizePolicy, QVBoxLayout, QWidget)

from core.image_manager import ImageManager
from core.project_manager import Page
from ui.widgets.controls import ModernButton, SectionTitle
from ui.widgets.icons import icon


class ProjectImageList(QListWidget):
    folder_dropped = Signal(str)
    viewport_resized = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.source_to_row: dict[int, int] = {}

    def setCurrentRow(self, source_index: int) -> None:
        # MainWindow works in chapter order even when this view is reversed.
        super().setCurrentRow(self.source_to_row.get(source_index, source_index))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.viewport_resized.emit()

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


class ThumbnailTask(QRunnable):
    """Decode a visible thumbnail off the UI thread; return a QImage, not a pixmap."""

    def __init__(self, manager, path, index, generation, finished) -> None:
        super().__init__()
        self.manager = manager
        self.path = path
        self.index = index
        self.generation = generation
        self.finished = finished

    def run(self) -> None:
        try:
            image = self.manager.thumbnail_image(self.path, 72)
        except (OSError, ValueError, RuntimeError):
            image = None
        self.finished.emit(self.index, self.generation, image)


class ElidedLabel(QLabel):
    """Keep long page names inside the thumbnail row at every dock width."""

    def __init__(self, value: str) -> None:
        super().__init__()
        self.full_text = value
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._fit_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_text()

    def setText(self, value: str) -> None:
        self.full_text = value
        self._fit_text()

    def _fit_text(self) -> None:
        available = max(0, self.contentsRect().width() - 2)
        value = self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, available)
        if self.text() != value:
            super().setText(value)


class ImageListItem(QWidget):
    STATUS_FIELDS = (
        ("detected", "D", "Cajas detectadas"),
        ("ocr", "O", "OCR completado"),
        ("cleaned", "L", "Imagen limpiada"),
        ("translated", "T", "Traducción completada"),
        ("typeset", "R", "Página rotulada"),
        ("error", "!", "Error de proceso en esta página"),
    )

    def __init__(self, page: Page) -> None:
        super().__init__()
        self.setObjectName("PageRow")
        layout = QHBoxLayout(self); layout.setContentsMargins(8, 7, 7, 7); layout.setSpacing(9)
        self._grid_mode = False
        self.thumbnail = QLabel()
        self.thumbnail.setObjectName("PageThumbnail")
        self.thumbnail.setAlignment(Qt.AlignCenter)
        self.thumbnail.setFixedSize(64, 64)
        self.thumbnail.setPixmap(icon("images", "#6E8098", 28).pixmap(QSize(28, 28)))
        self._thumbnail_pixmap = None
        self._thumbnail_target = QSize()
        layout.addWidget(self.thumbnail)
        details = QVBoxLayout(); details.setContentsMargins(0, 0, 0, 0); details.setSpacing(3)
        self.name = ElidedLabel(page.name); self.name.setObjectName("PageName")
        self.name.setProperty("kuro_i18n_ignore", True)
        self.name.setToolTip(str(page.path) if page.path else page.name)
        details.addWidget(self.name)
        dimensions = QLabel(f"{page.width} × {page.height} px" if page.width else "Dimensiones pendientes")
        dimensions.setObjectName("PageMeta"); details.addWidget(dimensions)
        dimensions.setMinimumWidth(0)
        dimensions.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.dimensions = dimensions
        # Folder scanning already collected this metadata in a worker. Avoid
        # one extra disk access per row while the chapter list is built in Qt.
        size = page.size_label if page.path and page.size_label != "—" else ""
        self.file_size = QLabel(size); self.file_size.setObjectName("PageMeta")
        self.file_size.setMinimumWidth(0)
        self.file_size.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        details.addWidget(self.file_size)
        self.badges = QWidget()
        state_row = QHBoxLayout(self.badges); state_row.setContentsMargins(0, 0, 0, 0); state_row.setSpacing(3)
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
        details.addWidget(self.badges)
        layout.addLayout(details, 1)
        self.more = ModernButton("", icon_name="more"); self.more.setFixedSize(26, 30)
        self.more.setAccessibleName(f"Opciones de {page.name}")
        self.more.setToolTip("Opciones de imagen"); layout.addWidget(self.more)

    def set_thumbnail(self, image) -> None:
        pixmap = QPixmap.fromImage(image) if hasattr(image, "format") else image
        if pixmap is not None and not pixmap.isNull():
            self._thumbnail_pixmap = pixmap
            self._thumbnail_target = self.thumbnail.size()
            self.thumbnail.setPixmap(pixmap.scaled(self.thumbnail.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def set_grid_mode(self, enabled: bool) -> None:
        self._grid_mode = enabled
        self.layout().setDirection(QBoxLayout.TopToBottom if enabled else QBoxLayout.LeftToRight)
        self.thumbnail.setFixedSize(118, 80) if enabled else self.thumbnail.setFixedSize(64, 64)
        if self._thumbnail_pixmap is not None:
            self.set_thumbnail(self._thumbnail_pixmap)
        if enabled:
            self.setFixedSize(140, 135)
        else:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
        self._update_compact_contents()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_compact_contents()

    def _update_compact_contents(self) -> None:
        if self._grid_mode:
            self.file_size.hide()
            self.more.hide()
            self.badges.hide()
            return
        width = self.width()
        self.file_size.setVisible(width >= 220)
        self.more.setVisible(width >= 220)
        self.badges.setVisible(width >= 240)
        self.thumbnail.setFixedSize(48, 64) if width < 205 else self.thumbnail.setFixedSize(64, 64)
        if self._thumbnail_pixmap is not None and self._thumbnail_target != self.thumbnail.size():
            self.set_thumbnail(self._thumbnail_pixmap)

    def set_status(self, status: dict[str, bool | str]) -> None:
        state = tuple(bool(status.get(key, False)) for key in self.status_badges)
        error_message = str(status.get("error_message") or "Error de proceso en esta página")
        if getattr(self, "_status_state", None) == (state, error_message):
            return
        self._status_state = (state, error_message)
        for key, badge in self.status_badges.items():
            done = bool(status.get(key, False))
            error = key == "error" and bool(status.get("error", False))
            changed = badge.property("done") != done or badge.property("error") != error
            if key == "error" and badge.toolTip() != error_message:
                badge.setToolTip(error_message)
            if not changed:
                continue
            badge.setProperty("done", done)
            badge.setProperty("error", error)
            badge.style().unpolish(badge)
            badge.style().polish(badge)


class ImagesPanel(QFrame):
    page_selected = Signal(int)
    folder_dropped = Signal(str)
    thumbnail_range_requested = Signal(int, int)
    thumbnail_decoded = Signal(int, int, object)

    def __init__(self, pages: list[Page], image_manager: ImageManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.pages = pages
        self.image_manager = image_manager
        layout = QVBoxLayout(self); layout.setContentsMargins(12, 12, 12, 10); layout.setSpacing(7)
        self.title = QLabel(f"Páginas ({len(pages)})")
        self.title.setObjectName("PagesHeading")
        layout.addWidget(self.title)
        self.folder = ElidedLabel("Sin capítulo cargado")
        self.folder.setObjectName("Muted")
        self.folder.setMinimumWidth(0)
        self.folder.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.folder)
        self.legend = QLabel("D Detectada · O OCR · L Limpia · T Traducida · R Rotulada")
        self.legend.setObjectName("Caption")
        self.legend.setWordWrap(True)
        self.legend.setToolTip("Estados del flujo de cada página")
        self.legend.hide()
        layout.addWidget(self.legend)
        filters = QHBoxLayout(); filters.setSpacing(5)
        self.grid_button = ModernButton("", icon_name="grid")
        self.grid_button.setToolTip("Vista en cuadrícula")
        self.grid_button.setAccessibleName("Vista en cuadrícula")
        self.list_button = ModernButton("", icon_name="images")
        self.list_button.setToolTip("Vista en lista")
        self.list_button.setAccessibleName("Vista en lista")
        for button in (self.grid_button, self.list_button):
            button.setFixedSize(32, 32); filters.addWidget(button)
        self.list_button.setCheckable(True); self.list_button.setChecked(True)
        self.grid_button.setCheckable(True)
        self.grid_button.clicked.connect(lambda: self._set_view(True))
        self.list_button.clicked.connect(lambda: self._set_view(False))
        self.filter = QComboBox()
        self.filter.setProperty("kuro_i18n_choices", True)
        for label in [
            "Todas", "Sin detectar", "Pendiente OCR", "Pendiente traducción",
            "Pendiente limpieza", "Con error", "Sin rotular", "Rotuladas",
        ]:
            self.filter.addItem(label, label)
        self.filter.setAccessibleName("Filtrar páginas")
        self.filter.setToolTip(self.filter.currentText())
        self.filter.currentTextChanged.connect(self.filter.setToolTip)
        self.filter.setMinimumWidth(0)
        self.filter.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.filter, 1)
        self.sort_button = ModernButton("", icon_name="arrow-down")
        self.sort_button.setToolTip("Invertir orden de visualización")
        self.sort_button.setAccessibleName("Invertir orden de visualización")
        self.sort_button.setFixedSize(32, 32)
        self.sort_button.clicked.connect(self._toggle_sort)
        filters.addWidget(self.sort_button)
        layout.addLayout(filters)
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar página…")
        self.search.setAccessibleName("Buscar página")
        self.search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search, 1)
        self.next_pending = ModernButton("", icon_name="chevron-right")
        self.next_pending.setObjectName("PageNextPending")
        self.next_pending.setFixedSize(34, 34)
        self.next_pending.setAccessibleName("Ir a la siguiente página pendiente")
        self.next_pending.setToolTip("Ir a la siguiente página pendiente del filtro (Ctrl+Alt+N)")
        self.next_pending.clicked.connect(self.select_next_pending)
        search_row.addWidget(self.next_pending)
        layout.addLayout(search_row)
        self.list = ProjectImageList()
        self.list.setSpacing(7)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.list.currentRowChanged.connect(
            lambda row: self.page_selected.emit(self._display_to_source[row]
                                              if 0 <= row < len(self._display_to_source) else -1)
        )
        self.list.folder_dropped.connect(self.folder_dropped)
        self.list.verticalScrollBar().valueChanged.connect(lambda _: self._queue_thumbnails())
        self.list.viewport_resized.connect(self._queue_thumbnails)
        layout.addWidget(self.list, 1)
        self.footer = ElidedLabel("Capítulo sin imágenes cargadas")
        self.footer.setObjectName("Muted")
        self.footer.setMinimumWidth(0)
        self.footer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.footer)
        self._statuses: list[dict[str, bool | str]] = []
        self._display_to_source: list[int] = []
        self._loaded_thumbnails: set[int] = set()
        self._thumbnail_candidates: deque[int] = deque()
        self._thumbnail_queue_dirty = True
        self._thumbnail_generation = 0
        self._thumbnail_pool = QThreadPool(self)
        self._thumbnail_pool.setMaxThreadCount(2)
        self.thumbnail_decoded.connect(self._thumbnail_ready)
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.timeout.connect(self.request_visible_thumbnails)
        self._reverse = False
        self.refresh_pages(pages)

    def refresh_pages(self, pages: list[Page]) -> None:
        self.pages = pages
        self.title.setText(f"Páginas ({len(pages)})")
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
        self._loaded_thumbnails.clear()
        self._thumbnail_candidates.clear()
        self._thumbnail_queue_dirty = True
        self._thumbnail_generation += 1
        self._display_to_source = list(range(len(pages)))
        if self._reverse:
            self._display_to_source.reverse()
        self.list.source_to_row = {source: row for row, source in enumerate(self._display_to_source)}
        for index in self._display_to_source:
            page = pages[index]
            item = QListWidgetItem()
            widget = ImageListItem(page)
            widget.set_grid_mode(self.grid_button.isChecked())
            widget.more.clicked.connect(lambda _, row=index: self._show_row_menu(row))
            item.setSizeHint(QSize(140, 138) if self.grid_button.isChecked() else QSize(0, 88))
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        if pages:
            self.list.setCurrentRow(0)
            self.list.scrollToTop()
        self.list.blockSignals(False)
        self._apply_filter()
        self._queue_thumbnails()
    def request_visible_thumbnails(self) -> None:
        """Decode only visible thumbnails and yield between rows."""
        if not self.isVisible():
            return
        if self._thumbnail_queue_dirty:
            viewport = self.list.viewport().rect()
            self._thumbnail_candidates = deque(
                index for index in self._display_to_source
                if self.pages[index].path is not None
                and index not in self._loaded_thumbnails
                and not self.list.item(self.list.source_to_row[index]).isHidden()
                and self.list.visualItemRect(self.list.item(self.list.source_to_row[index])).intersects(viewport)
            )
            self._thumbnail_queue_dirty = False
        while self._thumbnail_candidates:
            index = self._thumbnail_candidates.popleft()
            if index in self._loaded_thumbnails:
                continue
            self._loaded_thumbnails.add(index)
            page = self.pages[index]
            if page.path:
                self._thumbnail_pool.start(ThumbnailTask(
                    self.image_manager, page.path, index,
                    self._thumbnail_generation, self.thumbnail_decoded,
                ))
            if self._thumbnail_candidates:
                self._thumbnail_timer.start(20)
            break

    def _thumbnail_ready(self, index: int, generation: int, image) -> None:
        if generation == self._thumbnail_generation and image is not None:
            self.set_thumbnail(index, image)

    def _queue_thumbnails(self):
        self._thumbnail_queue_dirty = True
        self._thumbnail_timer.start(20)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._queue_thumbnails()

    def set_thumbnail(self, index: int, image) -> None:
        if index in self.list.source_to_row:
            widget = self.list.itemWidget(self.list.item(self.list.source_to_row[index]))
            if isinstance(widget, ImageListItem):
                widget.set_thumbnail(image)

    def set_statuses(self, statuses: list[dict[str, bool | str]]) -> None:
        self._statuses = statuses
        completed = 0
        for index in range(len(self.pages)):
            widget = self.list.itemWidget(self.list.item(self.list.source_to_row[index]))
            status = statuses[index] if index < len(statuses) else {}
            if isinstance(widget, ImageListItem):
                widget.set_status(status)
            if status.get("typeset", False):
                completed += 1
        self.footer.setText(f"Capítulo actual: {len(self.pages)} imágenes · {completed} rotuladas")
        if self.pages:
            self.folder.setText(f"Carpeta: {self.pages[0].path.parent.name if self.pages[0].path else '—'} · {completed} rotuladas")
        self._apply_filter()

    def _apply_filter(self, *_args) -> None:
        query = self.search.text().strip().casefold()
        choice = self.filter.currentData()
        visible_indices = []
        for index, page in enumerate(self.pages):
            status = self._statuses[index] if index < len(self._statuses) else {}
            if choice == "Sin detectar":
                matches = not status.get("detected", False)
            elif choice == "Pendiente OCR":
                matches = status.get("detected", False) and not status.get("ocr", False)
            elif choice == "Pendiente traducción":
                matches = status.get("ocr", False) and not status.get("translated", False)
            elif choice == "Pendiente limpieza":
                matches = status.get("detected", False) and not status.get("cleaned", False)
            elif choice == "Con error":
                matches = status.get("error", False)
            elif choice == "Sin rotular":
                matches = not status.get("typeset", False)
            elif choice == "Rotuladas":
                matches = status.get("typeset", False)
            else:
                matches = True
            visible = (not query or query in page.name.casefold()) and bool(matches)
            item = self.list.item(self.list.source_to_row[index])
            if item.isHidden() == visible:
                item.setHidden(not visible)
            if visible and (choice != "Todas" or not status.get("typeset", False)):
                visible_indices.append(index)
        self._pending_indices = visible_indices
        self.next_pending.setEnabled(bool(visible_indices))
        self._queue_thumbnails()

    def select_next_pending(self) -> None:
        candidates = getattr(self, "_pending_indices", [])
        if not candidates:
            return
        row = self.list.currentRow()
        current = self._display_to_source[row] if 0 <= row < len(self._display_to_source) else -1
        target = next((index for index in candidates if index > current), candidates[0])
        self.list.setCurrentRow(target)
        self.list.scrollToItem(self.list.item(self.list.source_to_row[target]))

    def _set_view(self, grid: bool) -> None:
        self.grid_button.setChecked(grid)
        self.list_button.setChecked(not grid)
        self.list.setViewMode(QListWidget.IconMode if grid else QListWidget.ListMode)
        self.list.setGridSize(QSize(148, 142) if grid else QSize())
        for row in range(self.list.count()):
            item = self.list.item(row)
            widget = self.list.itemWidget(item)
            if isinstance(widget, ImageListItem):
                widget.set_grid_mode(grid)
                item.setSizeHint(QSize(140, 138) if grid else QSize(0, 88))
        self._queue_thumbnails()

    def _toggle_sort(self) -> None:
        self._reverse = not self._reverse
        self.sort_button.setIcon(icon("arrow-up" if self._reverse else "arrow-down"))
        selected = self._display_to_source[self.list.currentRow()] if self.list.currentRow() >= 0 else 0
        self.refresh_pages(self.pages)
        self.list.setCurrentRow(selected)
        self.set_statuses(self._statuses)

    def _show_row_menu(self, index: int) -> None:
        if index not in self.list.source_to_row:
            return
        row = self.list.source_to_row[index]
        widget = self.list.itemWidget(self.list.item(row))
        menu = QMenu(self)
        menu.addAction("Abrir página", lambda: self.list.setCurrentRow(index))
        if widget is not None:
            menu.exec(widget.mapToGlobal(widget.rect().bottomRight()))

    def _show_menu(self, position) -> None:
        item = self.list.itemAt(position)
        if item is not None:
            self._show_row_menu(self._display_to_source[self.list.row(item)])
