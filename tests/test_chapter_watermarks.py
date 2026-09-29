"""Regressions for watermark spacing across file boundaries."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap

from core.chapter_watermarks import chapter_watermark_settings
from core.watermark_manager import prepare_watermark, watermark_positions, compose_watermark
from core.project_manager import Page
from ui.canvas_view import CanvasView
from ui.main_window import MainWindow, page_key


def setup(heights=(1000, 1000, 1000), **changes):
    stream = BytesIO()
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(stream, "PNG")
    pages = [dict(name=f"{i}.png", width=800, height=h) for i, h in enumerate(heights)]
    config = dict(png_bytes=stream.getvalue(), repeat=True, auto_count=False,
                  repeat_count=6, minimum_gap=400, size_mode="pixels", width_px=100,
                  opacity=100, enabled_pages=[p["name"] for p in pages])
    return pages, {**config, **changes}


def flattened(pages, plan):
    points = []
    offset = 0
    for page in pages:
        settings = plan.get(page["name"])
        if settings:
            size = (page["width"], page["height"])
            mark = prepare_watermark(size, settings)
            for x, y in watermark_positions(size, mark.size, settings):
                assert 0 <= y <= page["height"] - mark.height
                assert 0 <= x <= page["width"] - mark.width
                points.append((x, offset + y, mark.height))
        offset += page["height"]
    return sorted(points, key=lambda p: p[1])


def assert_gap(points, gap):
    assert all(b[1] - a[1] - a[2] >= gap for a, b in zip(points, points[1:]))


def test_count_and_spacing_apply_to_whole_chapter_not_each_page():
    pages, config = setup()
    plan = chapter_watermark_settings(pages, config)
    points = flattened(pages, plan)
    assert len(points) == 6  # Previously 6 on each file (18 total).
    assert_gap(points, 400)
    assert points[2][1] - points[1][1] - 50 == 450  # Cross-file gap.
    stitched = [dict(name="all", width=800, height=3000)]
    single = chapter_watermark_settings(stitched, {**config, "enabled_pages": ["all"]})
    assert points == flattened(stitched, single)


@pytest.mark.parametrize("heights", [(300, 1700, 600, 2200), (50, 80, 2300, 120), (901, 1101, 1001)])
@pytest.mark.parametrize("distribution", ["column", "alternating"])
def test_unequal_pages_respect_spacing_and_never_split_logos(heights, distribution):
    pages, config = setup(heights, distribution=distribution, repeat_count=8)
    pages[1]["width"] = 1100
    config.update(size_mode="percent", scale_percent=14, rotation=20)
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert points
    assert len(points) <= 8
    assert_gap(points, 400)


def test_text_avoidance_near_seams_keeps_global_gap():
    pages, config = setup(minimum_gap=600, repeat_count=4)
    pages[0]["regions"] = [dict(x=600, y=500, width=200, height=500)]
    pages[1]["regions"] = [dict(x=600, y=0, width=200, height=250)]
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert len(points) == 4
    assert_gap(points, 600)
    assert not any(434 < y < 1266 for _, y, _ in points)


def test_alternation_does_not_restart_at_each_page():
    pages, config = setup(repeat_count=3, distribution="alternating")
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert [x for x, _, _ in points] == [676, 24, 676]


def test_increasing_spacing_reduces_capacity_across_seams():
    pages, config = setup(repeat_count=20)
    sparse = flattened(pages, chapter_watermark_settings(pages, {**config, "minimum_gap": 1100}))
    dense = flattened(pages, chapter_watermark_settings(pages, config))
    assert len(sparse) < len(dense)
    assert_gap(sparse, 1100)


def test_disabled_page_keeps_its_height_in_chapter_coordinates():
    pages, config = setup(enabled_pages=["0.png", "2.png"], repeat_count=3)
    plan = chapter_watermark_settings(pages, config)
    assert "1.png" not in plan
    points = flattened(pages, plan)
    assert_gap(points, 400)
    assert any(y >= 2000 for _, y, _ in points)


def test_manual_marks_are_preserved_and_reserve_space_on_adjacent_pages():
    pages, config = setup(page_positions={"0.png": [[676, 940]]})
    plan = chapter_watermark_settings(pages, config)
    assert plan["0.png"]["positions"] == [(676, 940)]
    assert_gap(flattened(pages, plan), 400)


def test_deleted_page_marks_are_not_regenerated():
    pages, config = setup(page_positions={"1.png": []})
    plan = chapter_watermark_settings(pages, config)
    assert plan["1.png"]["positions"] == []


def test_automatic_count_uses_combined_height():
    pages, config = setup((1000,) * 10, auto_count=True)
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert len(points) == 5


def test_entirely_blocked_pages_remain_empty():
    pages, config = setup()
    for page in pages:
        page["regions"] = [dict(x=0, y=0, width=800, height=1000)]
    plan = chapter_watermark_settings(pages, config)
    assert flattened(pages, plan) == []


def test_resolved_plan_is_identical_on_canvas_and_export(tmp_path):
    app = QApplication.instance() or QApplication([])
    pages, config = setup((750, 2250), repeat_count=4, minimum_gap=300)
    plan = chapter_watermark_settings(pages, config)
    for page in pages:
        size = (page["width"], page["height"])
        image = Image.new("RGB", size, "white")
        path = tmp_path / page["name"]
        image.save(path)
        canvas = CanvasView()
        canvas._pixmap_item.setPixmap(QPixmap(str(path)))
        canvas.scene.setSceneRect(0, 0, *size)
        settings = plan[page["name"]]
        canvas.set_watermark(settings, True, page["name"])
        assert [(round(i.x()), round(i.y())) for i in canvas._watermark_items] == settings["positions"]
        compose_watermark(image, settings)
        for x, y in settings["positions"]:
            assert image.getpixel((x + 50, y + 25)) == (255, 0, 0)
        canvas.close()


def test_single_export_and_preview_use_full_chapter_not_active_page():
    pages, config = setup()
    project_pages = [Page(p["name"], width=p["width"], height=p["height"]) for p in pages]
    window = SimpleNamespace(watermark_settings=config, page_regions={},
                             project=SimpleNamespace(pages=project_pages, active_page=project_pages[1]))
    window._chapter_watermarks = lambda **kw: MainWindow._chapter_watermarks(window, **kw)
    expected = chapter_watermark_settings(pages, config)
    assert MainWindow._watermark_for_page(window, project_pages[1])["positions"] == expected["1.png"]["positions"]
    assert window._chapter_watermarks(preview=True)["1.png"]["positions"] == expected["1.png"]["positions"]


def test_chapter_reset_removes_old_per_page_overrides():
    pages, config = setup(page_positions={"0.png": [[0, 0]], "1.png": [[0, 0]]})
    window = SimpleNamespace(watermark_settings=config, watermark_dialog=None,
                             project=SimpleNamespace(pages=pages), _watermark_ready=lambda: True,
                             _refresh_watermark_preview=lambda **_: None,
                             _history_timer=SimpleNamespace(start=lambda: None))
    MainWindow._redistribute_watermark_chapter(window)
    assert window.watermark_settings["page_positions"] == {}


def test_blocked_pages_do_not_push_their_marks_to_start_of_next_page():
    pages, config = setup((4000, 4000, 4000), auto_count=True, minimum_gap=48)
    for page in pages[:2]:
        page["regions"] = [dict(x=0, y=0, width=800, height=4000)]
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert len(points) == 2
    assert all(y > 8500 for _, y, _ in points)
    assert all(b[1] - a[1] > 1000 for a, b in zip(points, points[1:]))


def test_loading_thumbnail_does_not_clamp_marks_to_thumbnail_height():
    app = QApplication.instance() or QApplication([])
    pages, config = setup((12000,), auto_count=True, minimum_gap=100)
    settings = chapter_watermark_settings(pages, config)["0.png"]
    canvas = CanvasView()
    thumbnail = QPixmap(400, 600)
    thumbnail.fill()
    canvas.set_pixmap(thumbnail, (800, 12000), preview=True)
    canvas.set_watermark(settings, True, "0.png")
    actual = [(round(item.x()), round(item.y())) for item in canvas._watermark_items]
    assert actual == settings["positions"]
    assert len(set(actual)) == len(actual)
    canvas.close()


@pytest.mark.parametrize("manual", [
    [[676, 550], [676, 550], [676, 550]],
    [[676, 10], [676, 30]],
    [[676, -100], [676, -200]],
    [[676, 10000], [676, 11000]],
])
def test_corrupted_saved_positions_are_replanned_without_mutating_project(manual):
    pages, config = setup((6000,), auto_count=True, minimum_gap=48,
                          page_positions={"0.png": manual})
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert len(points) == 3
    assert_gap(points, 1000)
    assert config["page_positions"]["0.png"] == manual


def test_large_negative_offset_does_not_push_all_marks_to_top():
    pages, config = setup((12000,), auto_count=True, offset_y=-20000, minimum_gap=48)
    points = flattened(pages, chapter_watermark_settings(pages, config))
    assert len(points) == 6
    assert_gap(points, 1000)


def test_selecting_watermark_does_not_convert_automatic_positions_to_manual():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    pages, config = setup((1000,), repeat_count=1)
    canvas = CanvasView()
    pixmap = QPixmap(800, 1000)
    pixmap.fill()
    canvas.set_pixmap(pixmap)
    canvas.set_watermark(chapter_watermark_settings(pages, config)["0.png"], True, "0.png")
    canvas.resize(800, 800)
    canvas.show()
    canvas.fit_image()
    app.processEvents()
    changes = []
    canvas.watermark_positions_changed.connect(changes.append)
    item = canvas._watermark_items[0]
    center = item.mapToScene(item.boundingRect().center())
    QTest.mouseClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, canvas.mapFromScene(center))
    assert item.isSelected()
    assert changes == []
    canvas.close()
