from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from core.font_profile_manager import FontProfileManager
from ui.widgets.icons import icon


ALL_TYPES = "__all__"


class ProjectSelectionDialog(QDialog):
    """Searchable project browser grouped by Manhua/Manhwa/Manga/etc."""

    def __init__(
        self,
        manager: FontProfileManager,
        current_type: str = "",
        current_project: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.current_type = current_type
        self.current_project = current_project
        self.setWindowTitle("Seleccionar proyecto de trabajo")
        self.setObjectName("ProjectSelectionDialog")
        self.resize(720, 540)
        self.setMinimumSize(620, 450)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        title = QLabel("Proyecto para este capítulo")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        subtitle = QLabel(
            "Organiza tus perfiles por Manhua, Manhwa, Manga u otra categoría. "
            "La selección quedará fija mientras editas el capítulo."
        )
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        browser = QHBoxLayout()
        browser.setSpacing(12)
        category_frame = QFrame()
        category_frame.setObjectName("ProjectBrowserPane")
        category_layout = QVBoxLayout(category_frame)
        category_layout.setContentsMargins(10, 10, 10, 10)
        category_layout.setSpacing(7)
        category_title = QLabel("TIPO DE PROYECTO")
        category_title.setObjectName("SectionTitle")
        category_layout.addWidget(category_title)
        self.categories = QListWidget()
        self.categories.setObjectName("ProjectTypeList")
        self.categories.setMinimumWidth(180)
        self.categories.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        category_layout.addWidget(self.categories, 1)
        browser.addWidget(category_frame, 0)

        project_frame = QFrame()
        project_frame.setObjectName("ProjectBrowserPane")
        project_layout = QVBoxLayout(project_frame)
        project_layout.setContentsMargins(10, 10, 10, 10)
        project_layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("ProjectSearch")
        self.search.setPlaceholderText("Buscar proyecto por nombre…")
        self.search.addAction(icon("search", "#8FA6B8", 15), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        project_layout.addWidget(self.search)
        self.projects = QListWidget()
        self.projects.setObjectName("ProjectBrowserList")
        self.projects.setSpacing(4)
        self.projects.setWordWrap(True)
        self.projects.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        project_layout.addWidget(self.projects, 1)
        self.empty = QLabel("No hay proyectos que coincidan con la búsqueda.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setVisible(False)
        project_layout.addWidget(self.empty)
        browser.addWidget(project_frame, 1)
        root.addLayout(browser, 1)

        self.summary = QFrame()
        self.summary.setObjectName("ProjectSummary")
        summary_layout = QVBoxLayout(self.summary)
        summary_layout.setContentsMargins(12, 9, 12, 9)
        summary_layout.setSpacing(3)
        self.summary_title = QLabel("Selecciona un proyecto")
        self.summary_title.setObjectName("ProjectSummaryTitle")
        self.summary_meta = QLabel("Aquí verás las fuentes y el glosario asociados.")
        self.summary_meta.setObjectName("Muted")
        self.summary_fonts = QLabel("")
        self.summary_fonts.setObjectName("ProjectFonts")
        self.summary_fonts.setWordWrap(True)
        summary_layout.addWidget(self.summary_title)
        summary_layout.addWidget(self.summary_meta)
        summary_layout.addWidget(self.summary_fonts)
        root.addWidget(self.summary)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Open)
        self.open_button = self.buttons.button(QDialogButtonBox.Open)
        self.open_button.setText("Usar este proyecto")
        self.open_button.setObjectName("Primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.categories.currentItemChanged.connect(lambda *_: self._refresh_projects())
        self.search.textChanged.connect(lambda *_: self._refresh_projects())
        self.projects.currentItemChanged.connect(lambda *_: self._refresh_summary())
        self.projects.itemDoubleClicked.connect(lambda *_: self.accept())
        QShortcut(QKeySequence.Find, self, activated=self.search.setFocus)

        self._populate_categories()
        self._select_initial_project()

    def _all_entries(self) -> list[tuple[str, str]]:
        return [
            (project_type, project)
            for project_type in self.manager.project_types()
            for project in self.manager.projects(project_type)
        ]

    def _populate_categories(self) -> None:
        self.categories.clear()
        all_entries = self._all_entries()
        all_item = QListWidgetItem(f"Todos los proyectos   {len(all_entries)}")
        all_item.setData(Qt.UserRole, ALL_TYPES)
        all_item.setIcon(icon("grid", "#6EDCE7", 15))
        self.categories.addItem(all_item)
        for project_type in self.manager.project_types():
            count = len(self.manager.projects(project_type))
            item = QListWidgetItem(f"{project_type}   {count}")
            item.setData(Qt.UserRole, project_type)
            item.setIcon(icon("folder", "#9AAAB2", 15))
            self.categories.addItem(item)
        target = ALL_TYPES
        if self.current_type in self.manager.project_types():
            target = self.current_type
        for index in range(self.categories.count()):
            if self.categories.item(index).data(Qt.UserRole) == target:
                self.categories.setCurrentRow(index)
                break

    def _selected_type_filter(self) -> str:
        item = self.categories.currentItem()
        return str(item.data(Qt.UserRole)) if item else ALL_TYPES

    def _matching_entries(self) -> list[tuple[str, str]]:
        query = self.search.text().strip().casefold()
        selected_type = self._selected_type_filter()
        if query:
            # Global search across all project types when typing
            entries = [
                entry for entry in self._all_entries()
                if query in entry[1].casefold() or query in entry[0].casefold()
            ]
        else:
            entries = self._all_entries() if selected_type == ALL_TYPES else [
                (selected_type, project) for project in self.manager.projects(selected_type)
            ]
        return sorted(entries, key=lambda entry: (entry[0].casefold(), entry[1].casefold()))

    def _refresh_projects(self) -> None:
        previous = self.selected_project()
        self.projects.clear()
        entries = self._matching_entries()
        for project_type, project in entries:
            fonts = self.manager.font_entries(project_type, project)
            glossary = self.manager.translation_profile(project_type, project).get("glossary", [])
            item = QListWidgetItem(
                f"{project}\n{project_type}  ·  {len(fonts)} fuente(s)  ·  {len(glossary)} término(s)"
            )
            item.setData(Qt.UserRole, (project_type, project))
            item.setSizeHint(QSize(0, 64))
            self.projects.addItem(item)
            if (project_type, project) == previous:
                self.projects.setCurrentItem(item)
        self.empty.setVisible(not entries)
        self.projects.setVisible(bool(entries))
        if entries and self.projects.currentRow() < 0:
            self.projects.setCurrentRow(0)
        self._refresh_summary()

    def _select_initial_project(self) -> None:
        for index in range(self.projects.count()):
            if self.projects.item(index).data(Qt.UserRole) == (self.current_type, self.current_project):
                self.projects.setCurrentRow(index)
                return
        if self.projects.count():
            self.projects.setCurrentRow(0)

    def selected_project(self) -> tuple[str, str] | None:
        item = self.projects.currentItem()
        value = item.data(Qt.UserRole) if item else None
        return tuple(value) if value else None

    def _refresh_summary(self) -> None:
        selected = self.selected_project()
        self.open_button.setEnabled(selected is not None)
        if selected is None:
            self.summary_title.setText("Selecciona un proyecto")
            self.summary_meta.setText("Aquí verás las fuentes y el glosario asociados.")
            self.summary_fonts.clear()
            return
        project_type, project = selected
        fonts = self.manager.font_entries(project_type, project)
        glossary = self.manager.translation_profile(project_type, project).get("glossary", [])
        self.summary_title.setText(project)
        self.summary_meta.setText(
            f"{project_type}  ·  {len(fonts)} perfil(es) de fuente  ·  {len(glossary)} término(s) en el glosario"
        )
        previews = [f"{alias}: {data.get('family') or 'Sin fuente'}" for alias, data in fonts.items()]
        if len(previews) > 5:
            previews = [*previews[:5], f"+ {len(previews) - 5} más"]
        self.summary_fonts.setText("   •   ".join(previews) if previews else "Este proyecto todavía no tiene fuentes asignadas.")

    def accept(self) -> None:
        if self.selected_project() is not None:
            super().accept()
