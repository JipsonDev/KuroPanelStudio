from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from core.watermark_manager import normalized_watermark, prepare_watermark
from ui.widgets.controls import ModernButton, SectionTitle, CollapsibleSection


ANCHORS = (
    ("↖", "top-left"), ("↑", "top-center"), ("↗", "top-right"),
    ("←", "middle-left"), ("●", "center"), ("→", "middle-right"),
    ("↙", "bottom-left"), ("↓", "bottom-center"), ("↘", "bottom-right"),
)


class WatermarkDialog(QDialog):
    settings_changed = Signal(dict)
    apply_current_requested = Signal()
    apply_chapter_requested = Signal()
    remove_current_requested = Signal()
    remove_chapter_requested = Signal()
    dialog_closed = Signal()
    redistribute_requested = Signal()
    redistribute_chapter_requested = Signal()

    def __init__(self, settings: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Marca de agua")
        self.setObjectName("WatermarkDialog")
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setAcceptDrops(True)
        self.resize(460, 780)
        self._loading = False
        self._settings = normalized_watermark(settings)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QFrame(); content.setObjectName("AIPanel"); scroll.setWidget(content); outer.addWidget(scroll, 1)
        root = QVBoxLayout(content); root.setContentsMargins(16, 16, 16, 16); root.setSpacing(12)
        root.addWidget(SectionTitle("Archivo PNG"))
        self.drop_zone = QLabel("Arrastra un PNG aquí\no pulsa para seleccionarlo")
        self.drop_zone.setObjectName("WatermarkDropZone")
        self.drop_zone.setAlignment(Qt.AlignCenter); self.drop_zone.setMinimumHeight(126)
        self.drop_zone.setStyleSheet("border:1px dashed #626262; border-radius:9px; background:#202020; color:#B8B8B8;")
        root.addWidget(self.drop_zone)
        choose = ModernButton("Seleccionar PNG…", "Primary", icon_name="images")
        choose.clicked.connect(self._choose_png); root.addWidget(choose)
        self.source_label = QLabel("Sin archivo seleccionado")
        self.source_label.setObjectName("Muted"); self.source_label.setWordWrap(True); root.addWidget(self.source_label)

        root.addWidget(SectionTitle("Tamaño"))
        self.size_mode = QComboBox()
        self.size_mode.setProperty("kuro_i18n_choices", True)
        self.size_mode.addItem("Porcentaje del ancho de página", "percent")
        self.size_mode.addItem("Ancho exacto en píxeles", "pixels")
        root.addWidget(self.size_mode)
        self.scale_percent = self._spin(1, 100, " %")
        self.width_px = self._spin(8, 20000, " px")
        self.scale_label = QLabel("PORCENTAJE"); self.scale_label.setBuddy(self.scale_percent)
        self.width_label = QLabel("ANCHO EXACTO"); self.width_label.setBuddy(self.width_px)
        self.scale_percent.setAccessibleName("Porcentaje del ancho de página")
        self.width_px.setAccessibleName("Ancho exacto en píxeles")
        root.addWidget(self.scale_label); root.addWidget(self.scale_percent)
        root.addWidget(self.width_label); root.addWidget(self.width_px)
        self.trim_transparent = QCheckBox("Medir solo el logo visible")
        self.trim_transparent.setToolTip("Ignora el borde transparente del PNG al calcular el tamaño y la alineación.")
        root.addWidget(self.trim_transparent)
        self.size_summary = QLabel("Abre una página para ver el tamaño final.")
        self.size_summary.setObjectName("Muted"); self.size_summary.setWordWrap(True)
        root.addWidget(self.size_summary)
        self.size_mode.currentIndexChanged.connect(self._update_size_visibility)

        root.addWidget(SectionTitle("Posición y alineación"))
        anchor_grid = QGridLayout(); anchor_grid.setSpacing(6)
        self.anchor_group = QButtonGroup(self); self.anchor_group.setExclusive(True)
        self.anchor_buttons: dict[str, QPushButton] = {}
        for index, (label, name) in enumerate(ANCHORS):
            button = QPushButton(label); button.setCheckable(True); button.setMinimumHeight(34)
            label_es = ("Arriba izquierda", "Arriba centro", "Arriba derecha",
                        "Centro izquierda", "Centro", "Centro derecha",
                        "Abajo izquierda", "Abajo centro", "Abajo derecha")[index]
            button.setToolTip(label_es); button.setAccessibleName(label_es)
            button.setFocusPolicy(Qt.StrongFocus)
            self.anchor_group.addButton(button, index); self.anchor_buttons[name] = button
            anchor_grid.addWidget(button, index // 3, index % 3)
        self.anchor_group.idClicked.connect(lambda _index: self._emit())
        root.addLayout(anchor_grid)

        root.addWidget(SectionTitle("Distribución"))
        self.repeat = QCheckBox("Repartir por el capítulo completo")
        self.auto_count = QCheckBox("Cantidad automática según la altura del capítulo")
        self.interval_px = self._spin(0, 100000, " px")
        self.interval_px.setSpecialValueText("Automática")
        self.interval_px.setToolTip("Distancia aproximada entre centros de marcas. Cero calcula una distancia según el ancho del capítulo.")
        self.repeat_x = self._spin(0, 10000, " px")
        self.repeat_y = self._spin(0, 10000, " px")
        self.repeat_count = self._spin(1, 100, " marca(s)")
        root.addWidget(self.repeat)
        self.distribution = QComboBox()
        self.distribution.setProperty("kuro_i18n_choices", True)
        self.distribution.addItem("Columna alineada", "column")
        self.distribution.addItem("Alternar izquierda y derecha", "alternating")
        self.distribution.addItem("Adaptar al espacio libre", "adaptive")
        self.distribution.setToolTip("Adaptar busca otro lado en la misma zona si una caja de texto bloquea la posición elegida.")
        root.addWidget(self.distribution)
        self.minimum_gap = self._spin(0, 10000, " px")
        root.addWidget(QLabel("SEPARACIÓN MÍNIMA ENTRE MARCAS")); root.addWidget(self.minimum_gap)
        root.addWidget(self.auto_count)
        self.interval_label = QLabel("DISTANCIA ENTRE CENTROS"); self.interval_label.setBuddy(self.interval_px)
        self.count_label = QLabel("CANTIDAD TOTAL EN EL CAPÍTULO"); self.count_label.setBuddy(self.repeat_count)
        self.interval_px.setAccessibleName("DISTANCIA ENTRE CENTROS")
        self.repeat_count.setAccessibleName("CANTIDAD TOTAL EN EL CAPÍTULO")
        root.addWidget(self.interval_label); root.addWidget(self.interval_px)
        root.addWidget(self.count_label); root.addWidget(self.repeat_count)
        repeat_hint = QLabel("Las páginas forman una tira continua: la separación se mantiene entre archivos. La cantidad es para todo el capítulo, no para cada página.")
        repeat_hint.setObjectName("Muted"); repeat_hint.setWordWrap(True); root.addWidget(repeat_hint)
        self.plan_summary = QLabel("Sin reparto calculado")
        self.plan_summary.setObjectName("FontAssignmentStatus")
        self.plan_summary.setWordWrap(True); root.addWidget(self.plan_summary)
        self.avoid_text = QCheckBox("Evitar cajas de texto detectadas")
        root.addWidget(self.avoid_text)
        self.seam_safe = QCheckBox("Dejar espacio en las uniones de páginas")
        self.seam_safe.setVisible(False)
        self.preview_visible = QCheckBox("Mostrar previsualización durante la edición")
        self.preview_visible.setChecked(True)
        self.preview_visible.setVisible(False)
        canvas_hint = QLabel("En el lienzo: arrastra una marca para moverla · Ctrl + clic o Supr para borrarla.")
        canvas_hint.setObjectName("Muted"); canvas_hint.setWordWrap(True); root.addWidget(canvas_hint)

        self.redistribute = ModernButton("Redistribuir esta página", icon_name="refresh")
        self.redistribute.setToolTip("Reemplaza las posiciones manuales de esta página por el reparto automático.")
        self.redistribute.clicked.connect(self.redistribute_requested.emit)
        root.addWidget(self.redistribute)
        self.redistribute_chapter = ModernButton("Redistribuir todo el capítulo", icon_name="refresh")
        self.redistribute_chapter.setToolTip("Reemplaza las posiciones manuales por un reparto continuo del capítulo.")
        self.redistribute_chapter.clicked.connect(self.redistribute_chapter_requested.emit)
        root.addWidget(self.redistribute_chapter)
        self.advanced = CollapsibleSection("Márgenes y apariencia")
        root.addWidget(self.advanced)
        main_root = root
        root = self.advanced.body_layout
        self.margin_x = self._spin(0, 10000, " px")
        self.margin_y = self._spin(0, 10000, " px")
        self.offset_x = self._spin(-20000, 20000, " px")
        self.offset_y = self._spin(-20000, 20000, " px")
        for label, field in (
            ("MARGEN HORIZONTAL", self.margin_x), ("MARGEN VERTICAL", self.margin_y),
            ("DESPLAZAMIENTO X", self.offset_x), ("DESPLAZAMIENTO Y", self.offset_y),
        ):
            root.addWidget(QLabel(label)); root.addWidget(field)
        self.keep_inside = QCheckBox("Mantener dentro de la página")
        root.addWidget(self.keep_inside)

        root.addWidget(SectionTitle("Apariencia"))
        self.opacity = self._spin(0, 100, " %")
        self.rotation = self._spin(-180, 180, "°")
        self.blend = QComboBox()
        self.blend.setProperty("kuro_i18n_choices", True)
        for label, value in (("Normal", "normal"), ("Multiplicar", "multiply"), ("Pantalla", "screen"), ("Superponer", "overlay")):
            self.blend.addItem(label, value)
        root.addWidget(QLabel("OPACIDAD")); root.addWidget(self.opacity)
        root.addWidget(QLabel("ROTACIÓN")); root.addWidget(self.rotation)
        root.addWidget(QLabel("MODO DE FUSIÓN")); root.addWidget(self.blend)

        root = main_root

        root.addStretch()
        footer = QFrame()
        outer.addWidget(footer)
        root = QVBoxLayout(footer)
        root.setContentsMargins(14, 10, 14, 14)
        root.addWidget(SectionTitle("Aplicar"))
        apply_row = QHBoxLayout()
        current = ModernButton("Página actual", "Primary")
        chapter = ModernButton("Todo el capítulo", "Primary")
        current.clicked.connect(self.apply_current_requested.emit)
        chapter.clicked.connect(self.apply_chapter_requested.emit)
        apply_row.addWidget(current); apply_row.addWidget(chapter); root.addLayout(apply_row)
        remove_row = QHBoxLayout()
        remove_current = ModernButton("Quitar de página")
        remove_chapter = ModernButton("Quitar de capítulo")
        remove_current.clicked.connect(self.remove_current_requested.emit)
        remove_chapter.clicked.connect(self.remove_chapter_requested.emit)
        remove_row.addWidget(remove_current); remove_row.addWidget(remove_chapter); root.addLayout(remove_row)
        self.status = QLabel("Carga un PNG para comenzar.")
        self.status.setObjectName("FontAssignmentStatus"); self.status.setWordWrap(True); root.addWidget(self.status)

        self._value_widgets = [
            self.size_mode, self.scale_percent, self.width_px, self.margin_x, self.margin_y,
            self.offset_x, self.offset_y, self.keep_inside, self.opacity, self.rotation,
            self.blend, self.repeat, self.auto_count, self.repeat_count, self.avoid_text, self.seam_safe,
            self.distribution, self.minimum_gap,
            self.interval_px, self.trim_transparent,
        ]
        self.repeat.toggled.connect(self._update_size_visibility)
        self.auto_count.toggled.connect(self._update_size_visibility)
        for widget in self._value_widgets:
            signal = getattr(widget, "currentIndexChanged", None) or getattr(widget, "valueChanged", None) or getattr(widget, "toggled", None)
            signal.connect(self._emit)
        self.set_settings(self._settings)

    @staticmethod
    def _spin(low: int, high: int, suffix: str) -> QSpinBox:
        field = QSpinBox(); field.setRange(low, high); field.setSuffix(suffix); field.setMinimumHeight(36)
        field.setKeyboardTracking(False)
        return field

    def set_settings(self, settings: dict) -> None:
        self._loading = True; self._settings = normalized_watermark(settings)
        config = self._settings
        self.size_mode.setCurrentIndex(max(0, self.size_mode.findData(config["size_mode"])))
        self.scale_percent.setValue(config["scale_percent"]); self.width_px.setValue(config["width_px"])
        self.margin_x.setValue(config["margin_x"]); self.margin_y.setValue(config["margin_y"])
        self.offset_x.setValue(config["offset_x"]); self.offset_y.setValue(config["offset_y"])
        self.keep_inside.setChecked(config["keep_inside"]); self.opacity.setValue(config["opacity"])
        self.rotation.setValue(config["rotation"]); self.blend.setCurrentIndex(max(0, self.blend.findData(config["blend_mode"])))
        self.repeat.setChecked(config["repeat"]); self.repeat_x.setValue(config["repeat_spacing_x"])
        self.repeat_y.setValue(config["repeat_spacing_y"]); self.repeat_count.setValue(config["repeat_count"])
        self.auto_count.setChecked(config["auto_count"]); self.avoid_text.setChecked(config["avoid_text"])
        self.seam_safe.setChecked(config["seam_safe"])
        self.distribution.setCurrentIndex(self.distribution.findData(config["distribution"]))
        self.minimum_gap.setValue(config["minimum_gap"])
        self.interval_px.setValue(config["interval_px"])
        self.trim_transparent.setChecked(config["trim_transparent"])
        self.preview_visible.setChecked(True)
        self.anchor_buttons[config["anchor"]].setChecked(True)
        self._update_source_preview(); self._update_size_visibility(); self._loading = False

    def values(self) -> dict:
        anchor = next((name for name, button in self.anchor_buttons.items() if button.isChecked()), "bottom-right")
        return normalized_watermark({
            **self._settings, "size_mode": self.size_mode.currentData(),
            "scale_percent": self.scale_percent.value(), "width_px": self.width_px.value(),
            "anchor": anchor, "margin_x": self.margin_x.value(), "margin_y": self.margin_y.value(),
            "offset_x": self.offset_x.value(), "offset_y": self.offset_y.value(),
            "keep_inside": self.keep_inside.isChecked(), "opacity": self.opacity.value(),
            "rotation": self.rotation.value(), "blend_mode": self.blend.currentData(),
            "distribution": self.distribution.currentData(), "minimum_gap": self.minimum_gap.value(),
            "interval_px": self.interval_px.value(), "trim_transparent": self.trim_transparent.isChecked(),
            "repeat": self.repeat.isChecked(), "auto_count": self.auto_count.isChecked(),
            "repeat_spacing_x": self.repeat_x.value(),
            "repeat_spacing_y": self.repeat_y.value(), "repeat_count": self.repeat_count.value(),
            "avoid_text": self.avoid_text.isChecked(), "seam_safe": self.seam_safe.isChecked(),
            "visible_preview": True,
        })

    def set_scope_status(self, current_enabled: bool, enabled_count: int, total: int, visible_count: int = 0) -> None:
        current = "activa en esta página" if current_enabled else "solo en previsualización"
        distribution = f" · {visible_count} marca(s) visibles"
        if self.repeat.isChecked():
            distribution += " en esta página · reparto continuo del capítulo"
        self.status.setText(f"Marca {current} · {enabled_count} de {total} página(s) configuradas{distribution}.")

    def set_preview_info(self, page_size: tuple[int, int], settings: dict | None, total_count: int) -> None:
        """Describe the same resolved logo that the canvas and exporter use."""
        mark = prepare_watermark(page_size, settings) if settings else None
        if mark is None:
            self.size_summary.setText("Sin logo visible. Selecciona un PNG con contenido.")
        else:
            self.size_summary.setText(f"Tamaño final: {mark.width} × {mark.height} px · página {page_size[0]} × {page_size[1]} px")
        self.plan_summary.setText(f"{total_count} marca(s) en el capítulo. Las zonas sin espacio pueden quedar sin marca.")

    def _update_size_visibility(self, *_args) -> None:
        percent = self.size_mode.currentData() == "percent"
        self.scale_percent.setEnabled(percent); self.width_px.setEnabled(not percent)
        self.scale_percent.setVisible(percent); self.scale_label.setVisible(percent)
        self.width_px.setVisible(not percent); self.width_label.setVisible(not percent)
        automatic = self.auto_count.isChecked()
        self.interval_px.setVisible(automatic); self.interval_label.setVisible(automatic)
        self.repeat_count.setVisible(not automatic); self.count_label.setVisible(not automatic)
        self.auto_count.setEnabled(self.repeat.isChecked())
        self.interval_px.setEnabled(self.repeat.isChecked() and self.auto_count.isChecked())
        self.repeat_count.setEnabled(self.repeat.isChecked() and not self.auto_count.isChecked())
        self.distribution.setEnabled(self.repeat.isChecked())
        self.minimum_gap.setEnabled(self.repeat.isChecked())
        self.seam_safe.setEnabled(self.repeat.isChecked())

    def _emit(self, *_args) -> None:
        if not self._loading:
            self._settings = self.values(); self.settings_changed.emit(dict(self._settings))
            self._update_source_preview()

    def _choose_png(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Seleccionar marca de agua", "", "PNG con transparencia (*.png)")
        if filename:
            self._load_png(Path(filename))

    def _load_png(self, path: Path) -> None:
        try:
            payload = path.read_bytes()
            image = QImage.fromData(payload, "PNG")
            if image.isNull():
                raise ValueError("El archivo no es un PNG válido.")
        except (OSError, ValueError) as error:
            self.status.setText(str(error)); return
        self._settings.update({"source_name": path.name, "source_path": str(path), "png_bytes": payload})
        self._update_source_preview(); self._emit()

    def _update_source_preview(self) -> None:
        payload = self._settings.get("png_bytes", b"")
        image = QImage.fromData(payload, "PNG") if payload else QImage()
        if not image.isNull():
            preview = prepare_watermark((190, 100), {
                **self._settings, "size_mode": "pixels", "width_px": 190,
                "rotation": 0, "opacity": 100, "margin_x": 0, "margin_y": 0,
                "chapter_fit_height": 100, "keep_inside": True,
            })
            if preview is None:
                self.drop_zone.setPixmap(QPixmap())
                self.drop_zone.setText("El PNG no contiene un logo visible.")
                self.source_label.setText(self._settings.get("source_name") or "Marca PNG")
                return
            pixels = preview.tobytes()
            visible_image = QImage(pixels, preview.width, preview.height, preview.width * 4, QImage.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(visible_image)
            self.drop_zone.setPixmap(pixmap)
            self.source_label.setText(f"{self._settings.get('source_name') or 'Marca PNG'} · {image.width()} × {image.height()} px")
        else:
            self.drop_zone.setPixmap(QPixmap()); self.drop_zone.setText("Arrastra un PNG aquí\no pulsa para seleccionarlo")
            self.source_label.setText("Sin archivo seleccionado")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(Path(url.toLocalFile()).suffix.lower() == ".png" for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".png":
                self._load_png(path); event.acceptProposedAction(); return
        event.ignore()

    def closeEvent(self, event) -> None:
        self.dialog_closed.emit(); super().closeEvent(event)
