"""Taking an image out of an open chapter."""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from core.project_manager import Page
from core.project_io import page_key
from ui.main_window import MainWindow


def test_removing_a_page_drops_its_work_and_keeps_the_file(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with (patch.object(MainWindow, "_offer_recovery"),
          patch.object(MainWindow, "_import_legacy_profiles_async"),
          patch.object(MainWindow, "_warm_up_models")):
        window = MainWindow()
        try:
            pages = []
            for number in range(3):
                path = tmp_path / f"{number:02d}.png"
                Image.new("RGB", (40, 70), "white").save(path)
                pages.append(Page(path.name, path, 40, 70))
            window.project.pages = list(pages)
            window.images_panel.refresh_pages(window.project.pages)
            window.page_regions[page_key(pages[1])] = [{"id": "a", "x": 1, "y": 1, "width": 5, "height": 5}]

            def answer(box):
                box.clickedButton = lambda: next(button for button in box.buttons()
                                                 if box.buttonRole(button) == QMessageBox.DestructiveRole)
                return 0

            with patch.object(QMessageBox, "exec", answer):
                window.remove_page(1)
            assert [page.name for page in window.project.pages] == ["00.png", "02.png"]
            assert page_key(pages[1]) not in window.page_regions
            assert pages[1].path.exists()
            assert window.project.active_index == 1
        finally:
            window.close()
            app.processEvents()
