"""Visible logo size and chapter rhythm must survive padding and file cuts."""
from io import BytesIO
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
import pytest
from PySide6.QtWidgets import QApplication

from core.chapter_watermarks import chapter_watermark_settings
from core.watermark_manager import prepare_watermark, watermark_positions
from ui.watermark_dialog import WatermarkDialog


def logo(padded=True, empty=False):
    image = Image.new("RGBA", (500, 300) if padded else (100, 50), (0, 0, 0, 0))
    if not empty:
        image.paste((255, 0, 0, 255), (200, 120, 300, 170) if padded else (0, 0, 100, 50))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def settings(**changes):
    return {**dict(png_bytes=logo(), size_mode="pixels", width_px=100, opacity=100,
                   repeat=True, auto_count=True, minimum_gap=100), **changes}


def test_visible_logo_not_transparent_canvas_defines_size_and_anchor():
    config = settings(anchor="bottom-right", repeat=False)
    mark = prepare_watermark((800, 1000), config)
    assert mark.size == (100, 50)
    assert mark.getbbox() == (0, 0, 100, 50)
    assert watermark_positions((800, 1000), mark.size, config) == [(676, 926)]


def test_transparent_padding_can_be_preserved_explicitly():
    mark = prepare_watermark((800, 1000), settings(trim_transparent=False))
    assert mark.size == (100, 60)


def test_fully_transparent_png_does_not_create_invisible_marks():
    assert prepare_watermark((800, 1000), dict(png_bytes=logo(empty=True))) is None


def test_short_page_does_not_shrink_logo_inside_chapter():
    pages = [dict(name="short", width=800, height=70), dict(name="long", width=800, height=3930)]
    config = {**settings(), "enabled_pages": ["short", "long"], "auto_count": False, "repeat_count": 4}
    plan = chapter_watermark_settings(pages, config)
    marks = [prepare_watermark((p["width"], p["height"]), plan[p["name"]]) for p in pages]
    assert [m.size for m in marks] == [(100, 50), (100, 50)]


def test_page_too_short_for_logo_does_not_get_a_miniature():
    pages = [dict(name="short", width=800, height=30), dict(name="long", width=800, height=3970)]
    config = {**settings(), "enabled_pages": ["short", "long"]}
    plan = chapter_watermark_settings(pages, config)
    assert plan["short"]["positions"] == []
    assert prepare_watermark((800, 30), plan["short"]).size == (100, 50)


@pytest.mark.parametrize("interval,count", [(1000, 10), (2500, 4), (5000, 2)])
def test_automatic_interval_controls_chapter_density_across_seams(interval, count):
    pages = [dict(name=str(i), width=800, height=h) for i, h in enumerate((2300, 1700, 6000))]
    config = {**settings(interval_px=interval), "enabled_pages": [p["name"] for p in pages]}
    plan = chapter_watermark_settings(pages, config)
    offset = 0
    points = []
    for page in pages:
        points.extend(offset + y for _, y in plan[page["name"]]["positions"])
        offset += page["height"]
    assert len(points) == count
    assert all(b - a == pytest.approx(interval, abs=1) for a, b in zip(points, points[1:]))


def test_page_automatic_density_also_uses_selected_interval():
    config = settings(interval_px=1000, seam_safe=False)
    assert len(watermark_positions((800, 6000), (100, 50), config)) == 6


def test_dialog_exposes_size_and_actual_dimensions():
    app = QApplication.instance() or QApplication([])
    dialog = WatermarkDialog(settings(interval_px=2500))
    dialog.resize(500, 900)
    dialog.show()
    app.processEvents()
    assert not dialog.advanced.isAncestorOf(dialog.scale_percent)
    assert not dialog.advanced.isAncestorOf(dialog.anchor_buttons["center"])
    assert dialog.interval_px.value() == 2500
    dialog.set_preview_info((800, 1000), settings(), 4)
    assert "100 × 50" in dialog.size_summary.text()
    assert "4" in dialog.plan_summary.text()
    dialog.auto_count.setChecked(False)
    assert not dialog.interval_px.isEnabled()
    dialog.close()


def test_adaptive_distribution_recovers_marks_on_free_side_without_piling_up():
    pages = [dict(name="page", width=800, height=6000,
                  regions=[dict(x=600, y=0, width=200, height=6000)])]
    config = {**settings(distribution="adaptive"), "enabled_pages": ["page"]}
    plan = chapter_watermark_settings(pages, config)
    points = plan["page"]["positions"]
    assert len(points) == 3
    assert all(x < 400 for x, y in points)
    assert [y for x, y in points] == [975, 2975, 4975]
    assert watermark_positions((800, 6000), (100, 50), config | {"avoid_regions": pages[0]["regions"]}) == points


def test_new_watermark_guidance_has_english_translations():
    from ui.i18n import translate_text

    assert translate_text("Medir solo el logo visible", "en") == "Measure visible logo only"
    assert translate_text("Tamaño final: 100 × 50 px · página 800 × 1000 px", "en") == "Final size: 100 × 50 px · page 800 × 1000 px"
    assert translate_text("4 marca(s) en el capítulo. Las zonas sin espacio pueden quedar sin marca.", "en").startswith("4 watermark(s)")


def test_dialog_translates_distribution_without_changing_saved_mode():
    from ui.i18n import UiTranslator

    app = QApplication.instance() or QApplication([])
    dialog = WatermarkDialog(settings(distribution="adaptive"))
    translator = UiTranslator(app, "en")
    try:
        translator.refresh(dialog)
        assert dialog.distribution.currentText() == "Adapt to free space"
        assert dialog.values()["distribution"] == "adaptive"
        assert dialog.interval_px.specialValueText() == "Automatic"
        translator.set_language("es")
        translator.refresh(dialog)
        assert dialog.distribution.currentText() == "Adaptar al espacio libre"
    finally:
        app.removeEventFilter(translator)
        translator.deleteLater()
        dialog.close()


@pytest.mark.parametrize("rotation", [0, 30, 90])
def test_visible_padded_logo_matches_canvas_and_export_after_rotation(rotation):
    import numpy as np
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from core.watermark_manager import compose_watermark
    from ui.canvas_view import CanvasView

    app = QApplication.instance() or QApplication([])
    size = (800, 3000)
    config = settings(rotation=rotation, distribution="adaptive", interval_px=1000, opacity=65)
    config["enabled_pages"] = ["page"]
    plan = chapter_watermark_settings([dict(name="page", width=800, height=3000)], config)["page"]
    source = QImage(*size, QImage.Format_RGB888); source.fill(Qt.white)
    canvas = CanvasView(); canvas.set_image(source)
    canvas.set_watermark(plan, True, "page")
    preview = QImage(*size, QImage.Format_RGB888); preview.fill(Qt.white)
    painter = QPainter(preview)
    canvas.scene.render(painter, QRectF(0, 0, *size), QRectF(0, 0, *size), Qt.IgnoreAspectRatio)
    painter.end()
    exported = Image.new("RGB", size, "white")
    assert compose_watermark(exported, plan)
    assert len(canvas._watermark_items) == 3
    assert np.abs(canvas._qimage_rgb_array(preview).astype(int) - np.asarray(exported).astype(int)).max() <= 1
    canvas.close()


def test_window_refresh_reports_resolved_logo_and_full_chapter_count():
    from types import SimpleNamespace
    from core.project_manager import Page
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    page = Page("page", width=800, height=6000)
    config = {**settings(), "enabled_pages": ["page"]}
    plan = chapter_watermark_settings([dict(name="page", width=800, height=6000)], config)
    dialog = WatermarkDialog(config)
    canvas = SimpleNamespace(_watermark_items=[1, 2, 3], set_watermark=lambda *_: None)
    window = SimpleNamespace(project=SimpleNamespace(pages=[page], active_page=page),
                             watermark_settings=config, watermark_dialog=dialog,
                             canvas_shell=SimpleNamespace(canvas=canvas),
                             _chapter_watermarks=lambda **_: plan)
    MainWindow._refresh_watermark_preview(window)
    assert "100 × 50" in dialog.size_summary.text()
    assert "3 marca(s)" in dialog.plan_summary.text()
    dialog.close()
