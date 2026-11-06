from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.controls import ModernButton, SectionTitle, Toggle


class LayerTextEdit(QTextEdit):
    """Text editor that commits once when the user leaves the field."""

    editing_finished = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.editing_finished.emit()


class InspectorPanel(QFrame):
    """Context inspector: page editing and document output stay separated."""

    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorPanel")
        self.setMinimumWidth(270)
        self.setMaximumWidth(330)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(9)

        page_header = QHBoxLayout()
        page_header.setSpacing(6)
        page_header.addWidget(SectionTitle("Página"))
        page_header.addStretch()
        self.page_label = QLabel("— / —")
        self.page_label.setObjectName("InspectorPage")
        self.page_label.setAlignment(Qt.AlignCenter)
        page_header.addWidget(self.page_label)
        root.addLayout(page_header)

        # Document navigation lives on the canvas; output belongs to the header.
        root.addWidget(self._build_layer_tab(), 1)
        self.format_choice = QComboBox(self)
        self.format_choice.addItems(["PNG", "JPG", "WEBP", "PSD"])
        self.format_choice.hide()
        self._stage = "Listo"
        self._progress = 0

    def _build_layer_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("InspectorTabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(9, 11, 9, 9)
        layout.setSpacing(8)

        self.text_section = SectionTitle("Texto de la capa")
        layout.addWidget(self.text_section)
        self.ocr_source = QLabel("Selecciona una caja para ver su OCR original.")
        self.ocr_source.setObjectName("OCRSource")
        self.ocr_source.setWordWrap(True)
        self.ocr_source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ocr_source.setMaximumHeight(48)
        layout.addWidget(self.ocr_source)

        ocr_header = QHBoxLayout()
        ocr_header.addWidget(SectionTitle("OCR de la página"))
        ocr_header.addStretch()
        self.pending_text_label = QLabel("")
        self.pending_text_label.setObjectName("Muted")
        ocr_header.addWidget(self.pending_text_label)
        layout.addLayout(ocr_header)
        self.page_ocr = QTextEdit()
        self.page_ocr.setObjectName("PageOCRSummary")
        self.page_ocr.setReadOnly(True)
        self.page_ocr.setPlaceholderText("El OCR completo aparecerá numerado aquí.")
        self.page_ocr.setMinimumHeight(72)
        self.page_ocr.setMaximumHeight(105)
        layout.addWidget(self.page_ocr)
        transfer_actions = QHBoxLayout()
        transfer_actions.setSpacing(6)
        copy_page = ModernButton("Copiar página", icon_name="clipboard")
        copy_page.clicked.connect(lambda: self.action_requested.emit("copy_current"))
        copy_chapter = ModernButton("Copiar capítulo")
        copy_chapter.clicked.connect(lambda: self.action_requested.emit("copy_all"))
        transfer_actions.addWidget(copy_page, 1)
        transfer_actions.addWidget(copy_chapter, 1)
        layout.addLayout(transfer_actions)
        paste_text = ModernButton("Pegar y distribuir texto")
        paste_text.clicked.connect(lambda: self.action_requested.emit("paste_ai"))
        layout.addWidget(paste_text)

        self.text_editor = LayerTextEdit()
        self.text_editor.setObjectName("LayerTextEditor")
        self.text_editor.setPlaceholderText("Traducción o texto final de esta capa…")
        self.text_editor.setMinimumHeight(105)
        self.text_editor.setMaximumHeight(150)
        layout.addWidget(self.text_editor)

        auto_row = QHBoxLayout()
        self.auto_apply = Toggle(True)
        auto_row.addWidget(self.auto_apply)
        auto_row.addWidget(QLabel("Aplicar al terminar de escribir"))
        auto_row.addStretch()
        layout.addLayout(auto_row)

        text_actions = QHBoxLayout()
        text_actions.setSpacing(6)
        apply = ModernButton("Aplicar texto", "Primary")
        apply.clicked.connect(lambda: self.action_requested.emit("apply_text"))
        clear = ModernButton("Vaciar")
        clear.clicked.connect(lambda: self.action_requested.emit("clear_text"))
        text_actions.addWidget(apply, 2)
        text_actions.addWidget(clear, 1)
        layout.addLayout(text_actions)

        layout.addSpacing(4)
        layout.addWidget(SectionTitle("Gestión de cajas"))
        remove_actions = QHBoxLayout()
        remove_actions.setSpacing(6)
        for label, command in (("Eliminar última", "delete_last"), ("Eliminar todas", "delete_all")):
            button = ModernButton(label)
            button.clicked.connect(lambda _, value=command: self.action_requested.emit(value))
            remove_actions.addWidget(button)
        layout.addLayout(remove_actions)
        delete_selected = ModernButton("Eliminar seleccionadas", icon_name="trash")
        delete_selected.setObjectName("DangerButton")
        delete_selected.clicked.connect(lambda: self.action_requested.emit("delete_selected"))
        layout.addWidget(delete_selected)
        layout.addStretch()
        return page

    def set_progress(self, value: int, active: bool = True) -> None:
        self._progress = max(0, min(100, int(value)))

    def set_stage(self, text: str, value: int = 0) -> None:
        self._stage = str(text)
        self.set_progress(value)

    def update_page(self, index: int, total: int) -> None:
        self.page_label.setText(f"{index + 1} / {total}" if total else "— / —")

    def export_format(self) -> str:
        return self.format_choice.currentText()

    def set_layer_text(self, index: int, source: str, translated: str, number: int | None = None) -> None:
        box_number = int(number if number is not None else index + 1)
        self.text_section.setText(f"Caja #{box_number:02} · OCR y traducción")
        self.ocr_source.setProperty("kuro_i18n_ignore", bool(source))
        self.ocr_source.setText(
            f"#{box_number:02}  {source}" if source
            else f"#{box_number:02}  Sin OCR. Puedes escribir directamente el texto final."
        )
        self.ocr_source.setToolTip(source)
        self.text_editor.blockSignals(True)
        self.text_editor.setPlainText(translated)
        self.text_editor.blockSignals(False)

    def clear_layer_text(self) -> None:
        self.text_section.setText("Texto de la capa")
        self.ocr_source.setProperty("kuro_i18n_ignore", False)
        self.ocr_source.setText("Selecciona una caja para ver su OCR original.")
        self.text_editor.blockSignals(True)
        self.text_editor.clear()
        self.text_editor.blockSignals(False)

    def set_page_ocr(self, text: str, pending: int = 0) -> None:
        self.page_ocr.setPlainText(str(text or ""))
        self.pending_text_label.setText(f"{pending} pendiente(s)" if pending else "")

    def clear_page_ocr(self) -> None:
        self.page_ocr.clear()
        self.pending_text_label.clear()
