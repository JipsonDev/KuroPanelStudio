"""Side-by-side script review and global find & replace panel."""
from __future__ import annotations

import re
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.widgets.controls import CollapsibleSection, ModernButton, SectionTitle
from ui.widgets.icons import icon


class AutoResizingTextEdit(QPlainTextEdit):
    """Compact text editor that auto-adjusts height and provides keyboard navigation."""

    editing_finished = Signal()
    tab_jump_requested = Signal(bool)  # forward (True) or backward (False)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScriptEditorText")
        self.setPlaceholderText("Escribe la traducción...")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumHeight(44)
        self.setMaximumHeight(140)
        self.document().documentLayout().documentSizeChanged.connect(self._adjust_height)

    def _adjust_height(self) -> None:
        doc_height = int(self.document().size().height()) + 14
        self.setFixedHeight(max(44, min(140, doc_height)))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Tab and not (event.modifiers() & Qt.ControlModifier):
            self.tab_jump_requested.emit(not bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
            self.tab_jump_requested.emit(True)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.editing_finished.emit()


class DialogueScriptCard(QFrame):
    """Side-by-side card presenting original OCR text and editable translation."""

    selected = Signal(str)
    text_changed = Signal(str, str)
    tab_next_requested = Signal(str, bool)
    translate_requested = Signal(str)

    def __init__(self, region: dict, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.region_id = str(region.get("id", f"region-{index}"))
        self._updating = False
        self.setObjectName("DialogueScriptCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "DialogueScriptCard, QFrame#DialogueScriptCard {"
            "  background: #0E1A26; border: 1px solid #1E3348; border-radius: 8px; margin-bottom: 2px;"
            "}"
            "QFrame#DialogueScriptCard[active=\"true\"] {"
            "  border: 1px solid #00CFE8; background: #132435;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(5)

        # Header row: [#01] + Shape / Confidence + Quick Actions
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self.badge = QLabel(f"#{index + 1:02d}")
        self.badge.setStyleSheet(
            "background: #00CFE8; color: #071018; font-weight: bold; font-size: 11px; "
            "padding: 1px 6px; border-radius: 4px;"
        )
        header_row.addWidget(self.badge)

        role = str(region.get("font_role") or "Diálogo")
        self.role_label = QLabel(role)
        self.role_label.setStyleSheet("color: #7E9BB6; font-size: 11px; font-weight: 600;")
        header_row.addWidget(self.role_label)

        header_row.addStretch()

        self.btn_focus = ModernButton("", "Secondary", icon_name="mouse-pointer")
        self.btn_focus.setToolTip("Centrar en el lienzo")
        self.btn_focus.setFixedSize(24, 22)
        self.btn_focus.clicked.connect(lambda: self.selected.emit(self.region_id))
        header_row.addWidget(self.btn_focus)

        self.btn_copy = ModernButton("", "Secondary", icon_name="copy")
        self.btn_copy.setToolTip("Copiar traducción")
        self.btn_copy.setFixedSize(24, 22)
        self.btn_copy.clicked.connect(self._copy_translation)
        header_row.addWidget(self.btn_copy)

        layout.addLayout(header_row)

        # Original source text box (OCR reference)
        orig_text = str(region.get("text", "")).strip()
        if orig_text:
            self.orig_box = QLabel(orig_text)
            self.orig_box.setWordWrap(True)
            self.orig_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.orig_box.setStyleSheet(
                "background: #09131C; color: #8FA8BE; border: 1px dashed #1E3348; "
                "border-radius: 5px; padding: 4px 6px; font-size: 11px;"
            )
            layout.addWidget(self.orig_box)

        # Editable target translation text
        trans_text = str(
            region.get("translation") or region.get("applied_text") or orig_text
        ).strip()
        self.editor = AutoResizingTextEdit()
        self.editor.setPlainText(trans_text)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.tab_jump_requested.connect(lambda fwd: self.tab_next_requested.emit(self.region_id, fwd))
        layout.addWidget(self.editor)

    def _copy_translation(self) -> None:
        text = self.editor.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _on_text_changed(self) -> None:
        if not self._updating:
            self.text_changed.emit(self.region_id, self.editor.toPlainText().strip())

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_text(self, text: str) -> None:
        if self.editor.toPlainText().strip() != text.strip():
            self._updating = True
            self.editor.setPlainText(text)
            self._updating = False

    def focus_editor(self) -> None:
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)


class ScriptPanel(QScrollArea):
    """Full-featured side-by-side script proofreading and global find/replace panel."""

    region_selected = Signal(str)
    text_edited = Signal(str, str)  # region_id, new_text
    translate_page_requested = Signal()
    replace_all_requested = Signal(str, str, bool, bool, str)  # find, replace, case, word, scope
    search_next_requested = Signal(str, bool, bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(250)
        self.setMaximumWidth(320)
        self._cards: dict[str, DialogueScriptCard] = {}
        self._active_region_id = ""

        content = QFrame()
        content.setObjectName("AIPanel")
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(8, 11, 8, 11)
        root.setSpacing(9)

        root.addWidget(SectionTitle("Guión y Traducción"))

        # Stats and Page Actions
        stats_layout = QHBoxLayout()
        self.stats_label = QLabel("0 diálogos")
        self.stats_label.setObjectName("Muted")
        stats_layout.addWidget(self.stats_label)
        stats_layout.addStretch()

        self.btn_translate = ModernButton("Traducir", "Primary", icon_name="languages")
        self.btn_translate.setToolTip("Traducir los globos de la página activa")
        self.btn_translate.clicked.connect(self.translate_page_requested.emit)
        stats_layout.addWidget(self.btn_translate)

        self.btn_copy_all = ModernButton("", "Secondary", icon_name="clipboard")
        self.btn_copy_all.setToolTip("Copiar todo el guión al portapapeles")
        self.btn_copy_all.clicked.connect(self._copy_full_script)
        stats_layout.addWidget(self.btn_copy_all)

        root.addLayout(stats_layout)

        # Collapsible Find & Replace Section
        self.find_section = CollapsibleSection("Búsqueda y Reemplazo", False)
        find_layout = self.find_section.body_layout

        find_layout.addWidget(QLabel("BUSCAR"))
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Texto a buscar...")
        find_layout.addWidget(self.find_input)

        find_layout.addWidget(QLabel("REEMPLAZAR POR"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Reemplazar con...")
        find_layout.addWidget(self.replace_input)

        # Options
        options_layout = QVBoxLayout()
        options_layout.setSpacing(3)
        self.case_sensitive = QCheckBox("Coincidir mayúsculas / minúsculas")
        self.whole_word = QCheckBox("Palabra completa")
        options_layout.addWidget(self.case_sensitive)
        options_layout.addWidget(self.whole_word)
        find_layout.addLayout(options_layout)

        find_layout.addWidget(QLabel("ÁMBITO"))
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Página actual", "page")
        self.scope_combo.addItem("Todo el proyecto", "project")
        find_layout.addWidget(self.scope_combo)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_find_next = ModernButton("Buscar", "Secondary", icon_name="search")
        self.btn_find_next.clicked.connect(self._on_search_next)
        self.btn_replace = ModernButton("Reemplazar", "Secondary")
        self.btn_replace.clicked.connect(self._on_replace_current)
        self.btn_replace_all = ModernButton("Todos", "Primary")
        self.btn_replace_all.clicked.connect(self._on_replace_all)
        btn_row.addWidget(self.btn_find_next, 1)
        btn_row.addWidget(self.btn_replace, 1)
        btn_row.addWidget(self.btn_replace_all, 1)
        find_layout.addLayout(btn_row)

        self.replace_feedback = QLabel("")
        self.replace_feedback.setObjectName("Muted")
        self.replace_feedback.setWordWrap(True)
        find_layout.addWidget(self.replace_feedback)

        root.addWidget(self.find_section)

        # Dialogue Cards Container
        dialogues_header = QLabel("DIÁLOGOS DE LA PÁGINA")
        dialogues_header.setObjectName("Caption")
        root.addWidget(dialogues_header)

        # Filter bar
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filtrar diálogos...")
        self.filter_input.textChanged.connect(self._filter_cards)
        root.addWidget(self.filter_input)

        self.cards_container = QVBoxLayout()
        self.cards_container.setSpacing(6)
        root.addLayout(self.cards_container)

        root.addStretch()

    def set_regions(self, regions: list[dict], page_index: int = 0) -> None:
        """Populate the side-by-side list with all dialogue regions on the current page."""
        # Clear existing cards
        while self.cards_container.count():
            item = self.cards_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

        translated_count = 0
        for index, region in enumerate(regions):
            card = DialogueScriptCard(region, index)
            card.selected.connect(self.region_selected.emit)
            card.text_changed.connect(self.text_edited.emit)
            card.tab_next_requested.connect(self._handle_tab_jump)
            card.translate_requested.connect(self._handle_single_translate)
            self.cards_container.addWidget(card)
            self._cards[card.region_id] = card
            if str(region.get("translation") or region.get("applied_text") or "").strip():
                translated_count += 1

        total = len(regions)
        self.stats_label.setText(f"{total} diálogos · {translated_count} traducidos")
        if self._active_region_id and self._active_region_id in self._cards:
            self._cards[self._active_region_id].set_active(True)

    def select_region(self, region_id: str) -> None:
        """Highlight and scroll to the active region's card."""
        self._active_region_id = str(region_id)
        for rid, card in self._cards.items():
            is_match = (rid == self._active_region_id)
            card.set_active(is_match)
            if is_match:
                self.ensureWidgetVisible(card)

    def update_region_text(self, region_id: str, text: str) -> None:
        card = self._cards.get(str(region_id))
        if card is not None:
            card.set_text(text)

    def _filter_cards(self, query: str) -> None:
        q = query.strip().lower()
        for card in self._cards.values():
            if not q:
                card.setVisible(True)
            else:
                orig = card.orig_box.text().lower() if hasattr(card, "orig_box") else ""
                trans = card.editor.toPlainText().lower()
                card.setVisible(q in orig or q in trans)

    def _handle_tab_jump(self, current_id: str, forward: bool) -> None:
        ids = list(self._cards.keys())
        if current_id not in ids:
            return
        idx = ids.index(current_id)
        next_idx = (idx + 1) if forward else (idx - 1)
        if 0 <= next_idx < len(ids):
            next_card = self._cards[ids[next_idx]]
            next_card.focus_editor()
            self.region_selected.emit(next_card.region_id)

    def _handle_single_translate(self, region_id: str) -> None:
        self.region_selected.emit(region_id)

    def _copy_full_script(self) -> None:
        lines: list[str] = []
        for idx, (rid, card) in enumerate(self._cards.items(), 1):
            orig = card.orig_box.text().strip() if hasattr(card, "orig_box") else ""
            trans = card.editor.toPlainText().strip()
            lines.append(f"[{idx:02d}] {orig}\n-> {trans}\n")
        if lines:
            QApplication.clipboard().setText("\n".join(lines))
            self.stats_label.setText("¡Guión copiado al portapapeles!")

    def _on_search_next(self) -> None:
        query = self.find_input.text().strip()
        if not query:
            return
        self.search_next_requested.emit(
            query,
            self.case_sensitive.isChecked(),
            self.whole_word.isChecked(),
            str(self.scope_combo.currentData() or "page"),
        )

    def _on_replace_current(self) -> None:
        find_str = self.find_input.text()
        replace_str = self.replace_input.text()
        if not find_str or not self._active_region_id:
            return
        card = self._cards.get(self._active_region_id)
        if not card:
            return
        text = card.editor.toPlainText()
        flags = 0 if self.case_sensitive.isChecked() else re.IGNORECASE
        pattern = rf"\b{re.escape(find_str)}\b" if self.whole_word.isChecked() else re.escape(find_str)
        new_text, count = re.subn(pattern, replace_str, text, count=1, flags=flags)
        if count > 0:
            card.set_text(new_text)
            self.text_edited.emit(self._active_region_id, new_text)
            self.replace_feedback.setText(f"Reemplazado en #{self._active_region_id}")

    def _on_replace_all(self) -> None:
        find_str = self.find_input.text()
        replace_str = self.replace_input.text()
        if not find_str:
            return
        self.replace_all_requested.emit(
            find_str,
            replace_str,
            self.case_sensitive.isChecked(),
            self.whole_word.isChecked(),
            str(self.scope_combo.currentData() or "page"),
        )
