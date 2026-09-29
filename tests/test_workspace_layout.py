"""Navigation and state continuity while the workspace changes layout."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget
from ui.canvas_view import CanvasShell
from ui.topbar import TopBar
from ui.workspace import EditorWorkspace


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def workspace(app):
    tools = QLineEdit("Conservar este texto")
    layout = EditorWorkspace(QLabel("Páginas"), QLabel("Capas"), QWidget(), tools, QWidget())
    layout.resize(1400, 800)
    layout.show()
    app.processEvents()
    yield layout
    layout.close()


def test_compact_layout_keeps_document_and_edited_text(workspace, app):
    assets = workspace.assets
    assert assets.tabBar().isHidden()
    assets.setCurrentIndex(1)
    for width in (1000, 1400, 800, 1300):
        workspace.adapt_to_width(width)
        app.processEvents()
        assert workspace.assets is assets
        assert assets.currentIndex() == 1
        assert workspace.tools.text() == "Conservar este texto"
        assert workspace.count() == (2 if width < 1180 else 3)
        assert workspace.dock.count() == (3 if width < 1180 else 2)
        if width < 1180:
            workspace.dock.setCurrentWidget(assets)
            app.processEvents()
            assert assets.isVisible()
        else:
            assert workspace.widget(0) is assets
            assert assets.isVisible()


def test_focus_can_cross_breakpoint_and_recover_tools(workspace, app):
    states = []
    workspace.focus_changed.connect(states.append)
    workspace.set_focus_mode(True)
    assert workspace.dock.isHidden()
    assert workspace.assets.isHidden()
    workspace.adapt_to_width(800)
    workspace.adapt_to_width(1400)
    workspace.show_tools()
    app.processEvents()
    assert not workspace.dock.isHidden()
    assert not workspace.assets.isHidden()
    assert workspace.dock.currentWidget() is workspace.tools
    assert states == [True, False]


def test_focus_restores_splitter_proportions(workspace, app):
    workspace.setSizes([255, 815, 318])
    sizes = workspace.sizes()
    workspace.set_focus_mode(True)
    app.processEvents()
    workspace.set_focus_mode(False)
    app.processEvents()
    assert all(abs(a - b) <= 2 for a, b in zip(sizes, workspace.sizes()))


def test_compact_document_dock_can_collapse_and_reopen(workspace, app):
    workspace.adapt_to_width(720)
    workspace.resize(650, 500)
    app.processEvents()
    before = workspace.widget(0).width()
    workspace.set_dock_collapsed(True)
    app.processEvents()
    assert workspace.dock.isHidden()
    assert workspace.widget(0).width() > before
    workspace.show_tools()
    app.processEvents()
    assert not workspace.dock.isHidden()
    assert workspace.dock.currentWidget() is workspace.tools
    workspace.set_dock_collapsed(True)
    workspace.adapt_to_width(1400)
    app.processEvents()
    assert not workspace.dock.isHidden()
    assert not workspace.dock_collapsed


def test_application_menu_and_save_remain_accessible_on_small_screen(app):
    bar = TopBar()
    received = []
    bar.action_requested.connect(received.append)
    bar.show()
    bar.adapt_to_width(720)
    assert not bar.menu_button.isHidden()
    assert not bar.save_button.isHidden()
    for action in bar.app_menu.actions():
        action.trigger()
    bar.save_button.click()
    bar.export_button.click()
    bar.focus_button.click()
    assert received == ["switch_project", "settings", "watermark", "check_updates",
                        "save_project", "save_current", "toggle_focus"]
    bar.close()


def test_canvas_empty_state_tracks_page_and_offers_real_actions(app):
    shell = CanvasShell()
    received = []
    shell.welcome.action_requested.connect(received.append)
    shell.show()
    shell.canvas.clear_page()
    assert shell.stage_layout.currentWidget() is shell.welcome
    for button in shell.welcome.findChildren(QPushButton):
        button.click()
    assert received == ["open_folder", "open_psd"]
    page = QPixmap(100, 200)
    page.fill(Qt.white)
    shell.canvas.set_pixmap(page)
    assert shell.stage_layout.currentWidget() is shell.canvas
    shell.canvas.clear_page()
    shell.canvas.clear_page()
    assert shell.stage_layout.currentWidget() is shell.welcome
    # Re-entering the empty state must not accumulate scene hints.
    assert sum(item is shell.canvas._empty_hint for item in shell.canvas.scene.items()) == 1
    shell.close()
