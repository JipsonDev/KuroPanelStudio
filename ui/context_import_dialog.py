"""Import a series' glossary and context from a .txt file."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from core.context_import import CONTEXT_REQUEST, merged_glossary, merged_prompt, parse_context


class ContextImportDialog(QDialog):
    def __init__(self, manager, project_type: str, project: str, parent=None) -> None:
        super().__init__(parent)
        self.manager, self.project_type, self.project = manager, project_type, project
        self.imported_terms = 0
        self.setWindowTitle(f"Importar contexto · {project}")
        self.setMinimumSize(640, 600)
        layout = QVBoxLayout(self)

        step_one = QLabel(
            f"<b>1. Prompt para obtener el contexto.</b> Cópialo o guárdalo y envíalo en el chat "
            f"donde traduces <b>{project}</b>. Guarda la respuesta en un .txt."
        )
        step_one.setWordWrap(True)
        layout.addWidget(step_one)
        request = QPlainTextEdit(CONTEXT_REQUEST)
        request.setReadOnly(True)
        request.setMaximumHeight(150)
        layout.addWidget(request)
        copy_row = QHBoxLayout()
        self.copy_button = QPushButton("Copiar prompt")
        self.copy_button.clicked.connect(self._copy_request)
        copy_row.addWidget(self.copy_button)
        save_prompt = QPushButton("Guardar prompt como .txt…")
        save_prompt.clicked.connect(self._save_request)
        copy_row.addWidget(save_prompt)
        copy_row.addStretch()
        layout.addLayout(copy_row)

        step_two = QLabel("<b>2. Abre el .txt con la respuesta</b> (o pega el texto aquí).")
        step_two.setWordWrap(True)
        layout.addWidget(step_two)
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("OBRA: …\n\n## CONTEXTO\n…\n\n## ESTILO\n…\n\n## GLOSARIO\noriginal | traducción | categoría | nota")
        self.answer.textChanged.connect(self._preview)
        layout.addWidget(self.answer, 1)
        open_row = QHBoxLayout()
        open_button = QPushButton("Abrir .txt…")
        open_button.setObjectName("Primary")
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
        QApplication.clipboard().setText(CONTEXT_REQUEST)
        self.copy_button.setText("Copiado ✓")

    def _save_request(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Guardar prompt", f"Prompt contexto - {self.project}.txt", "Texto (*.txt)",
        )
        if not filename:
            return
        try:
            Path(filename).write_text(CONTEXT_REQUEST + "\n", encoding="utf-8")
        except OSError as error:
            self.summary.setText(f"No se pudo guardar el prompt: {error}")
            return
        self.summary.setText(f"Prompt guardado en {Path(filename).name}.")

    def _open_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Abrir contexto (.txt)", "", "Texto (*.txt *.md *.json);;Todos los archivos (*)",
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
