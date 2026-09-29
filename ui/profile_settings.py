from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget, QSizePolicy,
)

from core.translation_manager import DEFAULT_TRANSLATION_PROMPT, glossary_to_text, normalize_glossary

from ui.font_utils import (
    FontFamilyModel, commit_font_combo_text, configure_searchable_font_combo,
    font_families, invalidate_font_cache, select_font_family,
)
from ui.widgets.icons import icon


class ProfileSettingsWidget(QWidget):
    """Editor for the internal Type -> Project -> Font role hierarchy."""

    def __init__(
        self,
        manager,
        active_type: str,
        active_project: str,
        locked: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.manager = manager
        self.active_type = active_type
        self.active_project = active_project
        self.locked = locked
        self._previous_alias = ""
        self._font_commit_in_progress = False
        self._updating_translation = False
        self._translation_save_timer = QTimer(self)
        self._translation_save_timer.setSingleShot(True)
        self._translation_save_timer.setInterval(550)
        self._translation_save_timer.timeout.connect(self._save_translation_profile)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        intro = QLabel(
            "Los perfiles se guardan automáticamente dentro del programa. "
            "Sigue el orden: crea un tipo, crea un proyecto y asigna sus fuentes."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        root.addWidget(intro)

        self.selection_summary = QLabel("Selecciona una biblioteca y un proyecto")
        self.selection_summary.setObjectName("ProfileSummary")
        self.selection_summary.setWordWrap(True)
        root.addWidget(self.selection_summary)

        hierarchy = QGroupBox("Biblioteca de perfiles")
        hierarchy.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        hierarchy_layout = QVBoxLayout(hierarchy)
        form = QFormLayout()
        self.project_type = QComboBox()
        self.project_type.setPlaceholderText("Selecciona un tipo")
        self.project_type.currentTextChanged.connect(self._type_selected)
        self.project = QComboBox()
        self.project.setPlaceholderText("Selecciona un proyecto")
        self.project.currentTextChanged.connect(self._project_selected)
        form.addRow("1 · Biblioteca", self.project_type)
        type_actions = QHBoxLayout()
        self.create_type_button = QPushButton("+ Crear")
        self.create_type_button.setObjectName("Primary")
        self.create_type_button.setToolTip("Crear una nueva biblioteca, por ejemplo Manhwas")
        self.create_type_button.clicked.connect(self._create_type)
        self.rename_type_button = QPushButton("Renombrar")
        self.rename_type_button.clicked.connect(self._rename_type)
        self.delete_type_button = QPushButton("Eliminar")
        self.delete_type_button.setObjectName("DangerButton")
        self.delete_type_button.clicked.connect(self._delete_type)
        type_actions.addWidget(self.create_type_button)
        type_actions.addWidget(self.rename_type_button)
        type_actions.addWidget(self.delete_type_button)
        form.addRow("", type_actions)
        form.addRow("2 · Proyecto de traducción", self.project)
        project_actions = QHBoxLayout()
        self.create_project_button = QPushButton("+ Crear")
        self.create_project_button.setObjectName("Primary")
        self.create_project_button.setToolTip("Crear un proyecto dentro de la biblioteca seleccionada")
        self.create_project_button.clicked.connect(self._create_project)
        self.rename_project_button = QPushButton("Renombrar")
        self.rename_project_button.clicked.connect(self._rename_project)
        self.delete_project_button = QPushButton("Eliminar")
        self.delete_project_button.setObjectName("DangerButton")
        self.delete_project_button.clicked.connect(self._delete_project)
        project_actions.addWidget(self.create_project_button)
        project_actions.addWidget(self.rename_project_button)
        project_actions.addWidget(self.delete_project_button)
        form.addRow("", project_actions)
        hierarchy_layout.addLayout(form)
        root.addWidget(hierarchy)

        fonts = QGroupBox("Fuentes y estilos del proyecto")
        fonts.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        fonts_layout = QVBoxLayout(fonts)
        fonts_layout.setSpacing(10)

        # Quick preset buttons for instant role creation
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        preset_label = QLabel("Plantillas:")
        preset_label.setObjectName("Muted")
        preset_row.addWidget(preset_label)
        for label, key in [
            ("💬 Diálogo", "dialogue"),
            ("⚡ Grito", "shout"),
            ("💭 Pensamiento", "thought"),
            ("📜 Narrador", "narration"),
            ("💥 SFX", "sfx"),
        ]:
            p_btn = QPushButton(label)
            p_btn.setToolTip(f"Rellenar campos con estilo para {label}")
            p_btn.clicked.connect(lambda _, k=key: self._apply_role_preset(k))
            preset_row.addWidget(p_btn)
        preset_row.addStretch()
        fonts_layout.addLayout(preset_row)

        # Role list filter
        self.role_search = QLineEdit()
        self.role_search.setPlaceholderText("Buscar rol o fuente en este proyecto…")
        self.role_search.addAction(icon("search", "#8FA6B8", 15), QLineEdit.LeadingPosition)
        self.role_search.setClearButtonEnabled(True)
        self.role_search.textChanged.connect(lambda *_: self._refresh_fonts(self._current_type(), self._current_project()))
        fonts_layout.addWidget(self.role_search)

        self.fonts = QListWidget()
        self.fonts.setObjectName("ProfileFontList")
        self.fonts.currentRowChanged.connect(self._font_selected)
        self.fonts.setMinimumHeight(85)
        self.fonts.setMaximumHeight(130)
        fonts_layout.addWidget(self.fonts)

        font_form = QFormLayout()
        font_form.setSpacing(8)
        self.alias = QLineEdit()
        self.alias.setPlaceholderText("Nombre del rol (ej: Diálogo, Gritos, Narrador…)")
        self.alias.textEdited.connect(self._assignment_edited)
        self.family = QComboBox()
        self.family.setEditable(True)
        self.family.setInsertPolicy(QComboBox.NoInsert)
        self.family.addItem("Segoe UI")
        self.family.setCurrentText("Segoe UI")
        self.family.lineEdit().setPlaceholderText("Buscar fuente instalada o del proyecto…")
        self.font_search = self.family.lineEdit()
        self.font_search.setClearButtonEnabled(True)
        self.font_search.returnPressed.connect(self._choose_first_filtered_font)
        self._font_choices_loaded = False
        self.family.activated.connect(self._family_selected)
        self.family.lineEdit().editingFinished.connect(self._family_editing_finished)
        self.preview = QLabel("Diálogo · GRITOS · Aa 123")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(48)
        self.font_weight = QComboBox()
        self.font_weight.setProperty("kuro_i18n_choices", True)
        for label, weight in (
            ("Normal (400)", 400), ("Seminegrita (600)", 600), ("Negrita / Bold (700)", 700),
            ("Extra negrita (800)", 800), ("Black (900)", 900),
        ):
            self.font_weight.addItem(label, weight)
        emphasis = QWidget()
        emphasis_layout = QHBoxLayout(emphasis)
        emphasis_layout.setContentsMargins(0, 0, 0, 0)
        self.font_italic = QCheckBox("Cursiva")
        self.font_underline = QCheckBox("Subrayado")
        self.font_strikeout = QCheckBox("Tachado")
        emphasis_layout.addWidget(self.font_italic)
        emphasis_layout.addWidget(self.font_underline)
        emphasis_layout.addWidget(self.font_strikeout)
        emphasis_layout.addStretch()
        self.font_case = QComboBox()
        self.font_case.setProperty("kuro_i18n_choices", True)
        self.font_case.addItem("Como fue escrito (Original)", "original")
        self.font_case.addItem("TODO MAYÚSCULAS", "upper")
        self.font_case.addItem("todo minúsculas", "lower")
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 300)
        self.font_size.setValue(36)
        self.font_size.setSuffix(" px")
        color_control = QWidget()
        color_layout = QHBoxLayout(color_control)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        self.font_color = QLineEdit("#111111")
        self.font_color.setMaxLength(9)
        self.font_color.setPlaceholderText("#111111")
        self.font_color_button = QPushButton("Elegir…")
        self.font_color_button.clicked.connect(self._pick_font_color)
        color_layout.addWidget(self.font_color, 1)
        color_layout.addWidget(self.font_color_button)
        font_form.addRow("Nombre del rol", self.alias)
        font_form.addRow("Fuente tipográfica", self.family)
        font_form.addRow("Tamaño base", self.font_size)
        font_form.addRow("Color", color_control)
        font_form.addRow("Grosor / Peso", self.font_weight)
        font_form.addRow("Énfasis", emphasis)
        font_form.addRow("Mayúsculas", self.font_case)
        font_form.addRow("Vista previa", self.preview)
        fonts_layout.addLayout(font_form)

        for control in (
            self.font_weight, self.font_italic, self.font_underline,
            self.font_strikeout, self.font_case, self.font_size, self.font_color,
        ):
            signal = (
                getattr(control, "currentIndexChanged", None)
                or getattr(control, "toggled", None)
                or getattr(control, "valueChanged", None)
                or getattr(control, "textEdited", None)
            )
            signal.connect(self._style_option_changed)

        self.font_status = QLabel("Completa el nombre de uso y selecciona una fuente.")
        self.font_status.setObjectName("FontAssignmentStatus")
        self.font_status.setWordWrap(True)
        fonts_layout.addWidget(self.font_status)

        buttons = QHBoxLayout()
        self.new_font_button = QPushButton("Nueva")
        self.new_font_button.setToolTip("Vaciar la selección y crear una asignación independiente")
        self.new_font_button.clicked.connect(self._begin_new_font)
        self.save_font_button = QPushButton("+ Agregar asignación")
        self.save_font_button.setObjectName("Primary")
        self.save_font_button.clicked.connect(self._save_font)
        self.import_font_button = QPushButton("Importar TTF/OTF…")
        self.import_font_button.setToolTip("Copiar una fuente externa dentro de este proyecto")
        self.import_font_button.clicked.connect(self._import_font)
        self.remove_font_button = QPushButton("Quitar")
        self.remove_font_button.setObjectName("DangerButton")
        self.remove_font_button.clicked.connect(self._remove_font)
        buttons.addWidget(self.new_font_button)
        buttons.addWidget(self.save_font_button, 1)
        buttons.addWidget(self.import_font_button)
        buttons.addWidget(self.remove_font_button)
        fonts_layout.addLayout(buttons)
        root.addWidget(fonts)

        translation = QGroupBox("Glosario y contexto de traducción del proyecto")
        translation.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        translation_layout = QVBoxLayout(translation)
        translation_hint = QLabel(
            "Se usa automáticamente al traducir. Puedes pegar JSON, CSV, tablas Markdown, "
            "original=traducción, original: traducción, flechas, Tab o texto libre. "
            "El sistema lo normaliza al guardar y la API mantiene esos términos."
        )
        translation_hint.setWordWrap(True); translation_hint.setObjectName("Muted")
        translation_layout.addWidget(translation_hint)
        translation_layout.addWidget(QLabel("PROMPT PERMANENTE"))
        self.translation_prompt = QPlainTextEdit()
        self.translation_prompt.setPlaceholderText(DEFAULT_TRANSLATION_PROMPT)
        self.translation_prompt.setMaximumHeight(125)
        self.translation_prompt.textChanged.connect(self._translation_edited)
        translation_layout.addWidget(self.translation_prompt)
        translation_layout.addWidget(QLabel("GLOSARIO · ORIGINAL → TRADUCCIÓN → CATEGORÍA → NOTA"))
        self.glossary_editor = QPlainTextEdit()
        self.glossary_editor.setPlaceholderText(
            "林枫 = Lin Feng\n"
            "天云城 → Ciudad Tianyun\n"
            "Kim Dokja, Kim Dokja, personaje, protagonista"
        )
        self.glossary_editor.setMaximumHeight(165)
        self.glossary_editor.textChanged.connect(self._translation_edited)
        translation_layout.addWidget(self.glossary_editor)
        glossary_actions = QHBoxLayout()
        self.save_translation_button = QPushButton("Guardar contexto")
        self.save_translation_button.setObjectName("Primary")
        self.save_translation_button.clicked.connect(self._save_translation_profile)
        copy_glossary = QPushButton("Copiar glosario")
        copy_glossary.clicked.connect(self._copy_glossary)
        paste_glossary = QPushButton("Pegar y combinar")
        paste_glossary.clicked.connect(self._paste_glossary)
        reset_prompt = QPushButton("Restaurar prompt")
        reset_prompt.clicked.connect(self._reset_translation_prompt)
        glossary_actions.addWidget(self.save_translation_button)
        glossary_actions.addWidget(copy_glossary)
        glossary_actions.addWidget(paste_glossary)
        glossary_actions.addWidget(reset_prompt)
        translation_layout.addLayout(glossary_actions)
        self.translation_status = QLabel("Selecciona un proyecto para editar su contexto.")
        self.translation_status.setObjectName("FontAssignmentStatus")
        self.translation_status.setWordWrap(True)
        translation_layout.addWidget(self.translation_status)
        root.addWidget(translation)
        root.addStretch()

        self._refresh_types()
        # Attaching a combo with thousands of styled rows while QScrollArea is
        # calculating its child size blocks the Windows event loop. Populate
        # the virtual model once the dialog has already been attached.
        QTimer.singleShot(0, self._load_font_choices)

    def _load_font_choices(self) -> None:
        if self._font_choices_loaded:
            return
        selected = self.family.currentText() or "Segoe UI"
        configure_searchable_font_combo(self.family, selected)
        self.family.lineEdit().textEdited.connect(self._preview_search_result)
        self.family.lineEdit().textEdited.connect(self._font_query_changed)
        self.family.highlighted.connect(self._preview_font_index)
        self.family.view().entered.connect(self._preview_font_index)
        self._font_choices_loaded = True

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, "Perfiles de fuentes", message)

    def _refresh_types(self, select: str = "") -> None:
        target = select or self.active_type
        self.project_type.blockSignals(True)
        self.project_type.clear()
        self.project_type.addItems(self.manager.project_types())
        if target and self.project_type.findText(target) >= 0:
            self.project_type.setCurrentText(target)
        elif self.project_type.count():
            self.project_type.setCurrentIndex(0)
        self.project_type.blockSignals(False)
        self._refresh_projects(self.project_type.currentText())
        self._update_hierarchy_actions()

    def _refresh_projects(self, project_type: str, select: str = "") -> None:
        target = select or (self.active_project if project_type == self.active_type else "")
        self.project.blockSignals(True)
        self.project.clear()
        self.project.addItems(self.manager.projects(project_type) if project_type else [])
        if target and self.project.findText(target) >= 0:
            self.project.setCurrentText(target)
        elif self.project.count():
            self.project.setCurrentIndex(0)
        self.project.blockSignals(False)
        self.create_project_button.setEnabled(bool(project_type))
        self._refresh_fonts(project_type, self.project.currentText())
        if project_type and self.project.currentText():
            self.selection_summary.setText(
                f"{project_type}  ›  {self.project.currentText()}\nLas fuentes siguientes pertenecen solamente a este proyecto."
            )
            self.selection_summary.setProperty("active", True)
        else:
            self.selection_summary.setText("Selecciona una biblioteca y un proyecto")
            self.selection_summary.setProperty("active", False)
        self.selection_summary.style().unpolish(self.selection_summary)
        self.selection_summary.style().polish(self.selection_summary)
        self._update_hierarchy_actions()

    def _create_type(self) -> None:
        name, accepted = QInputDialog.getText(self, "Nuevo tipo de proyecto", "Nombre del tipo (ejemplo: Manhwas):")
        if not accepted:
            return
        try:
            project_type = self.manager.create_type(name)
        except ValueError as error:
            self._error(str(error))
            return
        if not self.locked:
            self.active_type = project_type
            self.active_project = ""
        self._refresh_types(project_type)

    def _create_project(self) -> None:
        project_type = self._current_type()
        if not project_type:
            self._error("Crea o selecciona primero un tipo de proyecto.")
            return
        name, accepted = QInputDialog.getText(self, "Nuevo proyecto", "Nombre del proyecto (ejemplo: Valiente):")
        if not accepted:
            return
        try:
            project = self.manager.create_project(project_type, name)
        except ValueError as error:
            self._error(str(error))
            return
        if not self.locked:
            self.active_type = project_type
            self.active_project = project
        self._refresh_projects(project_type, project)

    def _rename_type(self) -> None:
        current = self._current_type()
        if not current:
            return
        name, accepted = QInputDialog.getText(
            self, "Renombrar tipo", "Nuevo nombre del tipo de proyecto:", text=current,
        )
        if not accepted:
            return
        try:
            renamed = self.manager.rename_type(current, name)
        except ValueError as error:
            self._error(str(error))
            return
        if self.active_type == current:
            self.active_type = renamed
        self._refresh_types(renamed)

    def _delete_type(self) -> None:
        project_type = self._current_type()
        if not project_type:
            return
        count = len(self.manager.projects(project_type))
        answer = QMessageBox.question(
            self,
            "Eliminar tipo de proyecto",
            f"¿Eliminar '{project_type}' y sus {count} proyecto(s)?\n\n"
            "Se eliminarán también sus asignaciones y archivos de fuentes importados.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.manager.delete_type(project_type)
        except ValueError as error:
            self._error(str(error))
            return
        if self.active_type == project_type:
            self.active_type = ""
            self.active_project = ""
        self._refresh_types()

    def _rename_project(self) -> None:
        project_type, current = self._current_type(), self._current_project()
        if not project_type or not current:
            return
        name, accepted = QInputDialog.getText(
            self, "Renombrar proyecto", "Nuevo nombre del proyecto:", text=current,
        )
        if not accepted:
            return
        try:
            renamed = self.manager.rename_project(project_type, current, name)
        except ValueError as error:
            self._error(str(error))
            return
        if self.active_type == project_type and self.active_project == current:
            self.active_project = renamed
        self._refresh_projects(project_type, renamed)

    def _delete_project(self) -> None:
        project_type, project = self._current_type(), self._current_project()
        if not project_type or not project:
            return
        answer = QMessageBox.question(
            self,
            "Eliminar proyecto",
            f"¿Eliminar el perfil '{project}'?\n\n"
            "Se eliminarán sus asignaciones y archivos de fuentes importados.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.manager.delete_project(project_type, project)
        except ValueError as error:
            self._error(str(error))
            return
        if self.active_type == project_type and self.active_project == project:
            self.active_project = ""
        self._refresh_projects(project_type)

    def _type_selected(self, project_type: str) -> None:
        self._refresh_projects(project_type)

    def _project_selected(self, project: str) -> None:
        self._refresh_fonts(self._current_type(), project)
        if self._current_type() and project:
            self.selection_summary.setText(
                f"{self._current_type()}  ›  {project}\nLas fuentes siguientes pertenecen solamente a este proyecto."
            )
            self.selection_summary.setProperty("active", True)
        else:
            self.selection_summary.setText("Selecciona una biblioteca y un proyecto")
            self.selection_summary.setProperty("active", False)
        self.selection_summary.style().unpolish(self.selection_summary)
        self.selection_summary.style().polish(self.selection_summary)
        self._update_hierarchy_actions()

    def _update_hierarchy_actions(self) -> None:
        has_type = bool(self._current_type())
        has_project = bool(self._current_project())
        self.rename_type_button.setEnabled(has_type)
        self.delete_type_button.setEnabled(has_type)
        self.create_project_button.setEnabled(has_type)
        self.rename_project_button.setEnabled(has_type and has_project)
        self.delete_project_button.setEnabled(has_type and has_project)
        self.save_font_button.setEnabled(has_project)
        self.new_font_button.setEnabled(has_project)
        self.import_font_button.setEnabled(has_project)
        self.remove_font_button.setEnabled(has_project and bool(self._previous_alias))

    def _apply_role_preset(self, role_key: str) -> None:
        presets = {
            "dialogue": {"alias": "Diálogo", "size": 36, "weight": 400, "case": "original", "italic": False},
            "shout": {"alias": "Gritos", "size": 46, "weight": 800, "case": "upper", "italic": False},
            "thought": {"alias": "Pensamiento", "size": 32, "weight": 400, "case": "original", "italic": True},
            "narration": {"alias": "Narrador", "size": 34, "weight": 600, "case": "original", "italic": False},
            "sfx": {"alias": "SFX", "size": 52, "weight": 900, "case": "upper", "italic": False},
        }
        data = presets.get(role_key)
        if not data:
            return
        self.alias.setText(data["alias"])
        self.font_size.setValue(data["size"])
        idx = self.font_weight.findData(data["weight"])
        if idx >= 0:
            self.font_weight.setCurrentIndex(idx)
        case_idx = self.font_case.findData(data["case"])
        if case_idx >= 0:
            self.font_case.setCurrentIndex(case_idx)
        self.font_italic.setChecked(data["italic"])
        self._assignment_edited()
        self._style_option_changed()
        self.font_status.setText(f"Plantilla '{data['alias']}' aplicada. Selecciona la fuente deseada y pulsa Guardar.")

    def _refresh_fonts(self, project_type: str, project: str, select: str = "") -> None:
        self.fonts.blockSignals(True)
        self.fonts.clear()
        query = self.role_search.text().strip().casefold() if hasattr(self, "role_search") else ""
        for alias, entry in self.manager.font_entries(project_type, project).items():
            if query and query not in alias.casefold() and query not in str(entry.get("family", "")).casefold():
                continue
            style = dict(entry.get("style", {}))
            details = []
            if int(style.get("font_weight", 400)) >= 700:
                details.append("Negrita")
            if style.get("italic"):
                details.append("Cursiva")
            if style.get("text_case") == "upper":
                details.append("MAYÚSCULAS")
            details.append(f"{int(style.get('font_size', 36))} px")
            details.append(str(style.get("text_color", "#111111")).upper())
            suffix = f"  ·  {' / '.join(details)}" if details else ""
            self.fonts.addItem(f"{alias}  ·  {entry['family']}{suffix}")
            item = self.fonts.item(self.fonts.count() - 1)
            item.setData(Qt.UserRole, alias)
            item.setData(Qt.UserRole + 1, entry)
            item.setFont(QFont(entry["family"], 10))
        self.fonts.blockSignals(False)
        for index in range(self.fonts.count()):
            if self.fonts.item(index).data(Qt.UserRole) == select:
                self.fonts.setCurrentRow(index)
                return
        self.fonts.setCurrentRow(-1)
        self._reset_font_editor()
        self.font_status.setText(
            "Escribe un nombre de uso, por ejemplo Diálogo, Gritos o Narrador."
            if project_type and project else "Selecciona primero una biblioteca y un proyecto."
        )
        self.font_status.setProperty("saved", False)
        self._load_translation_profile(project_type, project)

    def _load_translation_profile(self, project_type: str, project: str) -> None:
        self._translation_save_timer.stop()
        self._updating_translation = True
        try:
            if project_type and project and self.manager.has_project(project_type, project):
                profile = self.manager.translation_profile(project_type, project)
                self.translation_prompt.setPlainText(profile["prompt"])
                self.glossary_editor.setPlainText(glossary_to_text(profile["glossary"]))
                self.translation_status.setText(
                    f"{len(profile['glossary'])} término(s) · se aplicarán automáticamente a {project}."
                )
                enabled = True
            else:
                self.translation_prompt.setPlainText(DEFAULT_TRANSLATION_PROMPT)
                self.glossary_editor.clear()
                self.translation_status.setText("Selecciona un proyecto para editar su contexto.")
                enabled = False
            self.translation_prompt.setEnabled(enabled)
            self.glossary_editor.setEnabled(enabled)
            self.save_translation_button.setEnabled(enabled)
        finally:
            self._updating_translation = False

    def _translation_edited(self) -> None:
        if self._updating_translation:
            return
        self.translation_status.setText("Cambios pendientes · se guardarán automáticamente.")
        self._translation_save_timer.start()

    def _save_translation_profile(self) -> None:
        project_type, project = self._current_type(), self._current_project()
        if not project_type or not project:
            return
        try:
            glossary = normalize_glossary(self.glossary_editor.toPlainText())
            self.manager.set_translation_profile(
                project_type, project, self.translation_prompt.toPlainText(), glossary,
            )
        except ValueError as error:
            self._error(str(error)); return
        self._updating_translation = True
        self.glossary_editor.setPlainText(glossary_to_text(glossary))
        self._updating_translation = False
        self.translation_status.setText(f"Guardado · {len(glossary)} término(s) activos para la API.")

    def _copy_glossary(self) -> None:
        QApplication.clipboard().setText(self.glossary_editor.toPlainText())
        self.translation_status.setText("Glosario copiado. Puedes pegarlo en otro proyecto o editor.")

    def _paste_glossary(self) -> None:
        incoming = normalize_glossary(QApplication.clipboard().text())
        combined = normalize_glossary([
            *normalize_glossary(self.glossary_editor.toPlainText()), *incoming,
        ])
        self.glossary_editor.setPlainText(glossary_to_text(combined))
        self._save_translation_profile()

    def _reset_translation_prompt(self) -> None:
        self.translation_prompt.setPlainText(DEFAULT_TRANSLATION_PROMPT)
        self._save_translation_profile()

    def _font_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.fonts.item(row)
        alias = str(item.data(Qt.UserRole))
        entry = dict(item.data(Qt.UserRole + 1) or {})
        self._previous_alias = alias
        self.alias.setText(alias)
        select_font_family(self.family, str(entry.get("family", "Segoe UI")))
        self.font_search.blockSignals(True)
        self.font_search.setText(self.family.currentText())
        self.font_search.blockSignals(False)
        self._set_font_style(entry.get("style", {}))
        self._update_preview(self.family.currentText())
        self.save_font_button.setText("Guardar cambios")
        self.remove_font_button.setEnabled(True)
        self.font_status.setText(f"Editando el rol {alias}. Guarda para conservar los cambios.")
        self.font_status.setProperty("saved", False)

    def _reset_font_editor(self) -> None:
        self._previous_alias = ""
        self.alias.clear()
        select_font_family(self.family, "Segoe UI")
        self._set_font_style({})
        self._update_preview(self.family.currentText())
        self.save_font_button.setText("+ Agregar asignación")
        self.remove_font_button.setEnabled(False)

    def _begin_new_font(self) -> None:
        self.fonts.blockSignals(True)
        self.fonts.setCurrentRow(-1)
        self.fonts.clearSelection()
        self.fonts.blockSignals(False)
        self._reset_font_editor()
        self.alias.setFocus(Qt.OtherFocusReason)
        self.font_status.setText("Nueva asignación: escribe un nombre y elige su fuente y estilo.")
        self.font_status.setProperty("saved", False)

    def _font_style(self) -> dict:
        color = QColor(self.font_color.text().strip())
        return {
            "font_size": self.font_size.value(),
            "text_color": color.name().upper() if color.isValid() else "#111111",
            "font_weight": int(self.font_weight.currentData() or 400),
            "italic": self.font_italic.isChecked(),
            "underline": self.font_underline.isChecked(),
            "strikeout": self.font_strikeout.isChecked(),
            "text_case": str(self.font_case.currentData() or "original"),
        }

    def _set_font_style(self, style: dict) -> None:
        style = dict(style or {})
        controls = (
            self.font_weight, self.font_italic, self.font_underline,
            self.font_strikeout, self.font_case, self.font_size, self.font_color,
        )
        previous = [control.blockSignals(True) for control in controls]
        index = self.font_weight.findData(int(style.get("font_weight", 400)))
        self.font_weight.setCurrentIndex(max(0, index))
        self.font_italic.setChecked(bool(style.get("italic", False)))
        self.font_underline.setChecked(bool(style.get("underline", False)))
        self.font_strikeout.setChecked(bool(style.get("strikeout", False)))
        case_index = self.font_case.findData(str(style.get("text_case", "original")))
        self.font_case.setCurrentIndex(max(0, case_index))
        self.font_size.setValue(max(6, min(300, int(style.get("font_size", 36)))))
        color = QColor(str(style.get("text_color", "#111111")))
        self.font_color.setText(color.name().upper() if color.isValid() else "#111111")
        self._update_font_color_button()
        for control, blocked in zip(controls, previous):
            control.blockSignals(blocked)

    def _current_type(self) -> str:
        return self.project_type.currentText().strip()

    def _current_project(self) -> str:
        return self.project.currentText().strip()

    def _save_font(self) -> None:
        project_type, project = self._current_type(), self._current_project()
        if not project_type or not project:
            self._error("Selecciona un tipo y un proyecto antes de asignar fuentes.")
            return
        family = commit_font_combo_text(self.family).strip()
        if family.casefold() not in {name.casefold() for name in font_families()}:
            self._error("Selecciona una fuente válida de los resultados del menú.")
            return
        current_file = ""
        if self.fonts.currentItem():
            current_file = dict(self.fonts.currentItem().data(Qt.UserRole + 1) or {}).get("file", "")
        alias = self.alias.text().strip()
        if not self._previous_alias:
            duplicate = next(
                (name for name in self.manager.font_entries(project_type, project) if name.casefold() == alias.casefold()),
                "",
            )
            if duplicate:
                self._error(f"Ya existe la asignación '{duplicate}'. Selecciónala en la lista para editarla.")
                return
        try:
            self.manager.set_font(
                project_type, project, alias, family, current_file, self._previous_alias,
                self._font_style(),
            )
        except ValueError as error:
            self._error(str(error))
            return
        self._refresh_fonts(project_type, project)
        self.font_status.setText(f"Asignación guardada: {alias}  ·  {family}. Lista para agregar otra.")
        self.font_status.setProperty("saved", True)
        self.font_status.style().unpolish(self.font_status)
        self.font_status.style().polish(self.font_status)

    def _import_font(self) -> None:
        project_type, project = self._current_type(), self._current_project()
        if not project_type or not project:
            self._error("Selecciona un tipo y un proyecto antes de importar una fuente.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Importar fuente", "", "Fuentes (*.ttf *.otf *.ttc)")
        if not filename:
            return
        try:
            relative = self.manager.import_font(project_type, project, Path(filename))
            full_path = self.manager.font_file(project_type, project, relative)
            font_id = QFontDatabase.addApplicationFont(str(full_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if not families:
                raise ValueError("Qt no pudo reconocer la familia de esta fuente.")
            selected_family = families[0]
            invalidate_font_cache()
            configure_searchable_font_combo(self.family, selected_family)
            self._font_choices_loaded = True
            alias, accepted = QInputDialog.getText(
                self, "Nombre de uso", "Nombre que aparecerá en Texto:", text=Path(filename).stem,
            )
            if accepted and alias.strip():
                self.manager.set_font(
                    project_type, project, alias, selected_family, relative,
                    style=self._font_style(),
                )
                self._refresh_fonts(project_type, project)
        except ValueError as error:
            self._error(str(error))

    def _remove_font(self) -> None:
        project_type, project, alias = self._current_type(), self._current_project(), self._previous_alias
        if project_type and project and alias:
            self.manager.remove_font(project_type, project, alias)
            self._refresh_fonts(project_type, project)

    def _update_preview(self, family: str) -> None:
        family = str(family or "Segoe UI")
        style = self._font_style()
        font = QFont(family, max(10, min(28, int(style["font_size"]))))
        font.setWeight(QFont.Weight(int(style["font_weight"])))
        font.setItalic(bool(style["italic"]))
        font.setUnderline(bool(style["underline"]))
        font.setStrikeOut(bool(style["strikeout"]))
        self.preview.setFont(font)
        self.preview.setStyleSheet(f"color: {style['text_color']};")
        sample = f"{family}\nAa 123  ·  DIÁLOGO  ·  漢字  ·  한글"
        if style["text_case"] == "upper":
            sample = sample.upper()
        elif style["text_case"] == "lower":
            sample = sample.lower()
        self.preview.setText(sample)
        self.preview.setMinimumHeight(62)

    def _pick_font_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.font_color.text()), self, "Color de la fuente")
        if not color.isValid():
            return
        self.font_color.setText(color.name().upper())
        self._update_font_color_button()
        self._style_option_changed()

    def _update_font_color_button(self) -> None:
        color = QColor(self.font_color.text())
        if not color.isValid():
            color = QColor("#111111")
        swatch = QPixmap(16, 16)
        swatch.fill(color)
        self.font_color_button.setIcon(QIcon(swatch))

    def _style_option_changed(self, *_args) -> None:
        self._update_preview(self.family.currentText())
        self._assignment_edited()

    def _update_preview_from_current(self, *_args) -> None:
        self._update_preview(self.family.currentText())

    def _family_selected(self, *_args) -> None:
        family = select_font_family(self.family, self.family.currentText())
        self.font_search.blockSignals(True)
        self.font_search.setText(family)
        self.font_search.blockSignals(False)
        self._update_preview(family)
        self.font_status.setText(f"Fuente seleccionada: {family}. Pulsa Guardar para asignarla.")
        self.font_status.setProperty("saved", False)
        self.font_status.style().unpolish(self.font_status)
        self.font_status.style().polish(self.font_status)

    def _assignment_edited(self, *_args) -> None:
        self.font_status.setText("Cambios sin guardar. Revisa el nombre y pulsa Guardar.")
        self.font_status.setProperty("saved", False)
        self.font_status.style().unpolish(self.font_status)
        self.font_status.style().polish(self.font_status)

    def _font_query_changed(self, text: str) -> None:
        model = self.family.model()
        count = model.total_count if isinstance(model, FontFamilyModel) else 0
        self.font_status.setText(
            f"{count} resultado(s) en tiempo real. Pulsa Enter o elige uno del menú."
            if count else "No se encontró ninguna fuente con ese nombre."
        )
        self.font_status.setProperty("saved", False)

    def _choose_first_filtered_font(self) -> None:
        if self._font_commit_in_progress:
            return
        model = self.family.model()
        if not isinstance(model, FontFamilyModel) or model.rowCount() <= 0:
            self.font_status.setText("No se encontró ninguna fuente con ese nombre.")
            return
        # Capture the filtered value before restoring the full catalogue.
        # Otherwise the Return key can be delivered to the rebuilt popup and
        # select row zero of the unfiltered model (usually Arial/Segoe UI).
        chosen = str(model.data(model.index(0, 0), Qt.DisplayRole) or "")
        self._font_commit_in_progress = True
        QTimer.singleShot(0, lambda: setattr(self, "_font_commit_in_progress", False))
        self.family.hidePopup()
        select_font_family(self.family, chosen)
        self._update_preview(chosen)
        self.font_status.setText(f"Fuente seleccionada: {chosen}. Pulsa Guardar para asignarla.")
        self.font_status.setProperty("saved", False)

    def _preview_search_result(self, text: str) -> None:
        model = self.family.model()
        if isinstance(model, FontFamilyModel) and model.all_families:
            exact = next(
                (family for family in model.all_families if family.casefold() == text.casefold()), "",
            )
            matches = [
                family for family in model.all_families if text.casefold() in family.casefold()
            ]
            self._update_preview(exact or (matches[0] if matches else model.selected))

    def _preview_font_index(self, index) -> None:
        model = self.family.model()
        row = index.row() if hasattr(index, "row") else int(index)
        if isinstance(model, FontFamilyModel) and 0 <= row < model.rowCount():
            self._update_preview(str(model.data(model.index(row, 0), Qt.DisplayRole) or model.selected))

    def _family_editing_finished(self) -> None:
        family = commit_font_combo_text(self.family)
        self.font_search.blockSignals(True)
        self.font_search.setText(family)
        self.font_search.blockSignals(False)
        self._update_preview(family)
        self.font_status.setText(f"Fuente seleccionada: {family}. Pulsa Guardar para asignarla.")
        self.font_status.setProperty("saved", False)
        self.font_status.style().unpolish(self.font_status)
        self.font_status.style().polish(self.font_status)

    def values(self) -> tuple[str, str]:
        if self.locked:
            return self.active_type, self.active_project
        return self._current_type(), self._current_project()
