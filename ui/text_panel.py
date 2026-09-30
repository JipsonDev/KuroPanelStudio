from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox,
                               QSizePolicy, QVBoxLayout, QWidget)

from core.typography_manager import TypographyManager, QUICK_TYPOGRAPHY_PRESETS
from ui.font_utils import (
    FontFamilyModel, commit_font_combo_text, configure_searchable_font_combo,
    select_font_family,
)
from ui.widgets.controls import CollapsibleSection, ModernButton, SectionTitle


class TextOptionsPanel(QScrollArea):
    """Always-visible professional typesetting controls for the selected layer."""

    style_changed = Signal(dict)
    preset_save_requested = Signal(str, dict)
    project_type_selected = Signal(str)
    profile_selected = Signal(str, str)
    font_role_selected = Signal(str, str, str, object)
    fit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(270)
        self.setMaximumWidth(370)
        self._loading = False
        self._layer_index = -1
        self._presets: dict[str, dict] = {}
        self._font_file = ""
        self._font_role_changing = False
        self._fonts_loaded = False
        self._color_buttons: dict[QLineEdit, ModernButton] = {}
        content = QFrame()
        content.setObjectName("AIPanel")
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 14, 12, 16)
        root.setSpacing(13)

        root.addWidget(SectionTitle("Herramientas de texto"))
        self.layer_label = QLabel("Selecciona una capa de texto")
        self.layer_label.setObjectName("TextSelectionStatus")
        self.layer_label.setWordWrap(True)
        root.addWidget(self.layer_label)

        quick_card = QFrame()
        quick_card.setObjectName("TextQuickCard")
        quick_grid = QVBoxLayout(quick_card)
        quick_grid.setContentsMargins(11, 11, 11, 12)
        quick_grid.setSpacing(8)
        quick_header = QLabel("ESTILOS RÁPIDOS")
        quick_header.setObjectName("Caption")
        quick_grid.addWidget(quick_header)
        row1 = QHBoxLayout(); row1.setSpacing(8)
        self.btn_dialogue = ModernButton("Diálogo", "Secondary", icon_name="text")
        self.btn_dialogue.setToolTip("Estilo estándar para globos de diálogo (centrado, seminegrita, contorno)")
        self.btn_dialogue.clicked.connect(lambda: self._apply_quick_preset("dialogue"))
        self.btn_shout = ModernButton("Grito", "Secondary", icon_name="sparkles")
        self.btn_shout.setToolTip("Estilo enérgico con mayúsculas y doble trazo para acción o gritos")
        self.btn_shout.clicked.connect(lambda: self._apply_quick_preset("shout"))
        row1.addWidget(self.btn_dialogue, 1); row1.addWidget(self.btn_shout, 1)
        quick_grid.addLayout(row1)
        row2 = QHBoxLayout(); row2.setSpacing(8)
        self.btn_thought = ModernButton("Pensamiento", "Secondary", icon_name="moon")
        self.btn_thought.setToolTip("Estilo cursiva suave con resplandor para pensamientos")
        self.btn_thought.clicked.connect(lambda: self._apply_quick_preset("thought"))
        self.btn_whisper = ModernButton("Susurro", "Secondary", icon_name="edit")
        self.btn_whisper.setToolTip("Estilo ligero y suave para susurros o voces bajas")
        self.btn_whisper.clicked.connect(lambda: self._apply_quick_preset("whisper"))
        row2.addWidget(self.btn_thought, 1); row2.addWidget(self.btn_whisper, 1)
        quick_grid.addLayout(row2)
        self.btn_narrator = ModernButton("Narración", "Secondary", icon_name="script")
        self.btn_narrator.setToolTip("Estilo para cuadros de narración y carteles")
        self.btn_narrator.clicked.connect(lambda: self._apply_quick_preset("narrator"))
        quick_grid.addWidget(self.btn_narrator)
        for button in (self.btn_dialogue, self.btn_shout, self.btn_thought, self.btn_whisper, self.btn_narrator):
            button.setMinimumHeight(40)
            button.setIconSize(QSize(17, 17))
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setCheckable(True)
        self._quick_buttons = {
            "dialogue": self.btn_dialogue, "shout": self.btn_shout,
            "thought": self.btn_thought, "whisper": self.btn_whisper,
            "narrator": self.btn_narrator,
        }
        root.addWidget(quick_card)

        self.profile_section = CollapsibleSection("Proyecto y roles", False)
        profile_layout = self.profile_section.body_layout
        self.profile_summary = QLabel("Sin proyecto tipográfico")
        self.profile_summary.setObjectName("ProfileSummary")
        self.profile_summary.setWordWrap(True)
        profile_layout.addWidget(self.profile_summary)
        self.profile_hint = QLabel("Elige la biblioteca, el proyecto y luego el rol que usará esta capa.")
        self.profile_hint.setObjectName("Muted")
        self.profile_hint.setWordWrap(True)
        profile_layout.addWidget(self.profile_hint)
        profile_layout.addWidget(QLabel("BIBLIOTECA"))
        self.project_type = QComboBox()
        self.project_type.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.project_type.currentIndexChanged.connect(self._project_type_changed)
        profile_layout.addWidget(self.project_type)
        profile_layout.addWidget(QLabel("PROYECTO ACTIVO"))
        self.profile = QComboBox()
        self.profile.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.profile.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.profile.setMinimumContentsLength(8)
        self.profile.currentIndexChanged.connect(self._profile_changed)
        profile_layout.addWidget(self.profile)
        profile_layout.addWidget(QLabel("ROL PARA ESTA CAPA"))
        self.font_role = QComboBox()
        self.font_role.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.font_role.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.font_role.setMinimumContentsLength(8)
        self.font_role.currentIndexChanged.connect(self._font_role_changed)
        profile_layout.addWidget(self.font_role)

        self.font_section = CollapsibleSection("Fuente y color", False)
        font_layout = self.font_section.body_layout
        self.font_source_label = QLabel("Fuente manual · Segoe UI")
        self.font_source_label.setObjectName("FontSourceBadge")
        self.font_source_label.setWordWrap(True)
        self.font_source_label.setToolTip("Puedes reemplazar la fuente aunque la capa tenga un perfil asignado.")
        font_layout.addWidget(self.font_source_label)
        self.family = QComboBox()
        self.family.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.family.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.family.setMinimumContentsLength(8)
        self.family.setEditable(True)
        self.family.setInsertPolicy(QComboBox.NoInsert)
        self.family.setToolTip("Despliega el menú o escribe parte del nombre para filtrar las fuentes")
        self.family.addItem("Segoe UI")
        font_layout.addWidget(QLabel("FUENTE"))
        font_layout.addWidget(self.family)
        font_layout.addWidget(QLabel("ESTILO DE LA FUENTE"))
        self.weight = QComboBox()
        self.weight.addItem("Normal", 400)
        self.weight.addItem("Medium", 500)
        self.weight.addItem("Seminegrita", 600)
        self.weight.addItem("Negrita (Bold)", 700)
        self.weight.addItem("Extra negrita", 800)
        self.weight.addItem("Black", 900)
        self.weight.setProperty("kuro_i18n_choices", True)
        font_layout.addWidget(self.weight)
        self.size_label = QLabel("TAMAÑO MÁXIMO")
        self.size = self._spin(6, 300, " px")
        self.auto_fit = QCheckBox("Ajustar fuente automáticamente")
        self.fit_now = ModernButton("Ajustar texto ahora", "Primary", icon_name="scan")
        self.fit_now.setToolTip("Calcula una vez el mayor tamaño que cabe y vuelve inmediatamente al modo manual.")
        self.fit_now.clicked.connect(self.fit_requested.emit)
        self.text_color = self._color_control("Color del texto", "#111111", font_layout)

        self.advanced_section = CollapsibleSection("Opciones avanzadas", False)
        advanced_layout = self.advanced_section.body_layout
        preview_caption = QLabel("VISTA PREVIA DE LA FUENTE")
        preview_caption.setObjectName("Caption")
        advanced_layout.addWidget(preview_caption)
        self.font_preview = QLabel("Diálogo  ·  GRITOS  ·  Aa 123")
        self.font_preview.setObjectName("TextFontPreview")
        self.font_preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.font_preview.setAlignment(Qt.AlignCenter)
        self.font_preview.setMinimumHeight(72)
        advanced_layout.addWidget(self.font_preview)
        emphasis = QVBoxLayout()
        emphasis.setSpacing(7)
        self.italic = QCheckBox("Cursiva")
        self.underline = QCheckBox("Subrayado")
        self.strikeout = QCheckBox("Tachado")
        emphasis.addWidget(self.italic)
        emphasis.addWidget(self.underline)
        emphasis.addWidget(self.strikeout)
        advanced_layout.addLayout(emphasis)
        advanced_layout.addWidget(QLabel("FORMATO DE TEXTO"))
        self.text_case = QComboBox()
        self.text_case.addItem("Como fue escrito", "original")
        self.text_case.addItem("TODO MAYÚSCULAS", "upper")
        self.text_case.addItem("todo minúsculas", "lower")
        self.text_case.setProperty("kuro_i18n_choices", True)
        advanced_layout.addWidget(self.text_case)

        self.composition_section = CollapsibleSection("Ajuste al globo", True)
        composition_layout = self.composition_section.body_layout
        composition_layout.addWidget(self.size_label)
        composition_layout.addWidget(self.size)
        composition_layout.addWidget(self.auto_fit)
        self.spacing = self._spin(-10, 100, " px")
        self.balloon_fit = QCheckBox("Seguir contorno del globo")
        self.balloon_fit.setToolTip("Centra el texto dentro del área detectada y adapta cada línea al ancho seguro del globo.")
        composition_layout.addWidget(self.balloon_fit)
        self.layout_hint = QLabel("El texto se centra y mantiene distancia del borde detectado.")
        self.layout_hint.setObjectName("Muted")
        self.layout_hint.setWordWrap(True)
        composition_layout.addWidget(self.layout_hint)
        self.balloon_shape_label = QLabel("FORMA DE ADAPTACIÓN")
        composition_layout.addWidget(self.balloon_shape_label)
        self.balloon_shape = QComboBox()
        self.balloon_shape.addItem("Automático (según el globo)", "auto")
        self.balloon_shape.addItem("Ovalado / Elipse (Diálogo)", "ellipse")
        self.balloon_shape.addItem("Diamante / Puntiagudo (Gritos)", "diamond")
        self.balloon_shape.addItem("Rectangular (Cuadros)", "rectangle")
        self.balloon_shape.setProperty("kuro_i18n_choices", True)
        composition_layout.addWidget(self.balloon_shape)
        self.balloon_padding_label = QLabel("MARGEN MÍNIMO DEL GLOBO")
        composition_layout.addWidget(self.balloon_padding_label)
        self.balloon_padding = self._spin(2, 80, " px")
        self.balloon_padding.setToolTip("El programa aumenta este margen en globos grandes o con contornos gruesos para que las letras no toquen el borde.")
        composition_layout.addWidget(self.balloon_padding)
        self.optical_center = QCheckBox("Centrado óptico")
        composition_layout.addWidget(self.optical_center)
        composition_layout.addWidget(self.fit_now)
        advanced_layout.addWidget(QLabel("INTERLINEADO"))
        advanced_layout.addWidget(self.spacing)

        advanced_layout.addWidget(QLabel("REGLAS DEL IDIOMA"))
        self.language = QComboBox()
        self.language.addItem("Detectar automáticamente", "auto")
        self.language.addItem("Español", "es")
        self.language.addItem("Chino", "zh")
        self.language.addItem("Japonés", "ja")
        self.language.addItem("Coreano", "ko")
        self.language.setProperty("kuro_i18n_choices", True)
        advanced_layout.addWidget(self.language)
        self.hyphenation = QCheckBox("Separación silábica en español")
        self.orphan_control = QCheckBox("Evitar palabras huérfanas")
        self.hanging_punctuation = QCheckBox("Puntuación colgante")
        advanced_layout.addWidget(self.hyphenation)
        advanced_layout.addWidget(self.orphan_control)
        advanced_layout.addWidget(self.hanging_punctuation)

        advanced_layout.addWidget(QLabel("ESCALA MANUAL"))
        self.auto_scale = QCheckBox("Comprimir antes de reducir")
        self.max_horizontal_compression = self._spin(0, 20, " %")
        self.scale_x = self._spin(50, 200, " %")
        self.scale_y = self._spin(50, 200, " %")
        advanced_layout.addWidget(self.auto_scale)
        advanced_layout.addWidget(QLabel("COMPRESIÓN MÁXIMA"))
        advanced_layout.addWidget(self.max_horizontal_compression)
        advanced_layout.addWidget(QLabel("ESCALA HORIZONTAL"))
        advanced_layout.addWidget(self.scale_x)
        advanced_layout.addWidget(QLabel("ESCALA VERTICAL"))
        advanced_layout.addWidget(self.scale_y)

        self.alignment = QComboBox(); self.alignment.addItem("Izquierda", "left"); self.alignment.addItem("Centro", "center"); self.alignment.addItem("Derecha", "right")
        self.vertical_alignment = QComboBox(); self.vertical_alignment.addItem("Arriba", "top"); self.vertical_alignment.addItem("Centro", "center"); self.vertical_alignment.addItem("Abajo", "bottom")
        self.alignment.setProperty("kuro_i18n_choices", True)
        self.vertical_alignment.setProperty("kuro_i18n_choices", True)
        self.alignment.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.vertical_alignment.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        advanced_layout.addWidget(QLabel("ALINEACIÓN HORIZONTAL"))
        advanced_layout.addWidget(self.alignment)
        advanced_layout.addWidget(QLabel("POSICIÓN VERTICAL"))
        advanced_layout.addWidget(self.vertical_alignment)
        self.rotation = self._spin(-180, 180, "°")
        self.vertical = QCheckBox("Texto vertical")
        self.vertical.setToolTip("Componer el texto verticalmente")
        advanced_layout.addWidget(QLabel("ROTACIÓN"))
        advanced_layout.addWidget(self.rotation)
        advanced_layout.addWidget(self.vertical)
        self.margin = self._spin(0, 200, " px")
        advanced_layout.addWidget(QLabel("MARGEN INTERIOR DE CAJA"))
        advanced_layout.addWidget(self.margin)
        self.overflow_warning = QLabel("⚠ El texto no cabe dentro del globo")
        self.overflow_warning.setObjectName("WarningText")
        self.overflow_warning.setWordWrap(True)
        self.overflow_warning.hide()
        composition_layout.addWidget(self.overflow_warning)
        # Stroke belongs to the dedicated Effects tool. Keep its backing
        # values for project compatibility, but do not duplicate its controls.
        self.stroke_color = QLineEdit("#FFFFFF"); self.stroke_color.hide()
        self.stroke = self._spin(0, 20, " px")
        self.stroke.hide()

        self.preset_section = CollapsibleSection("Estilos guardados", False)
        preset_layout = self.preset_section.body_layout
        self.preset = QComboBox()
        self.preset.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.preset.addItem("Personalizado")
        self.preset.currentTextChanged.connect(self._preset_selected)
        preset_layout.addWidget(self.preset)
        preset_row = QHBoxLayout()
        self.preset_name = QLineEdit()
        self.preset_name.setMinimumWidth(0)
        self.preset_name.setPlaceholderText("Personaje o narrador")
        save_preset = ModernButton("Guardar", "Primary")
        save_preset.clicked.connect(self._save_preset)
        preset_row.addWidget(self.preset_name, 1)
        preset_row.addWidget(save_preset)
        preset_layout.addLayout(preset_row)
        for section in (
            self.composition_section, self.font_section, self.advanced_section,
            self.profile_section, self.preset_section,
        ):
            root.addWidget(section)

        for section in (
            self.profile_section, self.font_section, self.composition_section,
            self.advanced_section, self.preset_section,
        ):
            section.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            section.header.setMinimumWidth(0)
            section.header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            section.body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            section.set_collapsible(True)

        for widget in (
            self.weight, self.italic, self.underline, self.strikeout, self.text_case,
            self.size, self.spacing, self.auto_fit, self.balloon_fit, self.balloon_shape,
            self.balloon_padding, self.optical_center, self.language, self.hyphenation,
            self.orphan_control, self.hanging_punctuation, self.auto_scale,
            self.max_horizontal_compression, self.scale_x, self.scale_y, self.alignment,
            self.vertical_alignment, self.stroke, self.margin, self.rotation, self.vertical,
        ):
            signal = getattr(widget, "currentTextChanged", None) or getattr(widget, "valueChanged", None) or getattr(widget, "toggled", None)
            signal.connect(self._emit_style)
        self.family.activated.connect(self._family_selected)
        self.family.lineEdit().editingFinished.connect(self._family_editing_finished)
        root.addStretch()
        self._edit_controls = [
            self.btn_dialogue, self.btn_shout, self.btn_thought, self.btn_whisper,
            self.btn_narrator, self.preset, self.preset_name, save_preset, self.font_role,
            self.fit_now,
            self.family, self.weight, self.italic, self.underline, self.strikeout, self.size,
            self.text_case, self.spacing, self.auto_fit, self.balloon_fit, self.balloon_shape,
            self.balloon_padding, self.optical_center, self.language, self.hyphenation,
            self.orphan_control, self.hanging_punctuation, self.auto_scale,
            self.max_horizontal_compression, self.scale_x, self.scale_y,
            self.alignment, self.vertical_alignment, self.text_color,
            self.stroke_color, self.stroke, self.margin, self.rotation, self.vertical,
            *self._color_buttons.values(),
        ]
        self._set_editing_enabled(False)

    def ensure_fonts_loaded(self) -> None:
        if self._fonts_loaded:
            return
        selected = self.family.currentText() or "Segoe UI"
        configure_searchable_font_combo(self.family, selected)
        self.family.lineEdit().textEdited.connect(self._preview_search_result)
        self.family.highlighted.connect(self._preview_font_index)
        self.family.view().entered.connect(self._preview_font_index)
        self._fonts_loaded = True

    def _set_editing_enabled(self, enabled: bool) -> None:
        for widget in self._edit_controls:
            widget.setEnabled(enabled)
        self._sync_control_state()

    def _sync_control_state(self) -> None:
        """Keep dependent controls honest without changing the saved style."""
        active = self._layer_index >= 0
        vertical = self.vertical.isChecked()
        balloon = active and self.balloon_fit.isChecked() and not vertical
        self.balloon_fit.setEnabled(active and not vertical)
        for widget in (
            self.balloon_shape_label, self.balloon_shape,
            self.balloon_padding_label, self.balloon_padding,
        ):
            widget.setEnabled(balloon)
        self.auto_scale.setEnabled(balloon)
        self.max_horizontal_compression.setEnabled(balloon and self.auto_scale.isChecked())
        self.size_label.setText("TAMAÑO MÁXIMO" if self.auto_fit.isChecked() else "TAMAÑO FIJO")
        if vertical:
            hint = "El texto vertical usa la caja; el ajuste al globo queda en pausa."
        elif balloon:
            hint = "El texto se centra y mantiene distancia del borde detectado."
        else:
            hint = "El texto usa la caja rectangular y su margen interior."
        self.layout_hint.setText(hint)

    def _apply_quick_preset(self, preset_key: str) -> None:
        if self._loading or self._layer_index < 0:
            return
        preset = QUICK_TYPOGRAPHY_PRESETS.get(preset_key)
        if not preset:
            return
        merged = {**self.values(), **preset}
        self.set_layer(self._layer_index, merged, preset.get("name", "Personalizado"))
        self.style_changed.emit(self.values())

    def _show_quick_preset(self, preset_name: str) -> None:
        for key, button in self._quick_buttons.items():
            button.setChecked(QUICK_TYPOGRAPHY_PRESETS[key].get("name") == preset_name)

    @staticmethod
    def _spin(low: int, high: int, suffix: str) -> QSpinBox:
        field = QSpinBox()
        field.setRange(low, high)
        field.setSuffix(suffix)
        field.setMinimumHeight(36)
        field.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        field.setMinimumWidth(0)
        return field

    def _color_control(self, label: str, initial: str, layout: QVBoxLayout) -> QLineEdit:
        field = QLineEdit(initial)
        field.hide()
        short_label = "Texto" if label == "Color del texto" else "Contorno"
        button = ModernButton(f"{short_label}  {initial}")
        button.setToolTip(label)
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        button.setMinimumWidth(0)
        button.setProperty("color_label", short_label)
        button.clicked.connect(lambda: self._pick_color(field))
        self._color_buttons[field] = button
        layout.addWidget(button)
        return field

    def _pick_color(self, field: QLineEdit) -> None:
        color = QColorDialog.getColor(QColor(field.text()), self)
        if color.isValid():
            self._set_color(field, color.name().upper())
            self._emit_style()

    def _set_color(self, field: QLineEdit, value: str) -> None:
        field.setText(value)
        button = self._color_buttons.get(field)
        if button is None:
            return
        label = button.property("color_label")
        button.setText(f"{label}  {value}")
        swatch = QPixmap(16, 16); swatch.fill(QColor(value)); button.setIcon(QIcon(swatch))
        button.setStyleSheet("text-align:left;")

    def set_presets(self, presets: dict[str, dict]) -> None:
        current = self.preset.currentText()
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("Personalizado")
        self.preset.addItems(sorted(presets))
        self.preset.setCurrentText(current if current in presets else "Personalizado")
        self.preset.blockSignals(False)
        self._presets = presets

    def set_profiles(
        self,
        project_types: list[str],
        active_type: str,
        projects: list[str],
        active: str,
        locked: bool,
        entries: dict[str, dict],
    ) -> None:
        self._loading = True
        self.project_type.clear()
        self.project_type.addItem("Selecciona una biblioteca…", "")
        for project_type in project_types:
            self.project_type.addItem(project_type, project_type)
        type_index = self.project_type.findData(active_type)
        self.project_type.setCurrentIndex(max(0, type_index))
        self.project_type.setEnabled(not locked)
        self.profile.clear()
        self.profile.addItem("Selecciona un proyecto…", "")
        for project in projects:
            self.profile.addItem(project, project)
        profile_index = self.profile.findData(active)
        self.profile.setCurrentIndex(max(0, profile_index))
        self.profile.setEnabled(not locked)
        lock_tip = "Proyecto fijo durante la edición de este capítulo" if locked else "Selecciona el proyecto que usarás en el capítulo"
        self.project_type.setToolTip(lock_tip)
        self.profile.setToolTip(lock_tip)
        self.font_role.clear()
        self.font_role.addItem("Usar fuente manual", "")
        for alias, entry in entries.items():
            role_style = dict(entry.get("style", {}))
            role_size = int(role_style.get("font_size", 36))
            role_color = str(role_style.get("text_color", "#111111")).upper()
            self.font_role.addItem(
                f"{alias}  —  {entry['family']}  ·  {role_size} px  ·  {role_color}",
                entry["family"],
            )
            index = self.font_role.count() - 1
            self.font_role.setItemData(index, QFont(entry["family"], 11), Qt.FontRole)
            model_item = getattr(self.font_role.model(), "item", lambda _index: None)(index)
            if model_item is not None: model_item.setFont(QFont(entry["family"], 11))
            self.font_role.setItemData(index, alias, Qt.UserRole + 1)
            self.font_role.setItemData(index, entry.get("file", ""), Qt.UserRole + 2)
            self.font_role.setItemData(index, dict(entry.get("style", {})), Qt.UserRole + 3)
        if active_type and active:
            state = "Fijo para este capítulo" if locked else "Listo para usar"
            self.profile_summary.setText(f"{active_type}  ›  {active}\n{state}")
            self.profile_summary.setProperty("active", True)
            self.profile_hint.setText(
                "Elige el rol para la capa seleccionada. El proyecto no cambiará durante este capítulo."
                if locked else "Ahora elige el rol que usará la capa seleccionada."
            )
        else:
            self.profile_summary.setText("Sin proyecto tipográfico\nConfigúralo antes de comenzar el capítulo")
            self.profile_summary.setProperty("active", False)
            self.profile_hint.setText("Selecciona primero una biblioteca y después un proyecto.")
        self.profile_summary.style().unpolish(self.profile_summary)
        self.profile_summary.style().polish(self.profile_summary)
        self._loading = False

    def set_layer(self, index: int, style: dict, preset_name: str = "") -> None:
        self._loading = True
        self._layer_index = index
        self._set_editing_enabled(True)
        self.layer_label.setText(f"Editando capa {index + 1:02} · los cambios se ven en tiempo real")
        normalized = TypographyManager.normalized(style)
        select_font_family(self.family, normalized["font_family"])
        self._font_file = str(normalized.get("font_file", ""))
        self._set_data(self.weight, str(normalized["font_weight"]), numeric=True)
        self.italic.setChecked(normalized["italic"])
        self.underline.setChecked(normalized["underline"])
        self.strikeout.setChecked(normalized["strikeout"])
        self._set_data(self.text_case, normalized["text_case"])
        self.size.setValue(normalized["font_size"])
        self.spacing.setValue(normalized["line_spacing"])
        self.auto_fit.setChecked(normalized["auto_fit"])
        self.balloon_fit.setChecked(normalized["balloon_fit"])
        self._set_data(self.balloon_shape, normalized["balloon_shape"])
        self.balloon_padding.setValue(normalized["balloon_padding"])
        self._set_data(self.language, normalized["language"])
        self.hyphenation.setChecked(normalized["hyphenation"])
        self.orphan_control.setChecked(normalized["orphan_control"])
        self.hanging_punctuation.setChecked(normalized["hanging_punctuation"])
        self.auto_scale.setChecked(normalized["auto_scale"])
        self.max_horizontal_compression.setValue(normalized["max_horizontal_compression"])
        self.scale_x.setValue(normalized["scale_x"])
        self.scale_y.setValue(normalized["scale_y"])
        self.overflow_warning.setVisible(bool(style.get("text_overflow", False)))
        self.optical_center.setChecked(normalized["optical_center"])
        self._set_data(self.alignment, normalized["alignment"])
        self._set_data(self.vertical_alignment, normalized["vertical_alignment"])
        self.stroke.setValue(normalized["stroke_width"])
        self.margin.setValue(normalized["text_margin"])
        self.rotation.setValue(normalized["rotation"])
        self.vertical.setChecked(normalized["vertical_text"])
        self._set_color(self.text_color, normalized["text_color"])
        self._set_color(self.stroke_color, normalized["stroke_color"])
        self.preset.setCurrentText(preset_name if preset_name else "Personalizado")
        self._show_quick_preset(preset_name)
        self._update_font_preview(normalized["font_family"])
        self._loading = False
        self._sync_control_state()

    def clear_layer(self) -> None:
        self._layer_index = -1
        self.layer_label.setText("Selecciona una capa de texto")
        self._show_quick_preset("")
        self._set_editing_enabled(False)

    def set_font_role(self, alias: str) -> None:
        self._loading = True
        target = 0
        for index in range(1, self.font_role.count()):
            if str(self.font_role.itemData(index, Qt.UserRole + 1) or "") == alias:
                target = index; break
        self.font_role.setCurrentIndex(target)
        if target > 0:
            family = str(self.font_role.itemData(target) or "Segoe UI")
            self.font_source_label.setText(f"Rol {alias}  ·  {family}")
            self.font_source_label.setProperty("profile", True)
        else:
            self.font_source_label.setText(f"Fuente manual  ·  {self.family.currentText() or 'Segoe UI'}")
            self.font_source_label.setProperty("profile", False)
        self.font_source_label.style().unpolish(self.font_source_label)
        self.font_source_label.style().polish(self.font_source_label)
        self._loading = False

    @staticmethod
    def _set_data(combo: QComboBox, value, numeric: bool = False) -> None:
        index = combo.findData(int(value) if numeric else value)
        combo.setCurrentIndex(max(0, index))

    def values(self) -> dict:
        return TypographyManager.normalized({
            "font_family": self.family.currentText(), "font_size": self.size.value(),
            "font_file": self._font_file,
            "font_weight": int(self.weight.currentData() or 400),
            "italic": self.italic.isChecked(), "underline": self.underline.isChecked(),
            "strikeout": self.strikeout.isChecked(),
            "text_case": str(self.text_case.currentData() or "original"),
            "auto_fit": self.auto_fit.isChecked(), "balloon_fit": self.balloon_fit.isChecked(),
            "balloon_shape": str(self.balloon_shape.currentData() or "auto"),
            "balloon_padding": self.balloon_padding.value(),
            "language": str(self.language.currentData() or "auto"),
            "hyphenation": self.hyphenation.isChecked(),
            "orphan_control": self.orphan_control.isChecked(),
            "hanging_punctuation": self.hanging_punctuation.isChecked(),
            "auto_scale": self.auto_scale.isChecked(),
            "max_horizontal_compression": self.max_horizontal_compression.value(),
            "scale_x": self.scale_x.value(), "scale_y": self.scale_y.value(),
            "optical_center": self.optical_center.isChecked(), "line_spacing": self.spacing.value(),
            "alignment": self.alignment.currentData(), "vertical_alignment": self.vertical_alignment.currentData(),
            "stroke_width": self.stroke.value(), "stroke_color": self.stroke_color.text(),
            "text_color": self.text_color.text(), "rotation": self.rotation.value(),
            "vertical_text": self.vertical.isChecked(), "text_margin": self.margin.value(),
        })

    def _emit_style(self, *_args) -> None:
        if not self._loading and self._layer_index >= 0:
            if self.sender() is self.size:
                self.auto_fit.blockSignals(True)
                self.auto_fit.setChecked(False)
                self.auto_fit.blockSignals(False)
            if self.sender() in (self.family, self.family.lineEdit()) and not self._font_role_changing:
                self._font_file = ""
                self.font_role.blockSignals(True); self.font_role.setCurrentIndex(0); self.font_role.blockSignals(False)
                self._show_manual_font_source()
            self.preset.blockSignals(True)
            self.preset.setCurrentText("Personalizado")
            self.preset.blockSignals(False)
            self._show_quick_preset("")
            self._sync_control_state()
            self._update_font_preview(self.family.currentText())
            self.style_changed.emit(self.values())

    def _update_font_preview(self, family: str) -> None:
        family = str(family or "Segoe UI")
        font = QFont(family or "Segoe UI", 16)
        weight = int(self.weight.currentData() or 400) if hasattr(self, "weight") else 400
        font.setWeight(QFont.Weight(weight))
        font.setItalic(self.italic.isChecked() if hasattr(self, "italic") else False)
        font.setUnderline(self.underline.isChecked() if hasattr(self, "underline") else False)
        font.setStrikeOut(self.strikeout.isChecked() if hasattr(self, "strikeout") else False)
        self.font_preview.setFont(font)
        self.font_preview.setText(f"{family}\nAa 123  ·  漢字  ·  한글")
        self.font_preview.setMinimumHeight(72)

    def _update_font_preview_from_current(self, *_args) -> None:
        self._update_font_preview(self.family.currentText())

    def _show_manual_font_source(self) -> None:
        self.font_source_label.setText(f"Fuente manual  ·  {self.family.currentText() or 'Segoe UI'}")
        self.font_source_label.setProperty("profile", False)
        self.font_source_label.style().unpolish(self.font_source_label)
        self.font_source_label.style().polish(self.font_source_label)

    def _family_selected(self, *_args) -> None:
        """Commit one menu selection once, then redraw the selected layers."""
        family = select_font_family(self.family, self.family.currentText())
        self._font_file = ""
        self.font_role.blockSignals(True)
        self.font_role.setCurrentIndex(0)
        self.font_role.blockSignals(False)
        self._show_manual_font_source()
        self._update_font_preview(family)
        self._emit_style()

    def _preview_search_result(self, text: str) -> None:
        model = self.family.model()
        if isinstance(model, FontFamilyModel) and model.all_families:
            exact = next(
                (family for family in model.all_families if family.casefold() == text.casefold()), "",
            )
            matches = [
                family for family in model.all_families if text.casefold() in family.casefold()
            ]
            family = exact or (matches[0] if matches else model.selected)
            self._update_font_preview(family)
            self._emit_live_font_preview(family)

    def _preview_font_index(self, index) -> None:
        """Preview keyboard selection and mouse hover without waiting for a click."""
        model = self.family.model()
        row = index.row() if hasattr(index, "row") else int(index)
        if isinstance(model, FontFamilyModel) and 0 <= row < model.rowCount():
            family = str(model.data(model.index(row, 0), Qt.DisplayRole) or model.selected)
            self._update_font_preview(family)
            self._emit_live_font_preview(family)

    def _emit_live_font_preview(self, family: str) -> None:
        """Render the highlighted/search result directly in selected boxes.

        MainWindow already debounces partial text redraws, so browsing a large
        font menu remains responsive and only the selected text layers change.
        """
        if self._loading or self._layer_index < 0 or not family:
            return
        preview = self.values()
        preview["font_family"] = family
        preview["font_file"] = ""
        self.style_changed.emit(preview)

    def _family_editing_finished(self) -> None:
        family = commit_font_combo_text(self.family)
        self._update_font_preview(family)
        if not self._loading and self._layer_index >= 0 and not self._font_role_changing:
            self._font_file = ""
            self.font_role.blockSignals(True)
            self.font_role.setCurrentIndex(0)
            self.font_role.blockSignals(False)
            self._show_manual_font_source()
        self._emit_style()

    def _profile_changed(self, _index: int) -> None:
        if not self._loading:
            self.profile_selected.emit(
                str(self.project_type.currentData() or ""),
                str(self.profile.currentData() or ""),
            )

    def _project_type_changed(self, _index: int) -> None:
        if not self._loading:
            self.project_type_selected.emit(str(self.project_type.currentData() or ""))

    def _font_role_changed(self, index: int) -> None:
        if self._loading:
            return
        if index <= 0:
            self._font_file = ""
            self.font_source_label.setText(f"Fuente manual  ·  {self.family.currentText() or 'Segoe UI'}")
            self.font_source_label.setProperty("profile", False)
            self.font_source_label.style().unpolish(self.font_source_label)
            self.font_source_label.style().polish(self.font_source_label)
            self.font_role_selected.emit("", self.family.currentText(), "", {})
            return
        family = str(self.font_role.itemData(index) or "")
        alias = str(self.font_role.itemData(index, Qt.UserRole + 1) or "")
        file_name = str(self.font_role.itemData(index, Qt.UserRole + 2) or "")
        role_style = dict(self.font_role.itemData(index, Qt.UserRole + 3) or {})
        if family:
            self._font_file = file_name
            self._font_role_changing = True
            select_font_family(self.family, family)
            self._font_role_changing = False
            self._update_font_preview(family)
            self.font_source_label.setText(f"Rol {alias}  ·  {family}")
            self.font_source_label.setProperty("profile", True)
            self.font_source_label.style().unpolish(self.font_source_label)
            self.font_source_label.style().polish(self.font_source_label)
            normalized = TypographyManager.normalized(role_style)
            self._loading = True
            self._set_data(self.weight, str(normalized["font_weight"]), numeric=True)
            self.italic.setChecked(normalized["italic"])
            self.underline.setChecked(normalized["underline"])
            self.strikeout.setChecked(normalized["strikeout"])
            self._set_data(self.text_case, normalized["text_case"])
            self.size.setValue(normalized["font_size"])
            self._set_color(self.text_color, normalized["text_color"])
            self._loading = False
            self.font_role_selected.emit(alias, family, file_name, role_style)

    def _preset_selected(self, name: str) -> None:
        if self._loading or name not in getattr(self, "_presets", {}):
            return
        self.set_layer(max(0, self._layer_index), self._presets[name], name)
        self.style_changed.emit(self.values())

    def _save_preset(self) -> None:
        name = self.preset_name.text().strip()
        if name:
            self.preset_save_requested.emit(name, self.values())
            self.preset_name.clear()
