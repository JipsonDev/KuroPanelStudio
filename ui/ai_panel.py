"""Compact AI controls that preserve the editor's original panel rhythm."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QScrollArea, QSlider,
                               QSizePolicy, QSpinBox, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from ui.widgets.controls import ModernButton, SectionTitle, Toggle, CollapsibleSection
from ui.widgets.icons import icon


class ActionCardHeader(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ActionCard(QWidget):
    """Large disclosure card with the icon and explanatory line from the editor reference."""

    def __init__(self, title: str, subtitle: str, icon_name: str) -> None:
        super().__init__()
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        container.setSpacing(0)
        self.summary = ActionCardHeader()
        self.summary.setObjectName("ActionCardHeader")
        self.summary.setCursor(Qt.PointingHandCursor)
        summary_layout = QHBoxLayout(self.summary)
        summary_layout.setContentsMargins(12, 9, 12, 9)
        summary_layout.setSpacing(11)
        glyph = QLabel()
        glyph.setObjectName("ActionCardIcon")
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(28, 30)
        glyph.setPixmap(icon(icon_name, "#E9F0FA", 22).pixmap(22, 22))
        summary_layout.addWidget(glyph)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(3)
        self.header = QToolButton()
        self.header.setObjectName("ActionCardButton")
        self.header.setText(title)
        self.header.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.header.setAccessibleName(title)
        copy.addWidget(self.header, 0, Qt.AlignLeft)
        caption = QLabel(subtitle)
        caption.setObjectName("ActionCardSubtitle")
        copy.addWidget(caption)
        summary_layout.addLayout(copy, 1)
        self.chevron = QLabel()
        self.chevron.setFixedSize(18, 18)
        summary_layout.addWidget(self.chevron)
        container.addWidget(self.summary)
        self.body = QFrame()
        self.body.setObjectName("ActionCardBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 10, 12, 12)
        self.body_layout.setSpacing(9)
        self.body.hide()
        container.addWidget(self.body)
        self.header.toggled.connect(self.set_expanded)
        self.summary.clicked.connect(self.header.click)
        self.set_expanded(False)

    def set_expanded(self, expanded: bool) -> None:
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(False)
        self.chevron.setPixmap(icon("chevron-down" if expanded else "chevron-right", "#AAB8CC", 16).pixmap(16, 16))
        self.body.setVisible(expanded)

    def is_expanded(self) -> bool:
        return not self.body.isHidden()


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

    STATUS_OK = "#20D985"
    STATUS_WARNING = "#D7B754"
    STATUS_NEUTRAL = "#94A3B8"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(250)
        self.setMaximumWidth(370)
        content = QFrame()
        content.setObjectName("AIPanel")
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 16)
        root.setSpacing(14)
        self.group = QGroupBox("")
        self.group.setObjectName("AIGroup")
        self.group.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        root.addWidget(self.group)
        layout = QVBoxLayout(self.group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._mode_index = {key: i for i, (key, _) in enumerate(self.MODES)}
        self._status_labels: dict[str, QLabel] = {}
        self._style_widgets: dict[str, dict] = {}
        self._primary_buttons: dict[str, ModernButton] = {}
        self._primary_titles: dict[str, str] = {}
        self._primary_actions: dict[str, tuple[str, str, str]] = {}
        self._ready: dict[str, bool] = {}
        self._action_footers: dict[str, QFrame] = {}
        self.current_mode = "ocr"

        self.chapter_card = QFrame()
        self.chapter_card.setObjectName("ChapterReadyCard")
        chapter_row = QHBoxLayout(self.chapter_card)
        chapter_row.setContentsMargins(12, 11, 11, 11)
        chapter_row.setSpacing(10)
        self.chapter_icon = QLabel()
        self.chapter_icon.setObjectName("ChapterReadyIcon")
        self.chapter_icon.setAlignment(Qt.AlignCenter)
        self.chapter_icon.setFixedSize(26, 26)
        self.chapter_icon.setPixmap(icon("check", "#07111F", 16).pixmap(16, 16))
        chapter_row.addWidget(self.chapter_icon)
        chapter_copy = QVBoxLayout()
        chapter_copy.setSpacing(1)
        chapter_title = QLabel("Capítulo cargado")
        chapter_title.setObjectName("ChapterReadyTitle")
        chapter_copy.addWidget(chapter_title)
        self.chapter_count = QLabel("")
        self.chapter_count.setObjectName("Muted")
        chapter_copy.addWidget(self.chapter_count)
        chapter_row.addLayout(chapter_copy, 1)
        self.chapter_dismiss = QToolButton()
        self.chapter_dismiss.setObjectName("ChapterDismiss")
        self.chapter_dismiss.setIcon(icon("x", "#8FA0B8", 15))
        self.chapter_dismiss.setIconSize(QSize(15, 15))
        self.chapter_dismiss.setToolTip("Ocultar aviso de capítulo")
        self.chapter_dismiss.setCursor(Qt.PointingHandCursor)
        self.chapter_dismiss.clicked.connect(self.chapter_card.hide)
        chapter_row.addWidget(self.chapter_dismiss, 0, Qt.AlignTop)
        root.insertWidget(0, self.chapter_card)
        self.chapter_card.hide()

        self.mode_selector = QWidget()
        modes = QGridLayout(self.mode_selector)
        modes.setContentsMargins(0, 0, 0, 0)
        modes.setHorizontalSpacing(10)
        modes.setVerticalSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QToolButton] = {}
        for index, (key, text) in enumerate(self.MODES):
            radio = QToolButton()
            radio.setText(text)
            radio.setObjectName("AIMode")
            radio.setToolTip(f"Cambiar al modo {text.replace('&', '')}")
            radio.setAccessibleName(text.replace("&", ""))
            radio.setCheckable(True)
            radio.setCursor(Qt.PointingHandCursor)
            radio.setMinimumHeight(40)
            radio.toggled.connect(lambda checked, value=key: checked and self._set_mode(value))
            self.mode_group.addButton(radio, index)
            self.mode_buttons[key] = radio
            radio.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            modes.addWidget(radio, 0, index)
        layout.addWidget(self.mode_selector)

        self.pages = QStackedWidget()
        self.pages.setObjectName("AIModePages")
        self.pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.pages.addWidget(self._ocr_page())
        self.pages.addWidget(self._translation_page())
        self.pages.addWidget(self._clean_page())
        layout.addWidget(self.pages)
        # The scope and primary action stay in view while the settings scroll.
        self.action_footer = QStackedWidget(self)
        self.action_footer.setObjectName("AIActionFooter")
        self.action_footer.setFixedHeight(108)
        for key, _ in self.MODES:
            self.action_footer.addWidget(self._action_footers[key])
        self.setViewportMargins(0, 0, 0, self.action_footer.height())
        self.mode_buttons["ocr"].setChecked(True)
        root.addStretch()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "action_footer"):
            self.action_footer.setGeometry(
                0, self.height() - self.action_footer.height(),
                self.width(), self.action_footer.height(),
            )
            self.action_footer.raise_()

    def set_mode_selector_visible(self, visible: bool) -> None:
        self.mode_selector.setVisible(visible)

    def set_chapter_status(self, count: int) -> None:
        self.chapter_count.setText(f"{count} imagen{'es' if count != 1 else ''} disponible{'s' if count != 1 else ''}.")
        self.chapter_card.setVisible(count > 0)

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
            label.setStyleSheet(f"color:{color}; font-size:12px;")

    def set_ready(self, mode: str, ready: bool, reason: str = "") -> None:
        """Offer configuration as the primary action until a provider is usable."""
        self._ready[mode] = bool(ready)
        button = self._primary_buttons.get(mode)
        if button is not None:
            if ready:
                button.setText(self._primary_titles[mode])
                glyph = {"ocr": "script", "translation": "languages", "clean": "eraser"}.get(mode, "settings")
            else:
                button.setText("Configurar OCR" if mode == "ocr" else "Configurar traducción")
                glyph = "settings"
            button.setIcon(icon(glyph, "#07111F", 20))
            button.setToolTip(reason if not ready else "")
            getattr(self, f"{mode}_scope").setEnabled(ready)

    def is_ready(self, mode: str) -> bool:
        return self._ready.get(mode, True)

    # ------------------------------------------------------------------ #
    # Shared builders
    # ------------------------------------------------------------------ #
    @staticmethod
    def _page_layout() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 3, 0, 0)
        layout.setSpacing(12)
        return page, layout

    def _heading(self, layout, title, description, icon_name=None):
        heading_block = QWidget()
        block = QVBoxLayout(heading_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(8)
        if icon_name:
            glyph = QLabel()
            glyph.setObjectName("WorkflowIcon")
            glyph.setPixmap(icon(icon_name, "#E9F0FA", 17).pixmap(17, 17))
            glyph.setAlignment(Qt.AlignCenter)
            glyph.setFixedSize(26, 26)
            row.addWidget(glyph)
        heading = QLabel(title)
        heading.setObjectName("WorkflowHeading")
        row.addWidget(heading)
        if icon_name:
            info = QLabel()
            info.setPixmap(icon("info", "#94A3B8", 14).pixmap(14, 14))
            info.setToolTip(description)
            row.addWidget(info)
        row.addStretch()
        block.addLayout(row)
        hint = QLabel(description)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        block.addWidget(hint)
        layout.addWidget(heading_block)

    def _scope_action(self, layout, mode, title, actions):
        footer = QFrame()
        footer.setObjectName("AIActionFooterPage")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(14, 9, 14, 11)
        footer_layout.setSpacing(7)
        scope = self._combo(["Caja seleccionada", "Página actual", "Todo el capítulo"])
        scope.setProperty("kuro_i18n_choices", True)
        for index in range(scope.count()):
            scope.setItemData(index, index)
        scope.setAccessibleName(f"Alcance de {title}")
        scope.setCurrentIndex(1)
        footer_layout.addWidget(scope)
        button = ModernButton(title, "Primary", icon_name={
            "ocr": "script", "translation": "languages", "clean": "eraser",
        }.get(mode))
        button.setMinimumHeight(36)
        button.clicked.connect(lambda: self._request_primary_action(mode, scope.currentIndex()))
        if mode == "clean":
            scope.currentIndexChanged.connect(
                lambda index: button.setText("Limpiar capítulo" if index == 2 else title)
            )
        footer_layout.addWidget(button)
        self._primary_buttons[mode] = button
        self._primary_titles[mode] = title
        self._primary_actions[mode] = actions
        self._ready[mode] = True
        self._action_footers[mode] = footer
        return scope

    def _request_primary_action(self, mode: str, scope_index: int) -> None:
        if self._ready.get(mode, True):
            self.action_requested.emit(self._primary_actions[mode][scope_index])
        else:
            self.action_requested.emit("settings")

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
        status.setStyleSheet(f"color:{color}; font-size:12px;")
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
        self._heading(layout, "Leer texto (OCR)", "Detecta las cajas y reconoce solo el texto pendiente.", "scan")
        self.ocr_settings = ActionCard("Proveedor y modelo", "Configura el motor de OCR", "chip")
        config = self.ocr_settings.body_layout
        self.ocr_provider = self._combo(["Alibaba Cloud"])
        config.addWidget(QLabel("Modelo"))
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
        config.addWidget(self.ocr_model)
        model_hint = QLabel("Selecciona un modelo o escribe el identificador disponible en tu cuenta.")
        model_hint.setObjectName("Muted")
        model_hint.setWordWrap(True)
        config.addWidget(model_hint)
        layout.addWidget(self._status("ocr", "Configura las credenciales del proveedor", self.STATUS_WARNING))
        action_stack = QVBoxLayout()
        action_stack.setSpacing(9)
        provider_row = QHBoxLayout()
        provider_row.setSpacing(9)
        provider_row.addWidget(self.ocr_provider, 1)
        provider_options = ModernButton("", icon_name="settings")
        provider_options.setAccessibleName("Configurar proveedor OCR")
        provider_options.setToolTip("Abrir proveedor y modelo")
        provider_options.setFixedSize(38, 38)
        provider_options.clicked.connect(lambda: self.ocr_settings.set_expanded(True))
        provider_row.addWidget(provider_options)
        action_stack.addLayout(provider_row)
        detect = ModernButton("Detectar cajas de texto", icon_name="scan")
        detect.setObjectName("DetectAction")
        detect.clicked.connect(lambda: self.action_requested.emit("run_yolo"))
        detect.setMinimumHeight(38)
        action_stack.addWidget(detect)
        self.ocr_scope = self._scope_action(action_stack, "ocr", "Leer texto", ("run_ocr_api_one", "run_ocr_api", "run_ocr_api_all"))
        layout.addLayout(action_stack)
        reread = ModernButton("Releer caja seleccionada")
        reread.setToolTip("Realiza una nueva solicitud al proveedor, aunque haya OCR guardado")
        reread.clicked.connect(lambda: self.action_requested.emit("reread_ocr_one"))
        config.addWidget(reread)
        savings_hint = QLabel("Se reutiliza el OCR guardado. Releer caja realiza una nueva solicitud.")
        savings_hint.setWordWrap(True)
        savings_hint.setObjectName("Muted")
        config.addWidget(savings_hint)
        self.ocr_usage = QLabel("Sesión OCR: sin solicitudes")
        self.ocr_usage.setWordWrap(True)
        self.ocr_usage.setObjectName("Muted")
        config.addWidget(self.ocr_usage)
        layout.addWidget(self.ocr_settings)
        self._add_text_options(layout, mode="ocr", include_mask=False, include_manga=True)
        self.detection_settings = ActionCard("Ajustes de detección", "Sensibilidad, idioma y filtros", "sliders")
        detection_hint = QLabel("La detección usa el modelo local. Puedes detectar cajas antes de leer el texto.")
        detection_hint.setObjectName("Muted")
        detection_hint.setWordWrap(True)
        self.detection_settings.body_layout.addWidget(detection_hint)
        self.detection_settings.body_layout.addWidget(
            ModernButton("Detectar cajas de texto", icon_name="scan")
        )
        self.detection_settings.body_layout.itemAt(1).widget().clicked.connect(
            lambda: self.action_requested.emit("run_yolo")
        )
        layout.addWidget(self.detection_settings)
        layout.addStretch()
        return page

    def set_ocr_usage(self, stats: dict) -> None:
        requests = stats.get("requests", 0)
        reported = stats.get("reported_responses", 0)
        tokens = (
            f"{stats.get('input_tokens', 0):,} entrada · {stats.get('output_tokens', 0):,} salida"
            if reported else "tokens no reportados"
        )
        self.ocr_usage.setText(f"Sesión OCR: {requests} solicitud(es)\n{tokens}")
        self.ocr_usage.setToolTip(
            f"Tokens informados por {reported} respuesta(s). Las solicitudes fallidas pueden no informar consumo. "
            "Este contador se reinicia al cerrar la aplicación y no representa el saldo ni la factura de Alibaba."
        )

    def _translation_page(self) -> QWidget:
        page, layout = self._page_layout()
        self._heading(layout, "Traducir diálogo", "Traduce el texto reconocido usando el glosario del proyecto.", "languages")
        self.translation_settings = ActionCard("Proveedor y modelo", "Configura el motor de traducción", "chip")
        config = self.translation_settings.body_layout
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
        config.addWidget(QLabel("Modelo"))
        config.addWidget(self.translate_model)
        layout.addWidget(self._status("translation", "Proveedor no autenticado", self.STATUS_WARNING))
        provider_row = QHBoxLayout()
        provider_row.setSpacing(9)
        provider_row.addWidget(self.translate_provider, 1)
        provider_options = ModernButton("", icon_name="settings")
        provider_options.setAccessibleName("Configurar proveedor de traducción")
        provider_options.setFixedSize(38, 38)
        provider_options.clicked.connect(lambda: self.translation_settings.set_expanded(True))
        provider_row.addWidget(provider_options)
        layout.addLayout(provider_row)
        self.translation_context = QLabel("Sin proyecto de traducción asignado")
        self.translation_context.setObjectName("Muted")
        self.translation_context.setWordWrap(True)
        layout.addWidget(self.translation_context)
        self.translation_scope = self._scope_action(layout, "translation", "Traducir texto", ("run_translation_one", "run_translation", "run_translation_all"))
        layout.addWidget(self.translation_settings)
        self._add_text_options(layout, mode="translation", include_mask=False, include_manga=True)
        layout.addStretch()
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
        page_layout = layout
        self._heading(layout, "Limpiar página", "Prepara la máscara, revisa los trazos y aplica la limpieza.", "eraser")
        self.clean_settings = ActionCard("Motor de limpieza", "Modelo local y rendimiento", "chip")
        # Do not expose the old remote selector: it never had an implementation.
        self.clean_provider = self._combo(["Local (AI)"])
        self.clean_model = self._combo(["LaMa \u00b7 lama.onnx"])
        self.clean_provider.currentTextChanged.connect(lambda: self.provider_changed.emit("clean", *self.clean_configuration()))
        self.clean_model.currentTextChanged.connect(lambda: self.provider_changed.emit("clean", *self.clean_configuration()))
        self.clean_settings.body_layout.addWidget(QLabel("Modelo local"))
        self.clean_settings.body_layout.addWidget(self.clean_model)
        layout.addWidget(self._status("clean", "LaMa se cargará al primer uso", self.STATUS_NEUTRAL))
        provider_row = QHBoxLayout()
        provider_row.setSpacing(9)
        provider_row.addWidget(self.clean_provider, 1)
        provider_options = ModernButton("", icon_name="settings")
        provider_options.setAccessibleName("Configurar motor de limpieza")
        provider_options.setFixedSize(38, 38)
        provider_options.clicked.connect(lambda: self.clean_settings.set_expanded(True))
        provider_row.addWidget(provider_options)
        layout.addLayout(provider_row)
        self.clean_scope = self._scope_action(layout, "clean", "Preparar limpieza", ("run_clean_one", "run_clean", "run_clean_all"))
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
        self.quality_button.hide()
        layout.addWidget(self.quality_button)
        self.set_mask_preview_active(False)
        layout.addWidget(self.clean_settings)
        self.retouch_section = ActionCard("Retoque manual", "Pinceles, restauración y máscara", "brush")
        layout.addWidget(self.retouch_section)
        layout = self.retouch_section.body_layout
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
        page_layout.addStretch()
        return page

    def set_retouch_color(self, color: QColor) -> None:
        self.color_swatch.setToolTip(color.name().upper())
        border = "#00CFE8" if color.lightness() < 45 else "#456072"
        self.color_swatch.setStyleSheet(
            f"background:{color.name()}; border:1px solid {border}; border-radius:5px;"
        )

    def sync_brush_mode(self, mode: str | None) -> None:
        """Reflect the active canvas tool without causing recursive actions."""
        if mode is not None:
            self.retouch_section.set_expanded(True)
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
        self.mask_preview_hint.setVisible(bool(active))
        for widget in (
            getattr(self, "mask_erase_button", None),
            getattr(self, "apply_mask_button", None),
            getattr(self, "cancel_mask_button", None),
        ):
            if widget is not None:
                widget.setEnabled(bool(active))
                widget.setVisible(bool(active))
        if not active and hasattr(self, "mask_erase_button"):
            self.mask_erase_button.blockSignals(True)
            self.mask_erase_button.setChecked(False)
            self.mask_erase_button.blockSignals(False)

    def set_quality_available(self, available: bool, attention: int = 0) -> None:
        self.quality_button.setEnabled(bool(available))
        self.quality_button.setVisible(bool(available))
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
        if mode != "clean":
            section = ActionCard("Lectura y portapapeles", "Opciones de copia y formato", "clipboard")
            layout.addWidget(section)
            layout = section.body_layout
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
        if hasattr(self, "action_footer"):
            self.action_footer.setCurrentIndex(self._mode_index[mode])
        for index in range(self.pages.count()):
            self.pages.widget(index).setSizePolicy(
                QSizePolicy.Ignored,
                QSizePolicy.Preferred if index == self._mode_index[mode] else QSizePolicy.Ignored,
            )
        # A mode change should always reveal its controls from the beginning;
        # retaining the previous page's scroll offset made tools look missing.
        self.verticalScrollBar().setValue(0)
        self.mode_changed.emit(mode)
