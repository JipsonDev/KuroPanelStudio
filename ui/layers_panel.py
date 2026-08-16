from __future__ import annotations

from PySide6.QtCore import QPoint, QItemSelectionModel, QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ui.widgets.controls import CollapsibleSection, ModernButton, SectionTitle
from ui.widgets.icons import icon


def _layer_name(index: int, region: dict) -> str:
    explicit = str(region.get("name", "")).strip()
    if explicit:
        return explicit
    text = str(region.get("translation") or region.get("applied_text") or region.get("text") or "").strip()
    if text:
        return " ".join(text.split())[:42]
    return f"Región de texto {index + 1}"


def _layer_row_signature(index: int, region: dict) -> tuple:
    """Only values painted by LayerItem; geometry x/y is intentionally absent."""
    return (
        index,
        int(region.get("number", index + 1)),
        _layer_name(index, region),
        int(region.get("width", 0)),
        int(region.get("height", 0)),
        round(float(region.get("confidence", 1.0)), 4),
        str(region.get("label", "")),
        max(0, min(100, int(region.get("opacity", 100)))),
        bool(region.get("visible", True)),
        bool(region.get("locked", False)),
        bool(region.get("text_overflow", False)),
    )


class ImageLayerItem(QFrame):
    activated = Signal(str)
    visibility_changed = Signal(str, bool)
    opacity_changed = Signal(str, int)
    lock_changed = Signal(str, bool)
    delete_requested = Signal(str)
    merge_requested = Signal(str)

    def __init__(self, key: str, name: str, description: str, icon_name: str, editable: bool = False) -> None:
        super().__init__()
        self.key = key
        self.available = True
        self.visible = True
        self.locked = False
        self.editable = bool(editable)
        self.setObjectName("ImageLayerRow")
        self.setProperty("active", False)
        if self.editable:
            self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5 if self.editable else 7, 6, 5 if self.editable else 6, 6)
        layout.setSpacing(4 if self.editable else 7)

        glyph = QLabel()
        glyph.setObjectName("ImageLayerGlyph")
        glyph.setPixmap(icon(icon_name, "#4AD9E7", 16).pixmap(16, 16))
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(22 if self.editable else 28, 28)
        layout.addWidget(glyph)

        details = QVBoxLayout()
        details.setSpacing(1)
        self.title = QLabel(name)
        self.title.setObjectName("LayerName")
        self.subtitle = QLabel(description)
        self.subtitle.setObjectName("Caption")
        details.addWidget(self.title)
        details.addWidget(self.subtitle)
        layout.addLayout(details, 1)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(100)
        self.opacity.setFixedWidth(40 if self.editable else 56)
        self.opacity.setToolTip(f"Opacidad de {name.lower()}")
        self.opacity.valueChanged.connect(self._opacity_changed)
        layout.addWidget(self.opacity)
        self.percent = QLabel("100%")
        self.percent.setObjectName("LayerOpacity")
        self.percent.setFixedWidth(28)
        self.percent.setVisible(not self.editable)
        layout.addWidget(self.percent)
        if self.editable:
            # Per-layer opacity lives in the shared inspector below the rows.
            # Repeating a slider in every narrow row made the panel cramped.
            self.opacity.setVisible(False)

        self.eye = ModernButton("", icon_name="eye")
        self.eye.setObjectName("LayerEye")
        self.eye.setFixedSize(23 if self.editable else 27, 27)
        self.eye.clicked.connect(self._toggle_visibility)
        layout.addWidget(self.eye)

        if self.editable:
            self.lock_button = ModernButton("", icon_name="unlock")
            self.lock_button.setObjectName("LayerEye")
            self.lock_button.setFixedSize(23, 27)
            self.lock_button.clicked.connect(self._toggle_lock)
            layout.addWidget(self.lock_button)
        self.patch_count = 0

    def set_state(self, available: bool, visible: bool, opacity: int, locked: bool = False) -> None:
        self.available = bool(available)
        self.visible = bool(visible)
        self.locked = bool(locked)
        value = max(0, min(100, int(opacity)))
        self.opacity.blockSignals(True)
        self.opacity.setValue(value)
        self.opacity.blockSignals(False)
        self.percent.setText(f"{value}%")
        self.opacity.setEnabled(self.available)
        self.eye.setEnabled(self.available)
        lock_button = getattr(self, "lock_button", None)
        if lock_button is not None:
            # A locked layer must remain unlockable even after its last patch
            # was removed. Otherwise the brush for that logical layer becomes
            # permanently inaccessible.
            lock_button.setEnabled(True)
        for button_name in ("merge_button", "delete_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(self.available)
        self.setProperty("unavailable", not self.available)
        self.setProperty("hidden", not self.visible)
        self._refresh()

    def set_summary(self, count: int, attention: int = 0) -> None:
        self.patch_count = max(0, int(count))
        detail = f"{int(count)} parche(s)"
        if attention:
            detail += f" · {int(attention)} por revisar"
        self.subtitle.setText(detail)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))
        self._refresh()

    def mousePressEvent(self, event) -> None:
        if self.editable and event.button() == Qt.LeftButton:
            self.activated.emit(self.key)
        super().mousePressEvent(event)

    def _toggle_lock(self) -> None:
        self.locked = not self.locked
        self._refresh()
        self.lock_changed.emit(self.key, self.locked)

    def _opacity_changed(self, value: int) -> None:
        self.percent.setText(f"{value}%")
        self.opacity_changed.emit(self.key, value)

    def _toggle_visibility(self) -> None:
        self.visible = not self.visible
        self.setProperty("hidden", not self.visible)
        self._refresh()
        self.visibility_changed.emit(self.key, self.visible)

    def _refresh(self) -> None:
        self.eye.setIcon(icon("eye" if self.visible else "eye-off", "#AAB8C5", 16))
        self.eye.setToolTip("Ocultar capa" if self.visible else "Mostrar capa")
        if hasattr(self, "lock_button"):
            self.lock_button.setIcon(icon("lock" if self.locked else "unlock", "#FFB84D" if self.locked else "#AAB8C5", 16))
            self.lock_button.setToolTip("Desbloquear capa" if self.locked else "Bloquear capa")
        self.style().unpolish(self)
        self.style().polish(self)


class LayerItem(QFrame):
    activated = Signal(int, object)
    visibility_changed = Signal(int, bool)
    lock_changed = Signal(int, bool)

    def __init__(self, index: int, region: dict) -> None:
        super().__init__()
        self.index = index
        self.visible = bool(region.get("visible", True))
        self.locked = bool(region.get("locked", False))
        self.setObjectName("LayerRow")
        self.setProperty("active", False)
        self.setProperty("hidden", not self.visible)
        self.setProperty("locked", self.locked)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 6, 6, 6)
        layout.setSpacing(7)
        number = int(region.get("number", index + 1))
        self.badge = QLabel(f"{number:02}")
        self.badge.setObjectName("LayerBadge")
        self.badge.setFixedSize(27, 27)
        self.badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.badge)

        details = QVBoxLayout()
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(2)
        self.name_label = QLabel(_layer_name(index, region))
        self.name_label.setObjectName("LayerName")
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.name_label.setToolTip(self.name_label.text())
        details.addWidget(self.name_label)
        confidence = float(region.get("confidence", 1.0))
        source = "Manual" if region.get("label") == "manual" else "YOLO"
        opacity = max(0, min(100, int(region.get("opacity", 100))))
        metadata = QLabel(
            f"{source} · {int(region.get('width', 0))}×{int(region.get('height', 0))} px"
            f" · {confidence:.0%} · {opacity}%"
        )
        metadata.setObjectName("Caption")
        metadata.setMinimumWidth(0)
        metadata.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        metadata.setToolTip(metadata.text())
        details.addWidget(metadata)
        self.metadata = metadata
        layout.addLayout(details, 1)

        self.eye = ModernButton("", icon_name="eye")
        self.eye.setObjectName("LayerEye")
        self.eye.setFixedSize(27, 27)
        self.eye.clicked.connect(self._toggle_visibility)
        layout.addWidget(self.eye)
        self.lock_button = ModernButton("", icon_name="lock" if self.locked else "unlock")
        self.lock_button.setObjectName("LayerEye")
        self.lock_button.setFixedSize(27, 27)
        self.lock_button.clicked.connect(self._toggle_lock)
        layout.addWidget(self.lock_button)
        self._refresh_buttons()
        self._overflow = None
        self.set_overflow(bool(region.get("text_overflow", False)))
        self._region_signature = _layer_row_signature(index, region)

    def update_region(self, index: int, region: dict) -> None:
        """Refresh one existing row instead of rebuilding the complete list."""
        signature = _layer_row_signature(index, region)
        if signature == self._region_signature:
            return
        self._region_signature = signature
        self.index = index
        number = int(region.get("number", index + 1))
        badge_text = f"{number:02}"
        if self.badge.text() != badge_text:
            self.badge.setText(badge_text)

        name = _layer_name(index, region)
        if self.name_label.text() != name:
            self.name_label.setText(name)
            self.name_label.setToolTip(name)

        confidence = float(region.get("confidence", 1.0))
        source = "Manual" if region.get("label") == "manual" else "YOLO"
        opacity = max(0, min(100, int(region.get("opacity", 100))))
        metadata = (
            f"{source} · {int(region.get('width', 0))}×{int(region.get('height', 0))} px"
            f" · {confidence:.0%} · {opacity}%"
        )
        overflow = bool(region.get("text_overflow", False))
        visible = bool(region.get("visible", True))
        locked = bool(region.get("locked", False))
        state_changed = visible != self.visible or locked != self.locked
        self.visible = visible
        self.locked = locked
        self.setProperty("hidden", not visible)
        self.setProperty("locked", locked)
        current_base = self.metadata.toolTip().split(" · ⚠")[0]
        if current_base != metadata:
            self.metadata.setToolTip(metadata)
            self.metadata.setText(metadata)
            self._overflow = None
        self.set_overflow(overflow)
        if state_changed:
            self._refresh_buttons()
            self._refresh_style()

    def set_overflow(self, overflow: bool) -> None:
        overflow = bool(overflow)
        if self._overflow is overflow:
            return
        self._overflow = overflow
        self.metadata.setProperty("overflow", bool(overflow))
        self.metadata.setStyleSheet("color:#FF9A62;" if overflow else "")
        base = self.metadata.toolTip().split(" · ⚠")[0]
        self.metadata.setText(base + (" · ⚠ Desborde" if overflow else ""))
        self.metadata.setToolTip(self.metadata.text())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.index, event.modifiers())
        super().mousePressEvent(event)

    def _refresh_buttons(self) -> None:
        self.eye.setIcon(icon("eye" if self.visible else "eye-off", "#AAB8C5", 16))
        self.eye.setToolTip("Ocultar capa" if self.visible else "Mostrar capa")
        self.lock_button.setIcon(icon("lock" if self.locked else "unlock", "#FFB84D" if self.locked else "#AAB8C5", 16))
        self.lock_button.setToolTip("Desbloquear geometría" if self.locked else "Bloquear geometría")

    def _toggle_visibility(self) -> None:
        self.visible = not self.visible
        self.setProperty("hidden", not self.visible)
        self._refresh_style()
        self._refresh_buttons()
        self.visibility_changed.emit(self.index, self.visible)

    def _toggle_lock(self) -> None:
        self.locked = not self.locked
        self.setProperty("locked", self.locked)
        self._refresh_style()
        self._refresh_buttons()
        self.lock_changed.emit(self.index, self.locked)

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def set_active(self, active: bool) -> None:
        if bool(self.property("active")) == bool(active):
            return
        self.setProperty("active", active)
        self._refresh_style()


class LayersPanel(QFrame):
    region_selected = Signal(int)
    visibility_changed = Signal(int, bool)
    lock_changed = Signal(int, bool)
    opacity_changed = Signal(int, int)
    rename_requested = Signal(int, str)
    duplicate_requested = Signal(int)
    delete_requested = Signal(int)
    delete_many_requested = Signal(object)
    move_requested = Signal(int, int)
    image_visibility_changed = Signal(str, bool)
    image_opacity_changed = Signal(str, int)
    source_layer_visibility_changed = Signal(str, bool)
    source_layer_opacity_changed = Signal(str, int)
    psd_sync_requested = Signal()
    psd_auto_sync_changed = Signal(bool)
    selection_changed = Signal(object)
    batch_visibility_requested = Signal(object, bool)
    batch_lock_requested = Signal(object, bool)
    retouch_visibility_changed = Signal(str, bool)
    retouch_opacity_changed = Signal(str, int)
    retouch_lock_changed = Signal(str, bool)
    retouch_delete_requested = Signal(str)
    retouch_merge_requested = Signal(str)

    def __init__(self, count: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._regions: list[dict] = []
        self._source_signature: tuple | None = None
        self._layer_row_height = 53
        self._realized_layer_rows: set[int] = set()
        self._layer_filter_query = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 10, 11, 9)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Capas"))
        header.addStretch()
        self.count_label = QLabel("0")
        self.count_label.setObjectName("LayerCount")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        layout.addWidget(SectionTitle("Imagen"))
        self.original_layer = ImageLayerItem("original", "Imagen original", "Base · resolución original", "images")
        self.clean_layer = ImageLayerItem("clean", "Imagen limpia", "Parches LaMa · no destructivo", "sparkles")
        for image_layer in (self.original_layer, self.clean_layer):
            image_layer.visibility_changed.connect(self.image_visibility_changed)
            image_layer.opacity_changed.connect(self.image_opacity_changed)
        layout.addWidget(self.original_layer)
        # Master result remains available for quick original/clean comparison;
        # the rows below expose the individual non-destructive contributors.
        layout.addWidget(self.clean_layer)
        self.retouch_section = CollapsibleSection("Retoque · 0 parches", expanded=False)
        self.retouch_section.header.setToolTip(
            "Mostrar u ocultar las capas no destructivas de limpieza y restauración"
        )
        layout.addWidget(self.retouch_section)
        self._active_retouch = "automatic"

        self.retouch_rows: dict[str, ImageLayerItem] = {
            "automatic": ImageLayerItem("automatic", "Limpieza automática", "Sin parches", "sparkles", True),
            "paint": ImageLayerItem("paint", "Pintura manual", "Sin parches", "brush", True),
            "restore": ImageLayerItem("restore", "Restauración original", "Sin parches", "refresh", True),
        }
        for row in self.retouch_rows.values():
            row.activated.connect(self._set_active_retouch)
            row.visibility_changed.connect(self.retouch_visibility_changed)
            row.opacity_changed.connect(self.retouch_opacity_changed)
            row.lock_changed.connect(self._retouch_lock_toggled)
            row.delete_requested.connect(self.retouch_delete_requested)
            row.merge_requested.connect(self.retouch_merge_requested)
            self.retouch_section.body_layout.addWidget(row)

        self.retouch_inspector = QFrame()
        self.retouch_inspector.setObjectName("RetouchInspector")
        retouch_controls = QHBoxLayout(self.retouch_inspector)
        retouch_controls.setContentsMargins(7, 5, 6, 5)
        retouch_controls.setSpacing(6)
        self.retouch_active_label = QLabel("Limpieza automática")
        self.retouch_active_label.setObjectName("Caption")
        self.retouch_active_label.setMinimumWidth(0)
        self.retouch_active_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        retouch_controls.addWidget(self.retouch_active_label, 1)
        self.retouch_opacity = QSlider(Qt.Horizontal)
        self.retouch_opacity.setRange(0, 100)
        self.retouch_opacity.setFixedWidth(62)
        self.retouch_opacity.valueChanged.connect(self._retouch_opacity_changed)
        retouch_controls.addWidget(self.retouch_opacity)
        self.retouch_opacity_value = QLabel("100%")
        self.retouch_opacity_value.setObjectName("LayerOpacity")
        self.retouch_opacity_value.setFixedWidth(30)
        retouch_controls.addWidget(self.retouch_opacity_value)
        self.retouch_merge_button = ModernButton("", icon_name="layers")
        self.retouch_merge_button.setObjectName("LayerEye")
        self.retouch_merge_button.setFixedSize(25, 27)
        self.retouch_merge_button.setToolTip("Consolidar los parches de la capa seleccionada")
        self.retouch_merge_button.clicked.connect(
            lambda: self.retouch_merge_requested.emit(self._active_retouch)
        )
        retouch_controls.addWidget(self.retouch_merge_button)
        self.retouch_delete_button = ModernButton("", icon_name="trash")
        self.retouch_delete_button.setObjectName("LayerEye")
        self.retouch_delete_button.setFixedSize(25, 27)
        self.retouch_delete_button.setToolTip("Eliminar los parches de la capa seleccionada")
        self.retouch_delete_button.clicked.connect(
            lambda: self.retouch_delete_requested.emit(self._active_retouch)
        )
        retouch_controls.addWidget(self.retouch_delete_button)
        self.retouch_section.body_layout.addWidget(self.retouch_inspector)

        self.psd_title = SectionTitle("Capas PSD")
        layout.addWidget(self.psd_title)
        self.psd_sync_bar = QFrame()
        self.psd_sync_bar.setObjectName("PsdSyncBar")
        psd_sync_layout = QHBoxLayout(self.psd_sync_bar)
        psd_sync_layout.setContentsMargins(0, 0, 0, 0)
        psd_sync_layout.setSpacing(6)
        self.psd_sync_status = QLabel("Esperando cambios")
        self.psd_sync_status.setObjectName("Muted")
        self.psd_sync_status.setToolTip("El PSD se recarga después de guardarlo en Photoshop")
        psd_sync_layout.addWidget(self.psd_sync_status, 1)
        self.psd_auto_sync = QCheckBox("Auto")
        self.psd_auto_sync.setToolTip("Recargar automáticamente después de guardar en Photoshop")
        self.psd_auto_sync.toggled.connect(self.psd_auto_sync_changed)
        psd_sync_layout.addWidget(self.psd_auto_sync)
        self.psd_sync_button = ModernButton("", icon_name="refresh")
        self.psd_sync_button.setFixedSize(29, 29)
        self.psd_sync_button.setToolTip("Sincronizar PSD ahora")
        self.psd_sync_button.clicked.connect(self.psd_sync_requested)
        psd_sync_layout.addWidget(self.psd_sync_button)
        layout.addWidget(self.psd_sync_bar)
        self.psd_layers = QScrollArea()
        self.psd_layers.setObjectName("PsdLayers")
        self.psd_layers.setWidgetResizable(True)
        self.psd_layers.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.psd_layers.setMaximumHeight(220)
        self.psd_layers_content = QFrame()
        self.psd_layers_layout = QVBoxLayout(self.psd_layers_content)
        self.psd_layers_layout.setContentsMargins(0, 0, 0, 0)
        self.psd_layers_layout.setSpacing(4)
        self.psd_layers.setWidget(self.psd_layers_content)
        layout.addWidget(self.psd_layers)

        layout.addWidget(SectionTitle("Texto"))

        self.search = QLineEdit()
        self.search.setObjectName("LayerSearch")
        self.search.setPlaceholderText("Buscar por nombre o texto…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_rows)
        layout.addWidget(self.search)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)
        self.rename_button = self._tool_button("edit", "Renombrar capa", self._rename_current)
        self.duplicate_button = self._tool_button("copy", "Duplicar capa", self._duplicate_current)
        self.visibility_button = self._tool_button("eye", "Mostrar u ocultar selección", self._toggle_selected_visibility)
        self.lock_button = self._tool_button("lock", "Bloquear o desbloquear selección", self._toggle_selected_lock)
        self.up_button = self._tool_button("arrow-up", "Subir en el orden", lambda: self._move_current(-1))
        self.down_button = self._tool_button("arrow-down", "Bajar en el orden", lambda: self._move_current(1))
        self.delete_button = self._tool_button("trash", "Eliminar capa", self._delete_current, danger=True)
        for button in (
            self.rename_button, self.duplicate_button, self.visibility_button, self.lock_button,
            self.up_button, self.down_button, self.delete_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.layers = QListWidget()
        self.layers.setObjectName("LayersList")
        self.layers.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.layers.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.layers.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.layers.setSpacing(4)
        self.layers.currentRowChanged.connect(self._set_active_row)
        self.layers.currentRowChanged.connect(self.region_selected)
        self.layers.itemSelectionChanged.connect(self._selection_changed)
        self.layers.itemDoubleClicked.connect(lambda _item: self._rename_current())
        self._layer_realize_timer = QTimer(self)
        self._layer_realize_timer.setSingleShot(True)
        self._layer_realize_timer.setInterval(0)
        self._layer_realize_timer.timeout.connect(self._realize_visible_layer_rows)
        self.layers.verticalScrollBar().valueChanged.connect(
            lambda _value: self._layer_realize_timer.start()
        )
        layout.addWidget(self.layers, 1)

        self.empty_label = QLabel("Las cajas de texto aparecerán aquí")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        opacity_row = QHBoxLayout()
        opacity_label = QLabel("OPACIDAD")
        opacity_label.setObjectName("Caption")
        opacity_row.addWidget(opacity_label)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(100)
        self.opacity.valueChanged.connect(self._opacity_changed)
        opacity_row.addWidget(self.opacity, 1)
        self.opacity_value = QLabel("100%")
        self.opacity_value.setObjectName("LayerOpacity")
        self.opacity_value.setFixedWidth(34)
        opacity_row.addWidget(self.opacity_value)
        layout.addLayout(opacity_row)
        if count:
            self.set_regions([{"confidence": 1.0} for _ in range(count)])
        else:
            self._update_actions()

        self.set_image_layers(False, False, {})
        self.set_source_layers([], {})

    def set_image_layers(
        self, original_available: bool, clean_available: bool, state: dict | None = None,
        retouch_counts: dict[str, int] | None = None,
        retouch_states: dict[str, dict] | None = None,
        history: list[dict] | None = None,
    ) -> None:
        state = state or {}
        original = state.get("original", {})
        clean = state.get("clean", {})
        self.original_layer.set_state(
            original_available, original.get("visible", True), original.get("opacity", 100),
        )
        self.clean_layer.set_state(
            clean_available, clean.get("visible", True), clean.get("opacity", 100),
        )
        counts = retouch_counts or {}
        layer_states = retouch_states or {}
        attention_by_layer: dict[str, int] = {key: 0 for key in self.retouch_rows}
        for entry in history or []:
            if str(entry.get("qc_status", "")) == "attention":
                layer = str(entry.get("layer", ""))
                attention_by_layer[layer] = attention_by_layer.get(layer, 0) + 1
        for key, row in self.retouch_rows.items():
            current = layer_states.get(key, {})
            count = int(counts.get(key, 0))
            row.set_state(
                count > 0, current.get("visible", True), current.get("opacity", 100),
                current.get("locked", False),
            )
            row.set_summary(count, attention_by_layer.get(key, 0))
        total_patches = sum(max(0, int(counts.get(key, 0))) for key in self.retouch_rows)
        attention = sum(attention_by_layer.values())
        label = f"Retoque · {total_patches} parche{'s' if total_patches != 1 else ''}"
        if attention:
            label += f" · {attention} por revisar"
        self.retouch_section.header.setText(label)
        self._refresh_retouch_inspector()

    def _set_active_retouch(self, key: str) -> None:
        if key not in self.retouch_rows:
            return
        self._active_retouch = key
        self._refresh_retouch_inspector()

    def _refresh_retouch_inspector(self) -> None:
        names = {
            "automatic": "Limpieza automática",
            "paint": "Pintura manual",
            "restore": "Restauración original",
        }
        for key, row in self.retouch_rows.items():
            row.set_active(key == self._active_retouch)
        row = self.retouch_rows.get(self._active_retouch)
        if row is None:
            return
        self.retouch_active_label.setText(names.get(self._active_retouch, "Retoque"))
        self.retouch_opacity.blockSignals(True)
        self.retouch_opacity.setValue(row.opacity.value())
        self.retouch_opacity.blockSignals(False)
        self.retouch_opacity_value.setText(f"{row.opacity.value()}%")
        self.retouch_opacity.setEnabled(row.available)
        self.retouch_merge_button.setEnabled(row.available and row.patch_count > 1 and not row.locked)
        self.retouch_delete_button.setEnabled(row.available and not row.locked)

    def _retouch_opacity_changed(self, value: int) -> None:
        row = self.retouch_rows.get(self._active_retouch)
        if row is None:
            return
        value = max(0, min(100, int(value)))
        self.retouch_opacity_value.setText(f"{value}%")
        row.opacity.blockSignals(True)
        row.opacity.setValue(value)
        row.opacity.blockSignals(False)
        self.retouch_opacity_changed.emit(self._active_retouch, value)

    def _retouch_lock_toggled(self, key: str, locked: bool) -> None:
        self._refresh_retouch_inspector()
        self.retouch_lock_changed.emit(key, locked)

    def set_source_layers(self, layers: list[dict], states: dict[str, dict] | None = None) -> None:
        states = states or {}
        signature = tuple(
            (
                str(layer.get("id", "")), str(layer.get("name", "")), int(layer.get("depth", 0)),
                bool(states.get(str(layer.get("id", "")), {}).get("visible", layer.get("visible", True))),
                int(states.get(str(layer.get("id", "")), {}).get("opacity", layer.get("opacity", 100))),
            )
            for layer in layers
        )
        if signature == self._source_signature:
            return
        self._source_signature = signature
        while self.psd_layers_layout.count():
            item = self.psd_layers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.psd_title.setVisible(bool(layers))
        self.psd_title.setText(f"Capas PSD ({len(layers)})" if layers else "Capas PSD")
        self.psd_layers.setVisible(bool(layers))
        self.psd_sync_bar.setVisible(bool(layers))
        for layer in layers:
            identity = str(layer.get("id", ""))
            state = states.get(identity, {})
            depth = max(0, int(layer.get("depth", 0)))
            kind = "Grupo" if layer.get("group") else str(layer.get("kind", "Capa")).capitalize()
            name = ("    " * depth) + str(layer.get("name", "Capa"))
            row = ImageLayerItem(
                f"psd:{identity}", name,
                f"{kind} · {layer.get('blend_mode', 'normal')}",
                "layers" if layer.get("group") else "image",
            )
            row.visibility_changed.connect(
                lambda _key, visible, layer_id=identity: self.source_layer_visibility_changed.emit(layer_id, visible)
            )
            row.opacity_changed.connect(
                lambda _key, opacity, layer_id=identity: self.source_layer_opacity_changed.emit(layer_id, opacity)
            )
            row.set_state(
                True,
                bool(state.get("visible", layer.get("visible", True))),
                int(state.get("opacity", layer.get("opacity", 100))),
            )
            self.psd_layers_layout.addWidget(row)

    def set_psd_sync_state(
        self, *, available: bool, auto: bool, text: str = "Esperando cambios",
        syncing: bool = False,
    ) -> None:
        self.psd_sync_bar.setVisible(bool(available))
        self.psd_sync_status.setText(text)
        self.psd_auto_sync.blockSignals(True)
        self.psd_auto_sync.setChecked(bool(auto))
        self.psd_auto_sync.blockSignals(False)
        self.psd_sync_button.setEnabled(bool(available) and not syncing)
        self.psd_auto_sync.setEnabled(bool(available) and not syncing)

    def set_overflow_states(self, states: list[bool]) -> None:
        for index, overflow in enumerate(states):
            if index >= self.layers.count():
                break
            widget = self.layers.itemWidget(self.layers.item(index))
            if isinstance(widget, LayerItem):
                widget.set_overflow(bool(overflow))

    @staticmethod
    def _tool_button(icon_name: str, tooltip: str, callback, danger: bool = False) -> ModernButton:
        button = ModernButton("", icon_name=icon_name)
        button.setObjectName("LayerDanger" if danger else "LayerTool")
        button.setFixedSize(29, 29)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    def set_regions(
        self,
        regions: list[dict],
        selected_region_id: str | None = None,
        keep_empty_selection: bool = False,
    ) -> None:
        previous_ids = {
            str(self._regions[index].get("id", ""))
            for index in self.selected_indices() if 0 <= index < len(self._regions)
        }
        previous_id = str(selected_region_id or "")
        if previous_id:
            previous_ids = {previous_id}
        current = self.current_index()
        if not previous_id and 0 <= current < len(self._regions):
            previous_id = str(self._regions[current].get("id", ""))
        region_ids = [str(region.get("id", index)) for index, region in enumerate(regions)]
        existing_ids = [
            str(self.layers.item(index).data(Qt.UserRole))
            for index in range(self.layers.count())
        ]
        structure_unchanged = region_ids == existing_ids
        self._regions = regions
        self.layers.blockSignals(True)
        self.layers.setUpdatesEnabled(False)
        if structure_unchanged:
            self.layers.clearSelection()
        self.count_label.setText(str(len(regions)))
        self.empty_label.setVisible(not regions)
        selected_row = -1
        if not structure_unchanged:
            self.layers.clear()
            self._realized_layer_rows.clear()
        for index, region in enumerate(regions):
            if structure_unchanged:
                item = self.layers.item(index)
                widget = self.layers.itemWidget(item)
                item.setData(Qt.UserRole + 1, _layer_name(index, region))
                if isinstance(widget, LayerItem):
                    widget.update_region(index, region)
                    item.setSizeHint(widget.sizeHint())
            else:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, region_ids[index])
                item.setData(Qt.UserRole + 1, _layer_name(index, region))
                # QListWidgetItems are cheap. The actual rich LayerItem is
                # created only when its row enters the viewport.
                item.setSizeHint(QSize(0, self._layer_row_height))
                self.layers.addItem(item)
            if previous_id and str(region.get("id", "")) == previous_id:
                selected_row = index
            if str(region.get("id", "")) in previous_ids:
                item.setSelected(True)
        if regions and not keep_empty_selection:
            if selected_row < 0:
                selected_row = min(max(0, current), len(regions) - 1)
            self.layers.setCurrentItem(self.layers.item(selected_row), QItemSelectionModel.NoUpdate)
            if not self.layers.selectedItems():
                self.layers.item(selected_row).setSelected(True)
        elif keep_empty_selection:
            self.layers.clearSelection()
            self.layers.setCurrentRow(-1)
        self.layers.setUpdatesEnabled(True)
        self.layers.blockSignals(False)
        self._filter_rows(self.search.text())
        self._realize_visible_layer_rows()
        self._set_active_row(self.layers.currentRow())
        self.region_selected.emit(self.layers.currentRow())

    def _create_layer_row(self, index: int) -> LayerItem | None:
        if not 0 <= index < min(self.layers.count(), len(self._regions)):
            return None
        item = self.layers.item(index)
        current = self.layers.itemWidget(item)
        if isinstance(current, LayerItem):
            self._realized_layer_rows.add(index)
            return current
        widget = LayerItem(index, self._regions[index])
        widget.activated.connect(self._activate_row)
        widget.visibility_changed.connect(self.visibility_changed)
        widget.lock_changed.connect(self.lock_changed)
        widget.set_active(item.isSelected())
        item.setSizeHint(widget.sizeHint())
        self.layers.setItemWidget(item, widget)
        self._realized_layer_rows.add(index)
        return widget

    def _visible_layer_indices(self) -> set[int]:
        count = self.layers.count()
        if not count:
            return set()
        viewport = self.layers.viewport().rect()
        first = self.layers.indexAt(QPoint(2, 2)).row()
        last = self.layers.indexAt(QPoint(2, max(2, viewport.height() - 2))).row()
        current = self.current_index()
        if first < 0:
            first = max(0, current)
        if last < 0:
            last = min(count - 1, first + 12)
        keep = set(range(max(0, first - 6), min(count, last + 7)))
        keep.update(self.selected_indices())
        if 0 <= current < count:
            keep.add(current)
        return keep

    def _realize_visible_layer_rows(self) -> None:
        keep = self._visible_layer_indices()
        for index in keep:
            self._create_layer_row(index)
        # Release rich rows far outside the viewport. Item data and selection
        # remain in the QListWidget, so recreating a row is lossless.
        for index in self._realized_layer_rows - keep:
            item = self.layers.item(index)
            widget = self.layers.itemWidget(item)
            if isinstance(widget, LayerItem):
                self.layers.removeItemWidget(item)
                widget.deleteLater()
        self._realized_layer_rows.intersection_update(keep)

    def _activate_row(self, index: int, modifiers) -> None:
        if not 0 <= index < self.layers.count():
            return
        item = self.layers.item(index)
        if modifiers & Qt.ShiftModifier and self.current_index() >= 0:
            anchor = self.current_index()
            self.layers.clearSelection()
            for row in range(min(anchor, index), max(anchor, index) + 1):
                self.layers.item(row).setSelected(True)
            self.layers.setCurrentItem(item, QItemSelectionModel.NoUpdate)
        elif modifiers & Qt.ControlModifier:
            selected = item.isSelected()
            self.layers.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            item.setSelected(not selected)
            if not self.layers.selectedItems():
                item.setSelected(True)
        else:
            self.layers.setCurrentRow(index)

    def _selection_changed(self) -> None:
        selected = {
            index for index in self.selected_indices()
            if 0 <= index < len(self._regions)
        }
        for index in self._realized_layer_rows:
            widget = self.layers.itemWidget(self.layers.item(index))
            if isinstance(widget, LayerItem):
                widget.set_active(index in selected)
        self.count_label.setText(
            f"{len(selected)}/{len(self._regions)}" if len(selected) > 1 else str(len(self._regions))
        )
        self._update_actions()
        self.selection_changed.emit(sorted(selected))

    def _filter_rows(self, query: str) -> None:
        query = query.strip().casefold()
        if not query and not self._layer_filter_query:
            return
        self._layer_filter_query = query
        for index in range(self.layers.count()):
            item = self.layers.item(index)
            region = self._regions[index] if index < len(self._regions) else {}
            haystack = " ".join((
                str(index + 1), str(item.data(Qt.UserRole + 1) or ""),
                str(region.get("text", "")), str(region.get("translation", "")),
                str(region.get("applied_text", "")),
            )).casefold()
            item.setHidden(bool(query and query not in haystack))
        if hasattr(self, "_layer_realize_timer"):
            self._layer_realize_timer.start()

    def _set_active_row(self, row: int) -> None:
        if 0 <= row < self.layers.count():
            self._create_layer_row(row)
        enabled = 0 <= row < len(self._regions)
        self.opacity.blockSignals(True)
        value = max(0, min(100, int(self._regions[row].get("opacity", 100)))) if enabled else 100
        self.opacity.setValue(value)
        self.opacity_value.setText(f"{value}%")
        self.opacity.blockSignals(False)
        self._selection_changed()

    def _update_actions(self) -> None:
        row = self.current_index()
        enabled = 0 <= row < len(self._regions)
        selected = [
            index for index in self.selected_indices()
            if 0 <= index < len(self._regions)
        ]
        locked = bool(self._regions[row].get("locked", False)) if enabled else False
        self.rename_button.setEnabled(enabled and len(selected) <= 1)
        self.duplicate_button.setEnabled(enabled and len(selected) <= 1)
        self.visibility_button.setEnabled(bool(selected))
        self.lock_button.setEnabled(bool(selected))
        self.delete_button.setEnabled(bool(selected) and any(not self._regions[index].get("locked", False) for index in selected))
        self.up_button.setEnabled(enabled and len(selected) <= 1 and row > 0)
        self.down_button.setEnabled(enabled and len(selected) <= 1 and row < len(self._regions) - 1)
        self.opacity.setEnabled(enabled)

    def _rename_current(self) -> None:
        row = self.current_index()
        if not 0 <= row < len(self._regions):
            return
        current = _layer_name(row, self._regions[row])
        name, accepted = QInputDialog.getText(self, "Renombrar capa", "Nombre de la capa:", text=current)
        if accepted and name.strip():
            self.rename_requested.emit(row, name.strip())

    def _duplicate_current(self) -> None:
        if self.current_index() >= 0:
            self.duplicate_requested.emit(self.current_index())

    def _delete_current(self) -> None:
        indices = [
            index for index in self.selected_indices()
            if not bool(self._regions[index].get("locked", False))
        ]
        if len(indices) > 1:
            self.delete_many_requested.emit(indices)
        elif indices:
            self.delete_requested.emit(indices[0])

    def _move_current(self, offset: int) -> None:
        source = self.current_index()
        target = source + offset
        if 0 <= source < len(self._regions) and 0 <= target < len(self._regions):
            self.move_requested.emit(source, target)

    def _toggle_selected_visibility(self) -> None:
        indices = self.selected_indices()
        if not indices:
            return
        visible = any(not bool(self._regions[index].get("visible", True)) for index in indices)
        self.batch_visibility_requested.emit(indices, visible)

    def _toggle_selected_lock(self) -> None:
        indices = self.selected_indices()
        if not indices:
            return
        locked = not all(bool(self._regions[index].get("locked", False)) for index in indices)
        self.batch_lock_requested.emit(indices, locked)

    def _opacity_changed(self, value: int) -> None:
        self.opacity_value.setText(f"{value}%")
        if self.current_index() >= 0:
            self.opacity_changed.emit(self.current_index(), value)

    def current_index(self) -> int:
        return self.layers.currentRow()

    def selected_indices(self) -> list[int]:
        return sorted(index.row() for index in self.layers.selectedIndexes())

    def set_selected_indices(self, indices: list[int], current: int | None = None) -> None:
        valid = sorted({index for index in indices if 0 <= index < self.layers.count()})
        self.layers.blockSignals(True)
        self.layers.clearSelection()
        for index in valid:
            self.layers.item(index).setSelected(True)
        target = current if current in valid else (valid[-1] if valid else -1)
        if target >= 0:
            self.layers.setCurrentItem(self.layers.item(target), QItemSelectionModel.NoUpdate)
        else:
            # QListWidget keeps a separate current index even after
            # clearSelection(). If it survives a deletion, the next refresh
            # silently promotes row 0 and the editor appears to jump there.
            self.layers.setCurrentRow(-1)
        self.layers.blockSignals(False)
        for index in valid:
            self._create_layer_row(index)
        self._set_active_row(target)
        if target >= 0:
            self.region_selected.emit(target)

    def set_current_index(self, index: int) -> None:
        if 0 <= index < self.layers.count():
            self.layers.setCurrentRow(index)
