"""Distribution preserves readable areas, capacity and preview/export geometry."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from io import BytesIO

import pytest
from PIL import Image
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from core.watermark_manager import (
    compose_watermark, normalized_watermark, prepare_watermark,
    watermark_positions, watermark_vertical_bounds,
)
from ui.canvas_view import CanvasView
from ui.watermark_dialog import WatermarkDialog


def png():
    stream = BytesIO()
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(stream, "PNG")
    return stream.getvalue()


def config(**values):
    return dict(repeat=True, auto_count=False, repeat_count=4,
                seam_safe=False, anchor="top-right", **values)


def test_reserves_space_for_all_marks_instead_of_dropping_greedy_collisions():
    settings = config(avoid_regions=[
        dict(x=600, y=0, width=200, height=400),
        dict(x=600, y=800, width=200, height=200),
    ])
    points = watermark_positions((800, 1000), (100, 50), settings)
    assert len(points) == 4
    assert all(416 <= y <= 734 for x, y in points)
    assert all(b[1] - a[1] >= 98 for a, b in zip(points, points[1:]))


@pytest.mark.parametrize("avoid_text", [True, False])
def test_too_many_marks_are_reduced_and_spaced_even_without_text_regions(avoid_text):
    settings = {**config(), "repeat_count": 100, "minimum_gap": 80, "avoid_text": avoid_text}
    points = watermark_positions((800, 600), (100, 100), settings)
    assert len(points) == 3
    assert len(set(points)) == len(points)
    assert all(b[1] - a[1] >= 180 for a, b in zip(points, points[1:]))
    assert points[0][1] == 24 and points[-1][1] == 476


def test_alternating_layout_uses_both_sides_with_even_vertical_gaps():
    points = watermark_positions((800, 1600), (100, 50), config(distribution="alternating"))
    assert [x for x, y in points] == [676, 24, 676, 24]
    gaps = [b[1] - a[1] for a, b in zip(points, points[1:])]
    assert max(gaps) - min(gaps) <= 1
    assert watermark_positions((800, 1600), (100, 50), config(distribution="alternating")) == points


def test_fully_blocked_page_does_not_cover_dialogue():
    settings = config(avoid_regions=[dict(x=0, y=0, width=800, height=3000)])
    assert watermark_positions((800, 3000), (100, 50), settings) == []
    assert not compose_watermark(Image.new("RGB", (800, 3000), "white"),
                                 {**settings, "png_bytes": png()})


def test_single_watermark_also_respects_avoid_text():
    settings = dict(anchor="top-right", avoid_text=True,
                    avoid_regions=[dict(x=600, y=0, width=200, height=200)])
    assert watermark_positions((800, 1000), (100, 50), settings) == [(676, 216)]


def test_oversized_rotated_logo_fits_short_page():
    settings = dict(png_bytes=png(), size_mode="pixels", width_px=2000,
                    rotation=90, margin_x=20, margin_y=20)
    mark = prepare_watermark((800, 300), settings)
    assert mark.width <= 760 and mark.height <= 260
    assert mark.height / mark.width == pytest.approx(2, abs=.05)


def test_excessive_margins_collapse_to_center_without_duplicate_marks():
    settings = normalized_watermark(config(margin_y=10000))
    assert watermark_vertical_bounds((800, 100), (100, 50), settings) == (25, 25)
    assert watermark_positions((800, 100), (100, 50), settings) == [(676, 25)]


def test_manual_positions_and_deleted_marks_survive_new_layout_settings():
    settings = config(positions=[[40, 60], [600, 400]], distribution="alternating")
    assert watermark_positions((800, 1000), (100, 50), settings) == [(40, 60), (600, 400)]
    assert watermark_positions((800, 1000), (100, 50), {**settings, "positions": []}) == []


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_preview_and_composition_use_the_same_alternating_positions(app, tmp_path):
    settings = config(png_bytes=png(), size_mode="pixels", width_px=100,
                      distribution="alternating", opacity=100,
                      avoid_regions=[dict(x=600, y=0, width=200, height=200)])
    page = Image.new("RGB", (800, 1600), "white")
    path = tmp_path / "page.png"
    page.save(path)
    canvas = CanvasView()
    canvas._pixmap_item.setPixmap(QPixmap(str(path)))
    canvas.scene.setSceneRect(0, 0, 800, 1600)
    canvas.set_watermark(settings, True, "page.png")
    expected = watermark_positions(page.size, (100, 50), settings)
    assert [(round(i.x()), round(i.y())) for i in canvas._watermark_items] == expected
    assert compose_watermark(page, settings)
    for x, y in expected:
        assert page.getpixel((x + 50, y + 25)) == (255, 0, 0)
    canvas.close()


def test_dialog_exposes_distribution_and_reset_without_expanding_advanced(app):
    dialog = WatermarkDialog(config(distribution="alternating", minimum_gap=75))
    assert not dialog.advanced.is_expanded()
    assert dialog.values()["distribution"] == "alternating"
    assert dialog.values()["minimum_gap"] == 75
    emitted = []
    dialog.redistribute_requested.connect(lambda: emitted.append(True))
    dialog.redistribute.click()
    assert emitted == [True]
    dialog.repeat.setChecked(False)
    assert not dialog.distribution.isEnabled()
    assert dialog.avoid_text.isEnabled()
    dialog.set_scope_status(True, 1, 1, 0)
    assert "0 marca(s)" in dialog.status.text()
    dialog.close()


def test_redistribution_resets_only_current_page(app):
    from types import SimpleNamespace
    from ui.main_window import MainWindow
    calls = []
    window = SimpleNamespace(
        project=SimpleNamespace(pages=[1], active_page=SimpleNamespace(name="current.png")),
        _watermark_ready=lambda: True, watermark_dialog=None,
        watermark_settings=normalized_watermark(dict(page_positions={
            "current.png": [[20, 40]], "other.png": [[80, 200]],
        })),
        _refresh_watermark_preview=lambda **_: calls.append("preview"),
        _history_timer=SimpleNamespace(start=lambda: calls.append("history")),
    )
    MainWindow._redistribute_watermark_current(window)
    assert window.watermark_settings["page_positions"] == {"other.png": [[80, 200]]}
    assert calls == ["preview", "history"]


def test_appearance_changes_keep_positions_moved_after_opening_dialog(app):
    from types import SimpleNamespace
    from ui.main_window import MainWindow
    previous = normalized_watermark(dict(png_bytes=png(), page_positions={"page.png": [[80, 200]]}))
    stale = {**previous, "page_positions": {}, "opacity": 30}
    window = SimpleNamespace(
        project=SimpleNamespace(pages=[1], active_page=SimpleNamespace(name="page.png")),
        watermark_settings=previous,
        _refresh_watermark_preview=lambda **_: None,
        _history_timer=SimpleNamespace(start=lambda: None),
        _watermark_persist_timer=SimpleNamespace(start=lambda: None),
    )
    MainWindow._watermark_settings_changed(window, stale)
    assert window.watermark_settings["page_positions"] == {"page.png": [[80, 200]]}
    MainWindow._watermark_settings_changed(window, {**stale, "distribution": "alternating"})
    assert window.watermark_settings["page_positions"] == {}
