"""Script refreshes must retain editing state and avoid synchronous recomposition."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtWidgets import QApplication
import pytest

from ui.canvas_view import CanvasView
from ui.main_window import MainWindow
from ui.script_panel import ScriptPanel


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_refresh_preserves_card_cursor_and_undo_when_another_translation_changes(app):
    panel = ScriptPanel()
    regions = [dict(id="one", text="Original", translation="Hola"),
               dict(id="two", text="Otro", translation="Mundo")]
    panel.set_regions(regions)
    card = panel._cards["one"]
    cursor = card.editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    card.editor.setTextCursor(cursor)
    card.editor.insertPlainText("!")
    regions[0]["translation"] = "Hola!"
    regions[1]["translation"] = "Nuevo"
    panel.set_regions(regions)
    assert panel._cards["one"] is card
    assert card.editor.textCursor().position() == 5
    card.editor.undo()
    assert card.editor.toPlainText() == "Hola"
    assert panel._cards["two"].editor.toPlainText() == "Nuevo"
    panel.close()


def test_refresh_updates_source_order_and_active_filter_without_spurious_edits(app):
    panel = ScriptPanel()
    panel.set_regions([dict(id="one", text="Original", translation="Hola"),
                       dict(id="two", text="Otro", translation="Mundo")])
    panel.filter_input.setText("hola")
    edits = []
    panel.text_edited.connect(lambda *value: edits.append(value))
    panel.set_regions([dict(id="two", text="Fuente corregida", translation="Mundo"),
                       dict(id="one", text="Original", translation="Hola")])
    assert panel.cards_container.itemAt(0).widget().region_id == "two"
    assert panel._cards["two"].orig_box.text() == "Fuente corregida"
    assert panel._cards["two"].isHidden()
    assert panel._cards["one"].badge.text() == "#02"
    assert edits == []
    panel.close()


def test_unchanged_queue_keeps_current_row_and_long_preview_keeps_full_tooltip(app):
    panel = ScriptPanel()
    entries = [dict(text=f"Traducción {index}") for index in range(100)]
    panel.set_pending_translations(entries)
    panel.queue_list.setCurrentRow(90)
    panel.set_pending_translations(entries)
    assert panel.queue_list.currentRow() == 90
    long_text = "Un párrafo muy largo " * 1000
    panel.set_pending_translations([dict(text=long_text)])
    assert len(panel.queue_list.item(0).text()) < 300
    assert panel.queue_list.item(0).toolTip() == long_text.strip()
    panel.close()


def test_paste_updates_canvas_and_page_immediately_without_replacing_glyph_item(app):
    canvas = CanvasView()
    image = QImage(300, 200, QImage.Format_RGB888)
    image.fill(255)
    canvas.set_image(image)
    region = dict(id="one", x=30, y=30, width=150, height=100,
                  applied_text="Hola", style=dict(font_size=24, auto_fit=True, balloon_fit=True))
    canvas.set_regions([region])
    canvas.add_text(["Hola"], [region])
    original_item = canvas._text_items[0]

    class Window:
        _schedule_text_render = MainWindow._schedule_text_render
        _render_active_text = MainWindow._render_active_text
        _pending_text_render_all = False
        _restoring_state = False
        _pending_text_render_indices = set()
        project = SimpleNamespace(pages=[object()])
        layers = SimpleNamespace(current_index=lambda: -1)
        _active_key = lambda self: "page"
        _store_active_regions = lambda self, values: setattr(self, "saved", values)
        _refresh_page_text = lambda *_: None

    window = Window()
    window.canvas_shell = SimpleNamespace(canvas=canvas)
    window.regions = [region]
    window._history_timer = QTimer()
    window._text_render_timer = QTimer()
    window._text_render_timer.setSingleShot(True)
    window._text_render_timer.timeout.connect(window._render_active_text)
    MainWindow._script_text_edited(window, "one", "Texto recién pegado")
    assert canvas._text_items[0] is original_item
    assert original_item.toPlainText() == "Texto recién pegado"
    assert window.saved[0]["applied_text"] == "Texto recién pegado"
    assert window._text_render_timer.isActive()
    window._text_render_timer.stop()
    window._render_active_text()
    assert canvas._text_values[0] == "Texto recién pegado"
    window._history_timer.stop()
    canvas.close()
