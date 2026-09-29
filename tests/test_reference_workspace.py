"""Reference layout keeps navigation, list controls and progress operational."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QWidget

from core.image_manager import ImageManager
from core.project_manager import Page
from ui.images_panel import ImagesPanel, ImageListItem
from ui.layers_panel import LayersPanel
from ui.main_window import MainWindow
from ui.sidebar import Sidebar
from ui.status_bar import StatusBar
from ui.task_progress import TaskProgress
from ui.topbar import TopBar
from ui.workspace import EditorWorkspace


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_page_search_filter_reverse_and_selection_use_chapter_indices(app, tmp_path):
    pages = []
    for i in range(12):
        path = tmp_path / f"canvas_{i:02}.png"
        Image.new("RGB", (80, 160), (i * 18, 40, 90)).save(path)
        pages.append(Page(path.name, path, 80, 160))
    panel = ImagesPanel(pages, ImageManager())
    panel.resize(315, 560)
    panel.show()
    app.processEvents()
    received = []
    panel.page_selected.connect(received.append)
    panel.set_statuses([{"typeset": i % 2 == 0} for i in range(12)])
    panel.filter.setCurrentText("Rotuladas")
    assert sum(not panel.list.item(i).isHidden() for i in range(12)) == 6
    panel.filter.setCurrentText("Todas")
    panel.search.setText("canvas_03")
    assert sum(not panel.list.item(i).isHidden() for i in range(12)) == 1
    panel.search.clear()
    panel.sort_button.click()
    assert panel.list.itemWidget(panel.list.item(0)).name.full_text == "canvas_11.png"
    panel.list.setCurrentRow(3)  # Public API stays in project order.
    assert received[-1] == 3
    assert panel.list.currentRow() == 8
    panel.grid_button.click()
    assert panel.grid_button.isChecked()
    panel.list_button.click()
    assert panel.list_button.isChecked()
    panel.request_visible_thumbnails()
    assert len(panel._loaded_thumbnails) < len(pages)
    visible_row = panel.list.itemWidget(panel.list.item(panel.list.currentRow()))
    for _ in range(20):
        if visible_row.thumbnail.pixmap().height() > 28:
            break
        QTest.qWait(100)
    assert visible_row.thumbnail.pixmap().height() > 28
    row = panel.list.itemWidget(panel.list.item(panel.list.source_to_row[3]))
    assert isinstance(row, ImageListItem)
    assert row.thumbnail.pixmap() is not None
    panel.close()


def test_pending_page_filters_and_next_navigation(app, tmp_path):
    pages = []
    for index in range(3):
        path = tmp_path / f"page_{index}.png"
        Image.new("RGB", (40, 70)).save(path)
        pages.append(Page(path.name, path, 40, 70))
    panel = ImagesPanel(pages, ImageManager())
    panel.show()
    panel.set_statuses([
        {"detected": False},
        {"detected": True, "ocr": False, "error": True, "error_message": "OCR: 404"},
        {"detected": True, "ocr": True, "translated": False, "cleaned": False},
    ])
    panel.filter.setCurrentText("Pendiente OCR")
    assert [not panel.list.item(i).isHidden() for i in range(3)] == [False, True, False]
    panel.next_pending.click()
    assert panel.list.currentRow() == 1
    panel.filter.setCurrentText("Con error")
    assert panel.list.item(1).isHidden() is False
    row = panel.list.itemWidget(panel.list.item(1))
    assert "404" in row.status_badges["error"].toolTip()
    panel.filter.setCurrentText("Pendiente traducción")
    panel.next_pending.click()
    assert panel.list.currentRow() == 2
    panel.sort_button.click()
    panel.next_pending.click()
    assert panel.list.currentRow() == panel.list.source_to_row[2]
    panel.filter.setCurrentText("Con error")
    panel.next_pending.click()
    assert panel.list.currentRow() == panel.list.source_to_row[1]
    panel.close()


def test_sidebar_has_eight_real_destinations_and_active_gold_icon(app):
    sidebar = Sidebar()
    assert [button.tool_name for button in sidebar.buttons] == [
        "Editar", "Páginas", "Capas", "Procesar", "Guión", "Texto", "Efectos", "SFX"]
    assert sidebar.width() == 88
    assert next(button for button in sidebar.buttons if button.tool_name == "Páginas").isChecked()
    selected = []
    sidebar.tool_changed.connect(selected.append)
    next(button for button in sidebar.buttons if button.tool_name == "Capas").click()
    assert selected == ["Capas"]
    sidebar.resize(88, 385)
    sidebar.show()
    app.processEvents()
    assert all(button.toolButtonStyle() == Qt.ToolButtonTextUnderIcon for button in sidebar.buttons)
    assert sidebar.scroll.verticalScrollBar().maximum() > 0
    sidebar.scroll.ensureWidgetVisible(sidebar.buttons[-1])
    app.processEvents()
    assert sidebar.scroll.verticalScrollBar().value() > 0
    sidebar.close()


def test_narrow_page_row_elides_name_without_covering_thumbnail(app, tmp_path):
    page = Page("01 - Guerrero del fuego y de las llamas eternas.png", tmp_path / "page.png", 1099, 2465)
    row = ImageListItem(page)
    row.setFixedSize(185, 88)
    row.show()
    app.processEvents()
    assert row.name.text().endswith("…")
    assert row.thumbnail.geometry().right() < row.name.geometry().left()
    assert row.more.isHidden()
    assert row.badges.isHidden()
    row.close()


def test_page_panel_fits_narrow_dock_after_grid_and_list_switch(app):
    panel = ImagesPanel([Page("01 - Guerrero del fuego y las llamas eternas.png", None, 1099, 2465)], ImageManager())
    panel.resize(220, 330)
    panel.show()
    app.processEvents()
    assert panel.width() == 220
    panel.grid_button.click()
    panel.list_button.click()
    app.processEvents()
    row = panel.list.itemWidget(panel.list.item(0))
    assert row.width() <= panel.list.viewport().width()
    assert row.thumbnail.geometry().right() < row.name.geometry().left()
    assert row.more.isHidden()
    assert row.badges.isHidden()
    panel.close()


def test_layers_scroll_in_compact_workspace_without_row_overlap(app):
    layers = LayersPanel()
    workspace = EditorWorkspace(QLabel("Páginas"), layers, QWidget(), QLabel("Herramientas"), QWidget())
    workspace.resize(720, 440)
    workspace.adapt_to_width(720)
    workspace.show()
    workspace.dock.setCurrentWidget(workspace.assets)
    workspace.assets.setCurrentIndex(1)
    app.processEvents()
    assert workspace.layers_scroll.verticalScrollBar().maximum() > 0
    assert layers.original_layer.geometry().bottom() < layers.clean_layer.geometry().top()
    assert layers.original_layer.opacity.isVisibleTo(layers)
    workspace.close()


def test_layers_fit_a_narrow_document_panel(app):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    layers = LayersPanel()
    scroll.setWidget(layers)
    scroll.resize(240, 500)
    scroll.show()
    app.processEvents()
    assert layers.width() <= scroll.viewport().width()
    assert layers.original_layer.eye.geometry().right() <= layers.original_layer.width()
    assert layers.more_button.geometry().right() <= layers.width()
    assert layers.rename_button.text() == "Renombrar"
    scroll.close()


def test_empty_layers_offer_detection_and_menu_actions_keep_working(app):
    layers = LayersPanel()
    layers.set_image_layers(True, False, {})
    layers.show()
    app.processEvents()
    detected = []
    layers.detect_requested.connect(lambda: detected.append(True))
    assert layers.empty_action.isVisible()
    assert layers.empty_action.isEnabled()
    layers.empty_action.click()
    assert detected == [True]
    duplicated = []
    layers.duplicate_requested.connect(duplicated.append)
    layers.set_regions([{"id": "box", "x": 2, "y": 3, "width": 80, "height": 40}])
    assert layers.empty_state.isHidden()
    assert layers.more_actions["duplicate"].isEnabled()
    layers.more_actions["duplicate"].trigger()
    assert duplicated == [0]
    layers.close()


def test_sidebar_tracks_the_open_document_tools_and_inspector(app, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with (patch.object(MainWindow, "_offer_recovery"),
          patch.object(MainWindow, "_import_legacy_profiles_async"),
          patch.object(MainWindow, "_warm_up_models")):
        window = MainWindow()
        window.resize(720, 540)
        window.show()
        app.processEvents()
        active = lambda: next(button.tool_name for button in window.sidebar.buttons if button.isChecked())
        assert active() == "Procesar"
        assert window.canvas_shell.panel_button.accessibleName()
        window.canvas_shell.panel_button.click()
        assert window.workspace.dock.isHidden()
        window.sidebar._select("Editar")
        assert not window.workspace.dock.isHidden()
        assert window.workspace.dock.currentWidget() is window.inspector_scroll
        assert active() == "Editar"
        window.sidebar._select("Capas")
        assert window.workspace.dock.currentWidget() is window.workspace.assets
        assert active() == "Capas"
        window.workspace.dock.setCurrentWidget(window.tool_panel)
        assert active() == "Procesar"
        window.workspace.dock.setCurrentWidget(window.inspector_scroll)
        assert active() == "Editar"
        window.close()


def test_status_details_keep_diagnostics_available_without_crowding_the_strip(app):
    bar = StatusBar()
    bar.resize(720, 38)
    bar.show()
    bar.set_page("canvas_01.png", 1024, 1536, "3 MB")
    bar.set_operation_time("OCR", 1.25)
    app.processEvents()
    assert bar.details.isVisible()
    assert bar.resources.isHidden()
    assert bar.operation.isHidden()
    bar._populate_details_menu()
    labels = [action.text() for action in bar.details_menu.actions()]
    assert any("canvas_01.png · 1024 × 1536 px · 3 MB" in label for label in labels)
    assert any("RAM" in label for label in labels)
    assert any("Última: OCR · 1.25 s" in label for label in labels)
    bar.close()


def test_header_actions_and_progress_banner_stay_functional(app):
    bar = TopBar()
    actions = []
    bar.action_requested.connect(actions.append)
    bar.open_button.menu().actions()[0].trigger()
    bar.save_button.click()
    bar.export_button.click()
    bar.focus_button.click()
    assert actions == ["open_folder", "save_project", "save_current", "toggle_focus"]
    progress = TaskProgress()
    progress.start("Cargar capítulo")
    progress.update_progress(48)
    assert progress.bar.value() == 48
    assert not progress.complete_icon.isVisible()
    progress.finish()
    assert progress.complete_icon.isHidden() is False
    assert progress.dismiss.isVisible()
    progress.dismiss.click()
    assert progress.isHidden()
    bar.close()
