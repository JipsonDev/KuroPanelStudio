"""Manual watermark edits must survive planning, export and document history."""
from copy import deepcopy
from io import BytesIO
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from core.chapter_watermarks import chapter_watermark_settings
from core.history_manager import HistoryManager
from core.project_manager import Page
from core.watermark_manager import normalized_watermark
from ui.canvas_view import CanvasView
from ui.main_window import MainWindow


def logo(colour="red"):
    buffer = BytesIO()
    Image.new("RGBA", (100, 50), colour).save(buffer, "PNG")
    return buffer.getvalue()


def chapter(**changes):
    pages = [dict(name=f"{i}.png", width=800, height=1000) for i in range(3)]
    settings = normalized_watermark(dict(
        png_bytes=logo(), repeat=True, auto_count=False, repeat_count=6,
        minimum_gap=400, size_mode="pixels", width_px=100, opacity=100,
        distribution="alternating", enabled_pages=[p["name"] for p in pages],
        **changes,
    ))
    return pages, settings


@pytest.mark.parametrize("positions", [
    [[-1, 225], [24, 725]],
    [[676, -10], [24, 725]],
    [[780, 225], [24, 980]],
    [[100, 100], [110, 110]],
])
def test_unconstrained_manual_positions_are_never_discarded(positions):
    pages, settings = chapter(keep_inside=False, page_positions={"0.png": positions})

    planned = chapter_watermark_settings(pages, settings)

    assert planned["0.png"]["positions"] == [tuple(point) for point in positions]
    assert settings["page_positions"]["0.png"] == positions


def test_editing_one_page_does_not_redistribute_other_pages():
    pages, settings = chapter(page_positions={"0.png": [[650, 200], [24, 725]]})

    planned = chapter_watermark_settings(pages, settings)

    assert planned["0.png"]["positions"] == [(650, 200), (24, 725)]
    assert planned["1.png"]["positions"] == [(676, 225), (24, 725)]
    assert planned["2.png"]["positions"] == [(676, 225), (24, 725)]


def test_manual_boundary_collision_omits_only_conflicting_automatic_mark():
    pages, settings = chapter(page_positions={"0.png": [[676, 940]]})

    planned = chapter_watermark_settings(pages, settings)

    assert planned["0.png"]["positions"] == [(676, 940)]
    assert planned["1.png"]["positions"] == [(24, 725)]
    assert planned["2.png"]["positions"] == [(676, 225), (24, 725)]


@pytest.mark.parametrize("change", ["position", "asset"])
def test_watermark_only_edit_is_recorded_and_survives_undo_redo(change):
    _, settings = chapter(page_positions={"0.png": [[676, 225]]})
    before = {"watermark_settings": settings}
    after = deepcopy(before)
    if change == "position":
        after["watermark_settings"]["page_positions"]["0.png"] = [[-1, 230]]
    else:
        after["watermark_settings"]["png_bytes"] = logo("blue")
    history = HistoryManager()
    history.reset(before)

    assert history.record(after), "Watermark-only changes must mark the document dirty"
    assert history.undo()["watermark_settings"] == before["watermark_settings"]
    assert history.redo()["watermark_settings"] == after["watermark_settings"]


def test_chapter_export_keeps_exact_dragged_canvas_positions(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pages, settings = chapter(keep_inside=False)
    project_pages = []
    for page in pages:
        path = tmp_path / page["name"]
        Image.new("RGB", (800, 1000), "white").save(path)
        project_pages.append(Page(page["name"], path, 800, 1000))
    canvas = CanvasView()
    image = QImage(800, 1000, QImage.Format_RGB888)
    image.fill(Qt.white)
    canvas.set_image(image)
    initial = chapter_watermark_settings(pages, settings)
    canvas.set_watermark(initial["0.png"], True, "0.png")
    assert len(canvas._watermark_items) == 2
    window = SimpleNamespace(
        project=SimpleNamespace(pages=project_pages, active_index=0, active_page=project_pages[0]),
        watermark_settings=settings, _restoring_state=False,
        _history_timer=SimpleNamespace(start=lambda: None),
        page_regions={}, clean_results={}, page_styles={},
        inspector=SimpleNamespace(export_format=lambda: "PNG"),
    )
    canvas.watermark_positions_changed.connect(
        lambda positions: MainWindow._watermark_positions_changed(window, positions))
    canvas._watermark_items[0].setPos(-1, 230)
    canvas._emit_watermark_positions()
    assert settings["page_positions"]["0.png"] == [[-1, 230], [24, 725]]
    preview = QImage(800, 1000, QImage.Format_RGB888)
    preview.fill(Qt.white)
    painter = QPainter(preview)
    canvas.scene.render(painter, QRectF(0, 0, 800, 1000), QRectF(0, 0, 800, 1000), Qt.IgnoreAspectRatio)
    painter.end()
    output = tmp_path / "exported"
    output.mkdir()
    monkeypatch.setattr("ui.main_window.QFileDialog.getExistingDirectory", lambda *_: str(output))
    # Replace only task scheduling, not chapter planning or actual image export.
    window._start_task = lambda work, *_: work(lambda *_: None, lambda: False)

    MainWindow.save_all(window)

    with Image.open(output / "0.png") as exported:
        np.testing.assert_array_equal(np.asarray(exported), canvas._qimage_rgb_array(preview))
    for name in ("1.png", "2.png"):
        with Image.open(output / name) as exported:
            assert exported.getpixel((700, 250)) == (255, 0, 0)
            assert exported.getpixel((50, 750)) == (255, 0, 0)
    canvas.close()
    app.processEvents()
