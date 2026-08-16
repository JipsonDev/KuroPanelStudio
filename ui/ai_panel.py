"""Compact AI controls that preserve the editor's original panel rhythm."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QRadioButton, QScrollArea, QSlider,
                               QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ui.widgets.controls import ModernButton, SectionTitle, Toggle


class AIOptionsPanel(QScrollArea):
    action_requested = Signal(str)
    mode_changed = Signal(str)
    provider_changed = Signal(str, str, str)
    # NEW: thickness / margin / opacity / manga-mode now actually reach the rest
    # of the app instead of living only inside the widgets that display them.
    style_options_changed = Signal(str, dict)  # mode, {"thickness", "margin", "opacity", "manga_mode"?}

    MODES = (("ocr", "&OCR"), ("translation", "&Traducir"), ("clean", "&Limpiar"))

    # Centralised sizing so every combo/spinbox in the panel stays visually consistent.
    _COMBO_HEIGHT = 38
    _SPIN_WIDTH = 105
    _SPIN_HEIGHT = 34

    STATUS_OK = "#43D98A"
    STATUS_WARNING = "#FFB84D"
    STATUS_NEUTRAL = "#AAB8C5"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(250)
        self.setMaximumWidth(310)
        content = QFrame()
        content.setObjectName("AIPanel")
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(6, 10, 6, 10)
        root.setSpacing(0)
        self.group = QGroupBox("Herramientas IA")
        self.group.setObjectName("AIGroup")
        self.group.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        root.addWidget(self.group)
        layout = QVBoxLayout(self.group)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(7)

        self._mode_index = {key: i for i, (key, _) in enumerate(self.MODES)}
        self._status_labels: dict[str, QLabel] = {}
        self._style_widgets: dict[str, dict] = {}
        self._primary_buttons: dict[str, ModernButton] = {}
        self.current_mode = "ocr"

        modes = QGridLayout()
        modes.setContentsMargins(0, 0, 0, 0)
        modes.setHorizontalSpacing(6)
        modes.setVerticalSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        for index, (key, text) in enumerate(self.MODES):
            radio = QRadioButton(text)
            radio.setObjectName("AIMode")
            radio.setToolTip(f"Cambiar al modo {text.replace('&', '')}")
            radio.toggled.connect(lambda checked, value=key: checked and self._set_mode(value))
            self.mode_group.addButton(radio, index)
            self.mode_buttons[key] = radio
            radio.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            if index == 2:
                modes.addWidget(radio, 1, 0, 1, 2)
            else:
                modes.addWidget(radio, 0, index)
        layout.addLayout(modes)

        self.pages = QStackedWidget()
        self.pages.setObjectName("AIModePages")
        self.pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.pages.addWidget(self._ocr_page())
        self.pages.addWidget(self._translation_page())
        self.pages.addWidget(self._clean_page())
        layout.addWidget(self.pages)
        self.mode_buttons["ocr"].setChecked(True)
        root.addStretch()

    # ------------------------------------------------------------------ #
    # Configuration in/out
    # ------------------------------------------------------------------ #
    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(value)

    def set_configuration(self, settings: dict) -> None:
        self._select(self.ocr_provider, settings["ocr"]["platform"])
        self._select(self.ocr_model, settings["ocr"]["model"])
        self._select(self.translate_provider, settings["translate"]["platform"])
        if self.translate_model.findText(settings["translate"]["model"]) < 0:
            self.translate_model.addItem(settings["translate"]["model"])
        self._select(self.translate_model, settings["translate"]["model"])
        self._select(self.clean_provider, settings["clean"]["platform"])
        self._select(self.clean_model, settings["clean"]["model"])

    @staticmethod
    def _configuration(provider: QComboBox, model: QComboBox) -> tuple[str, str]:
        return provider.currentText(), model.currentText()

    def ocr_configuration(self) -> tuple[str, str]:
        return self._configuration(self.ocr_provider, self.ocr_model)

    def translate_configuration(self) -> tuple[str, str]:
        return self._configuration(self.translate_provider, self.translate_model)

    def clean_configuration(self) -> tuple[str, str]:
        return self._configuration(self.clean_provider, self.clean_model)

    def style_options(self, mode: str) -> dict:
        """Current thickness/margin/opacity/manga-mode values for a page, read live."""
        widgets = self._style_widgets.get(mode, {})
        result = {}
        if "thickness" in widgets:
            result["thickness"] = widgets["thickness"].value()
        if "margin" in widgets:
            result["margin"] = widgets["margin"].value()
        if "opacity" in widgets:
            result["opacity"] = widgets["opacity"].value()
        if "manga" in widgets:
            result["manga_mode"] = widgets["manga"].isChecked()
        return result

    def set_style_options(self, mode: str, options: dict) -> None:
        """Restore saved per-page controls without changing their layout."""
        widgets = self._style_widgets.get(mode, {})
        for key in ("thickness", "margin", "opacity"):
            if key in widgets and key in options:
                widgets[key].blockSignals(True)
                widgets[key].setValue(int(options[key]))
                widgets[key].blockSignals(False)
        if "manga" in widgets and "manga_mode" in options:
            widgets["manga"].blockSignals(True)
            widgets["manga"].setChecked(bool(options["manga_mode"]))
            widgets["manga"].blockSignals(False)

    def set_status(self, mode: str, text: str, color: str = STATUS_NEUTRAL) -> None:
        """Update a page's status line, e.g. once credentials are actually verified."""
        label = self._status_labels.get(mode)
        if label is not None:
            label.setText(f"\u25cf  {text}")
            label.setToolTip(text)
            label.setStyleSheet(f"color:{color}; font-size:10px;")

    def set_ready(self, mode: str, ready: bool, reason: str = "") -> None:
        """Enable/disable a page's primary action, e.g. while a provider is unauthenticated."""
        button = self._primary_buttons.get(mode)
        if button is not None:
            button.setEnabled(ready)
            button.setToolTip(reason if not ready else "")

    # ------------------------------------------------------------------ #
    # Shared builders
    # ------------------------------------------------------------------ #
    @staticmethod
    def _page_layout() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 3, 0, 0)
        layout.setSpacing(7)
        return page, layout

    @classmethod
    def _combo(cls, values: list[str]) -> QComboBox:
        box = QComboBox()
        box.addItems(values)
        box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(8)
        box.setMinimumWidth(0)
        box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        box.setMinimumHeight(cls._COMBO_HEIGHT)
        return box

    def _status(self, mode: str, text: str, color: str) -> QLabel:
        status = QLabel(f"\u25cf  {text}")
        status.setObjectName("StatusLabel")
        status.setWordWrap(True)
        status.setMinimumWidth(0)
        status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status.setToolTip(text)
        status.setStyleSheet(f"color:{color}; font-size:10px;")
        self._status_labels[mode] = status
        return status

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        line.setObjectName("Divider")
        return line

    def _action_row(self, layout: QVBoxLayout, buttons: tuple[tuple[str, str, str], ...]) -> list[ModernButton]:
        row = QGridLayout()
        row.setHorizontalSpacing(7)
        row.setVerticalSpacing(7)
        created: list[ModernButton] = []
        for index, (text, action, tip) in enumerate(buttons):
            button = ModernButton(text)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            if tip:
                button.setToolTip(tip)
            button.clicked.connect(lambda _, value=action: self.action_requested.emit(value))
            created.append(button)
            if len(buttons) == 3 and index == 2:
                row.addWidget(button, 1, 0, 1, 2)
            else:
                row.addWidget(button, index // 2, index % 2)
        layout.addLayout(row)
        return created

    # ------------------------------------------------------------------ #
    # Pages
    # ------------------------------------------------------------------ #
    def _ocr_page(self) -> QWidget:
        page, layout = self._page_layout()
        self.ocr_provider = self._combo(["Alibaba Cloud"])
        layout.addWidget(QLabel("PROVEEDOR OCR"))
        layout.addWidget(self.ocr_provider)
        layout.addWidget(QLabel("NOMBRE DEL MODELO"))
        self.ocr_model = self._combo([
            "qwen-vl-ocr",
            "qwen-vl-ocr-latest",
            "qwen2.5-vl-72b-instruct",
            "qwen2.5-vl-7b-instruct",
            "qwen-vl-max",
            "qwen-vl-plus",
            "qwen-vl-ocr-2025-11-20",
        ])
        self.ocr_model.setEditable(True)
        self.ocr_model.setInsertPolicy(QComboBox.NoInsert)
        self.ocr_model.lineEdit().setPlaceholderText("Escribe el identificador del modelo")
        self.ocr_model.setToolTip(
            "Selecciona un modelo o escribe su identificador exacto de Alibaba Cloud"
        )
        self.ocr_provider.currentTextChanged.connect(lambda: self.provider_changed.emit("ocr", *self.ocr_configuration()))
        self.ocr_model.activated.connect(
            lambda _index: self.provider_changed.emit("ocr", *self.ocr_configuration())
        )
        self.ocr_model.lineEdit().editingFinished.connect(
            lambda: self.provider_changed.emit("ocr", *self.ocr_configuration())
        )
        layout.addWidget(self.ocr_model)
        model_hint = QLabel("Puedes pegar aquí un modelo nuevo sin modificar el código.")
        model_hint.setObjectName("Muted")
        model_hint.setWordWrap(True)
        layout.addWidget(model_hint)
        layout.addWidget(self._status("ocr", "Configura las credenciales del proveedor", self.STATUS_WARNING))
        layout.addWidget(QLabel("ALCANCE"))
        self._action_row(layout, (
            ("Una caja", "run_ocr_api_one", "Ejecuta OCR solo en la caja seleccionada"),
            ("Página", "run_ocr_api", "Ejecuta OCR en todas las cajas de la página actual"),
            ("Todo el capítulo", "run_ocr_api_all", "Ejecuta OCR en todas las páginas del capítulo"),
        ))
        layout.addWidget(self._divider())
        self._add_text_options(layout, mode="ocr", include_mask=False, include_manga=True)
        return page

    def _translation_page(self) -> QWidget:
        page, layout = self._page_layout()
        self.translate_provider = self._combo([
            "Alibaba Cloud", "Gemini", "OpenAI", "DeepSeek", "DeepL",
        ])
        self.translate_model = self._combo([
            "qwen-mt-flash", "qwen-mt-plus", "qwen-plus", "gemini-2.5-flash",
            "gpt-5-mini", "deepseek-chat", "deepseek-reasoner", "deepl",
        ])
        self.translate_model.setEditable(True)
        self.translate_model.setInsertPolicy(QComboBox.NoInsert)
        self.translate_model.setToolTip("Selecciona un modelo o escribe el identificador exacto disponible en tu cuenta")
        self.translate_provider.currentTextChanged.connect(lambda: self.provider_changed.emit("translate", *self.translate_configuration()))
        self.translate_provider.currentTextChanged.connect(self._sync_translation_model)
        self.translate_model.currentTextChanged.connect(lambda: self.provider_changed.emit("translate", *self.translate_configuration()))
        layout.addWidget(self.translate_provider)
        layout.addWidget(self.translate_model)
        layout.addWidget(self._status("translation", "Proveedor no autenticado", self.STATUS_WARNING))
        self.translation_context = QLabel("Sin proyecto de traducción asignado")
        self.translation_context.setObjectName("Muted")
        self.translation_context.setWordWrap(True)
        layout.addWidget(self.translation_context)
        layout.addWidget(QLabel("ALCANCE"))
        translation_buttons = self._action_row(layout, (
            ("Una caja", "run_translation_one", "Traduce solo la caja seleccionada"),
            ("Página", "run_translation", "Traduce todas las cajas de la página actual"),
            ("Todo el capítulo", "run_translation_all", "Traduce todas las páginas del capítulo"),
        ))
        translation_buttons[1].setObjectName("Primary")
        self._primary_buttons["translation"] = translation_buttons[1]
        layout.addWidget(self._divider())
        self._add_text_options(layout, mode="translation", include_mask=False, include_manga=True)
        return page

    def set_translation_context(self, project: str, term_count: int) -> None:
        if project:
            self.translation_context.setText(
                f"Proyecto: {project} · {max(0, int(term_count))} término(s) del glosario activos"
            )
            self.translation_context.setStyleSheet("color:#43D98A;")
        else:
            self.translation_context.setText("Sin proyecto asignado · los términos nuevos no se conservarán")
            self.translation_context.setStyleSheet("color:#FFB84D;")

    def _sync_translation_model(self, provider: str) -> None:
        preferred = {
            "Alibaba Cloud": "qwen-mt-flash", "Gemini": "gemini-2.5-flash",
            "OpenAI": "gpt-5-mini", "DeepSeek": "deepseek-chat", "DeepL": "deepl",
        }.get(provider)
        if preferred:
            self.translate_model.setCurrentText(preferred)

    def _clean_page(self) -> QWidget:
        page, layout = self._page_layout()
        # Do not expose the old remote selector: it never had an implementation.
        self.clean_provider = self._combo(["Local (AI)"])
        self.clean_model = self._combo(["LaMa \u00b7 lama.onnx"])
        self.clean_provider.currentTextChanged.connect(lambda: self.provider_changed.emit("clean", *self.clean_configuration()))
        self.clean_model.currentTextChanged.connect(lambda: self.provider_changed.emit("clean", *self.clean_configuration()))
        layout.addWidget(self.clean_provider)
        layout.addWidget(self.clean_model)
        layout.addWidget(self._status("clean", "LaMa se cargará al primer uso", self.STATUS_NEUTRAL))
        layout.addWidget(QLabel("ALCANCE"))
        cleaning_buttons = self._action_row(layout, (
            ("Una caja", "run_clean_one", "Limpia solo la caja seleccionada"),
            ("Página", "run_clean", "Detecta la máscara de todas las cajas de la página"),
            ("Todo el capítulo", "run_clean_all", "Limpia todas las imágenes del capítulo"),
        ))
        cleaning_buttons[1].setObjectName("Primary")
        self._primary_buttons["clean"] = cleaning_buttons[1]
        self.mask_preview_hint = QLabel("Primero revisa la máscara roja. Puedes borrar falsos positivos antes de aplicar LaMa.")
        self.mask_preview_hint.setObjectName("Muted")
        self.mask_preview_hint.setWordWrap(True)
        self.mask_preview_hint.setMinimumWidth(0)
        self.mask_preview_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.mask_preview_hint)
        self.mask_erase_button = ModernButton("Editar máscara", icon_name="eraser")
        self.mask_erase_button.setMinimumWidth(0)
        self.mask_erase_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.mask_erase_button.setCheckable(True)
        self.mask_erase_button.toggled.connect(
            lambda checked: self.action_requested.emit(
                "mask_preview_erase_on" if checked else "mask_preview_erase_off"
            )
        )
        layout.addWidget(self.mask_erase_button)
        preview_actions = QVBoxLayout()
        preview_actions.setSpacing(8)
        self.apply_mask_button = ModernButton("Aplicar limpieza", "Primary")
        self.apply_mask_button.setMinimumWidth(0)
        self.apply_mask_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.apply_mask_button.clicked.connect(lambda: self.action_requested.emit("apply_clean_mask"))
        self.cancel_mask_button = ModernButton("Cancelar")
        self.cancel_mask_button.setMinimumWidth(0)
        self.cancel_mask_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.cancel_mask_button.clicked.connect(lambda: self.action_requested.emit("cancel_clean_mask"))
        preview_actions.addWidget(self.apply_mask_button, 1)
        preview_actions.addWidget(self.cancel_mask_button, 1)
        layout.addLayout(preview_actions)
        self.quality_button = ModernButton("Control de calidad", icon_name="check")
        self.quality_button.setMinimumWidth(0)
        self.quality_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.quality_button.setToolTip("Comparar original, máscara protegida y resultado caja por caja")
        self.quality_button.clicked.connect(lambda: self.action_requested.emit("open_clean_quality"))
        self.quality_button.setEnabled(False)
        layout.addWidget(self.quality_button)
        self.set_mask_preview_active(False)
        layout.addWidget(SectionTitle("Retoque manual"))
        brush_row = QHBoxLayout()
        brush_row.setSpacing(6)
        self.retouch_button = ModernButton("", icon_name="brush")
        self.retouch_button.setAccessibleName("Pintar (P)")
        self.retouch_button.setMinimumWidth(0)
        self.retouch_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.retouch_button.setCheckable(True)
        self.retouch_button.setToolTip("Pincel de color (P). Alt + clic toma un color del lienzo")
        self.retouch_button.toggled.connect(
            lambda checked: self.action_requested.emit("retouch_on" if checked else "retouch_off")
        )
        brush_row.addWidget(self.retouch_button, 1)
        self.clone_button = ModernButton("", icon_name="stamp")
        self.clone_button.setAccessibleName("Tampón de clonar (S)")
        self.clone_button.setMinimumWidth(0)
        self.clone_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.clone_button.setCheckable(True)
        self.clone_button.setToolTip("Tampón de clonar (S). Alt + clic fija el origen de textura")
        self.clone_button.toggled.connect(
            lambda checked: self.action_requested.emit("clone_on" if checked else "clone_off")
        )
        brush_row.addWidget(self.clone_button, 1)
        self.heal_button = ModernButton("", icon_name="sparkles")
        self.heal_button.setAccessibleName("Corrector puntual (H)")
        self.heal_button.setMinimumWidth(0)
        self.heal_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.heal_button.setCheckable(True)
        self.heal_button.setToolTip("Corrector puntual (H). Pinta para mezclar y fusionar la textura circundante")
        self.heal_button.toggled.connect(
            lambda checked: self.action_requested.emit("heal_on" if checked else "heal_off")
        )
        brush_row.addWidget(self.heal_button, 1)
        self.restore_button = ModernButton("", icon_name="refresh")
        self.restore_button.setAccessibleName("Restaurar original (R)")
        self.restore_button.setMinimumWidth(0)
        self.restore_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.restore_button.setCheckable(True)
        self.restore_button.setToolTip(
            "Recupera con el pincel los píxeles exactos de la imagen original, sin quitar otros retoques"
        )
        self.restore_button.toggled.connect(
            lambda checked: self.action_requested.emit("restore_on" if checked else "restore_off")
        )
        brush_row.addWidget(self.restore_button, 1)
        self.mask_brush_button = ModernButton("", icon_name="eraser")
        self.mask_brush_button.setAccessibleName("Crear máscara (M)")
        self.mask_brush_button.setMinimumWidth(0)
        self.mask_brush_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.mask_brush_button.setCheckable(True)
        self.mask_brush_button.setToolTip("Pincel de máscara (M). Dibuja, revisa en rojo y luego aplica LaMa")
        self.mask_brush_button.toggled.connect(
            lambda checked: self.action_requested.emit("mask_brush_on" if checked else "mask_brush_off")
        )
        brush_row.addWidget(self.mask_brush_button, 1)
        layout.addLayout(brush_row)
        picker_row = QHBoxLayout()
        picker_row.setSpacing(8)
        self.picker_button = ModernButton("Cuentagotas", icon_name="pipette")
        self.picker_button.setToolTip("Cuentagotas: toma un color del lienzo (también Alt + clic)")
        self.picker_button.clicked.connect(lambda: self.action_requested.emit("retouch_pick"))
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(24, 24)
        self.set_retouch_color(QColor("#FFFFFF"))
        picker_row.addWidget(self.picker_button, 1)
        picker_row.addWidget(self.color_swatch)
        layout.addLayout(picker_row)
        repair_row = QHBoxLayout()
        repair_row.setSpacing(6)
        remove_retouch = ModernButton("Deshacer", icon_name="undo")
        remove_retouch.setMinimumWidth(0)
        remove_retouch.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        remove_retouch.setToolTip("Elimina el último parche del pincel, incluso si venía guardado en el proyecto")
        remove_retouch.clicked.connect(lambda: self.action_requested.emit("remove_last_retouch"))
        repair_row.addWidget(remove_retouch, 1)
        remove_artifacts = ModernButton("Reparar antiguos", icon_name="refresh")
        remove_artifacts.setMinimumWidth(0)
        remove_artifacts.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        remove_artifacts.setToolTip("Elimina los trazos largos creados por el antiguo error de desplazamiento")
        remove_artifacts.clicked.connect(lambda: self.action_requested.emit("remove_broken_retouches"))
        repair_row.addWidget(remove_artifacts, 1)
        layout.addLayout(repair_row)
        brush_help = QLabel("P color · S clonar · H corrector · R restaurar · M máscara  |  Shift+mover: tamaño  |  Alt+clic: origen/color")
        brush_help.setObjectName("Muted")
        brush_help.setWordWrap(True)
        layout.addWidget(brush_help)
        layout.addWidget(self._divider())
        self._add_text_options(layout, mode="clean", include_mask=False, include_manga=False)
        return page

    def set_retouch_color(self, color: QColor) -> None:
        self.color_swatch.setToolTip(color.name().upper())
        border = "#00CFE8" if color.lightness() < 45 else "#456072"
        self.color_swatch.setStyleSheet(
            f"background:{color.name()}; border:1px solid {border}; border-radius:5px;"
        )

    def sync_brush_mode(self, mode: str | None) -> None:
        """Reflect the active canvas tool without causing recursive actions."""
        for button, active in (
            (self.retouch_button, mode == "paint"),
            (self.clone_button, mode == "clone"),
            (self.heal_button, mode == "heal"),
            (self.restore_button, mode == "restore"),
            (self.mask_brush_button, mode == "mask"),
        ):
            button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(False)

    def set_mask_preview_active(self, active: bool) -> None:
        for widget in (
            getattr(self, "mask_erase_button", None),
            getattr(self, "apply_mask_button", None),
            getattr(self, "cancel_mask_button", None),
        ):
            if widget is not None:
                widget.setEnabled(bool(active))
        if not active and hasattr(self, "mask_erase_button"):
            self.mask_erase_button.blockSignals(True)
            self.mask_erase_button.setChecked(False)
            self.mask_erase_button.blockSignals(False)

    def set_quality_available(self, available: bool, attention: int = 0) -> None:
        self.quality_button.setEnabled(bool(available))
        self.quality_button.setText(
            f"Control de calidad · {int(attention)} por revisar"
            if attention else "Control de calidad"
        )

    def set_brush_size(self, size: int) -> None:
        field = self._style_widgets.get("clean", {}).get("thickness")
        if field is not None:
            field.blockSignals(True)
            field.setValue(int(size))
            field.blockSignals(False)

    # ------------------------------------------------------------------ #
    # Only expose controls with a real consumer. Brush opacity belongs to the
    # retouch layer; OCR has no independent mask thickness or margin.
    # ------------------------------------------------------------------ #
    def _add_text_options(self, layout: QVBoxLayout, mode: str, include_mask: bool, include_manga: bool) -> None:
        layout.addSpacing(2)
        layout.addWidget(SectionTitle("Ajustes del pincel" if mode == "clean" else "Flujo y lectura"))
        if mode == "ocr":
            for text, action, icon_name in (
                ("Abrir imágenes", "open_folder", "folder"),
                ("Detectar cajas (YOLO)", "run_yolo", "sparkles"),
            ):
                button = ModernButton(text, icon_name=icon_name)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                if action == "run_yolo":
                    button.setToolTip("Autodetectar cajas de texto con YOLO")
                button.clicked.connect(lambda _, value=action: self.action_requested.emit(value))
                layout.addWidget(button)
        if include_mask:
            brush = ModernButton("Pincel máscara", icon_name="eraser")
            brush.setMinimumWidth(0)
            brush.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            brush.setToolTip("Dibuja manualmente la m\u00e1scara de limpieza")
            brush.clicked.connect(lambda: self.action_requested.emit("mask"))
            layout.addWidget(brush)

        widgets: dict = {}
        if mode == "clean":
            widgets["thickness"] = self._add_numeric(layout, "GROSOR DEL PINCEL", 15, mode)
        if include_manga:
            manga_row = QHBoxLayout()
            manga_row.setSpacing(7)
            manga_toggle = Toggle()
            manga_toggle.setToolTip(
                "Ordena las cajas de cada fila de derecha a izquierda y mantiene el avance de arriba abajo"
            )
            manga_toggle.toggled.connect(
                lambda checked, source=mode: self._manga_mode_changed(source, checked)
            )
            manga_row.addWidget(manga_toggle)
            manga_label = QLabel("Lectura manga · derecha → izquierda")
            manga_label.setToolTip(manga_toggle.toolTip())
            manga_row.addWidget(manga_label)
            manga_row.addStretch()
            layout.addLayout(manga_row)
            widgets["manga"] = manga_toggle
        self._style_widgets[mode] = widgets

        if mode != "clean":
            layout.addWidget(self._divider())
            self._add_clipboard(layout)

    def _manga_mode_changed(self, source_mode: str, checked: bool) -> None:
        """Keep OCR and translation on one unambiguous reading direction."""
        for mode in ("ocr", "translation"):
            toggle = self._style_widgets.get(mode, {}).get("manga")
            if toggle is None or mode == source_mode:
                continue
            toggle.blockSignals(True)
            toggle.setChecked(bool(checked))
            toggle.blockSignals(False)
            self._emit_style_changed(mode)
        self._emit_style_changed(source_mode)

    def _add_numeric(self, layout: QVBoxLayout, label: str, value: int, mode: str) -> QSpinBox:
        row = QHBoxLayout()
        row.setSpacing(6)
        caption = QLabel(label)
        caption.setObjectName("Caption")
        row.addWidget(caption)
        row.addStretch()
        field = QSpinBox()
        field.setObjectName("NumericField")
        field.setRange(0, 999)
        field.setValue(value)
        field.setSuffix(" px")
        field.setFixedWidth(self._SPIN_WIDTH)
        field.setMinimumHeight(self._SPIN_HEIGHT)
        field.valueChanged.connect(lambda _: self._emit_style_changed(mode))
        row.addWidget(field)
        layout.addLayout(row)
        return field

    def _add_opacity(self, layout: QVBoxLayout, mode: str) -> QSlider:
        label = QLabel("OPACIDAD LIMPIEZA")
        label.setObjectName("Caption")
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(8)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(100)
        value_label = QLabel("100%")
        slider.valueChanged.connect(lambda value: (value_label.setText(f"{value}%"), self._emit_style_changed(mode)))
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        layout.addLayout(row)
        return slider

    def _add_clipboard(self, layout: QVBoxLayout) -> None:
        for text, action in (("Copiar página", "copy_current"), ("Copiar capítulo", "copy_all")):
            button = ModernButton(text, icon_name="clipboard")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(lambda _, value=action: self.action_requested.emit(value))
            layout.addWidget(button)
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, action in (("Copiar", "copy_ai"), ("Pegar", "paste_ai")):
            button = ModernButton(text)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(lambda _, value=action: self.action_requested.emit(value))
            row.addWidget(button)
        layout.addLayout(row)

    def _emit_style_changed(self, mode: str) -> None:
        self.style_options_changed.emit(mode, self.style_options(mode))

    def _set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.pages.setCurrentIndex(self._mode_index[mode])
        # A mode change should always reveal its controls from the beginning;
        # retaining the previous page's scroll offset made tools look missing.
        self.verticalScrollBar().setValue(0)
        self.mode_changed.emit(mode)
