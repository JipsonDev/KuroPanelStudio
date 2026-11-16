"""Import a series' glossary and context from a ChatGPT project."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from core.context_import import CHATGPT_REQUEST, merged_glossary, merged_prompt, parse_context


class ContextImportDialog(QDialog):
    def __init__(self, manager, project_type: str, project: str, parent=None) -> None:
        super().__init__(parent)
        self.manager, self.project_type, self.project = manager, project_type, project
        self.imported_terms = 0
        self.setWindowTitle(f"Importar contexto · {project}")
        self.setMinimumSize(640, 600)
        layout = QVBoxLayout(self)

        step_one = QLabel(
            f"<b>1.</b> Copia esta petición y envíala en el proyecto de ChatGPT de <b>{project}</b>."
        )
        step_one.setWordWrap(True)
        layout.addWidget(step_one)
        request = QPlainTextEdit(CHATGPT_REQUEST)
        request.setReadOnly(True)
        request.setMaximumHeight(150)
        layout.addWidget(request)
        copy_row = QHBoxLayout()
        self.copy_button = QPushButton("Copiar petición")
        self.copy_button.clicked.connect(self._copy_request)
        copy_row.addWidget(self.copy_button)
        copy_row.addStretch()
        layout.addLayout(copy_row)

        step_two = QLabel("<b>2.</b> Pega aquí la respuesta completa de ChatGPT, o abre un .txt donde la guardaste.")
        step_two.setWordWrap(True)
        layout.addWidget(step_two)
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("OBRA: …\n\n## CONTEXTO\n…\n\n## ESTILO\n…\n\n## GLOSARIO\noriginal | traducción | categoría | nota")
        self.answer.textChanged.connect(self._preview)
        layout.addWidget(self.answer, 1)
        open_row = QHBoxLayout()
        open_button = QPushButton("Abrir .txt…")
        open_button.clicked.connect(self._open_file)
        open_row.addWidget(open_button)
        open_row.addStretch()
        layout.addLayout(open_row)

        self.summary = QLabel("Todavía no hay nada que importar.")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        note = QLabel(
            "Los términos importados reemplazan a los que tengan el mismo original; los demás se conservan. "
            "El contexto se añade a las instrucciones del proyecto y sustituye una importación anterior."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.import_button = buttons.addButton("Importar al proyecto", QDialogButtonBox.AcceptRole)
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _copy_request(self) -> None:
        QApplication.clipboard().setText(CHATGPT_REQUEST)
        self.copy_button.setText("Copiada ✓")

    def _open_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Abrir respuesta de ChatGPT", "", "Texto (*.txt *.md *.json);;Todos los archivos (*)",
        )
        if not filename:
            return
        try:
            self.answer.setPlainText(Path(filename).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as error:
            self.summary.setText(f"No se pudo leer el archivo: {error}")

    def _preview(self) -> None:
        imported = parse_context(self.answer.toPlainText())
        self.import_button.setEnabled(not imported.empty)
        if imported.empty:
            self.summary.setText("No se encontró glosario ni contexto. Revisa que la respuesta tenga las secciones.")
            return
        existing = {
            term["source"].casefold(): term["target"]
            for term in self.manager.translation_profile(self.project_type, self.project)["glossary"]
        }
        new = sum(term["source"].casefold() not in existing for term in imported.glossary)
        changed = sum(
            existing.get(term["source"].casefold()) not in (None, term["target"]) for term in imported.glossary
        )
        parts = []
        if imported.title:
            parts.append(f"Obra: {imported.title}")
        parts.append(f"{len(imported.glossary)} término(s): {new} nuevo(s), {changed} con otra traducción")
        parts.append("contexto ✓" if imported.context else "sin contexto")
        parts.append("estilo ✓" if imported.style else "sin reglas de estilo")
        self.summary.setText(" · ".join(parts))

    def accept(self) -> None:
        imported = parse_context(self.answer.toPlainText())
        if imported.empty:
            return
        profile = self.manager.translation_profile(self.project_type, self.project)
        self.manager.set_translation_profile(
            self.project_type, self.project,
            merged_prompt(profile["prompt"], imported),
            merged_glossary(profile["glossary"], imported),
        )
        self.imported_terms = len(imported.glossary)
        super().accept()
