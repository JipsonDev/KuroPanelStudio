"""Export must preserve the visible composition, beyond sharing line strings."""
import copy
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from scipy.ndimage import distance_transform_edt
from PIL import Image, ImageDraw
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from core.export_manager import _draw_region_text, export_page, _installed_windows_fonts
from core.project_manager import Page
from core.typography_manager import DEFAULT_STYLE
from ui.canvas_view import CanvasView


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    font = Path("C:/Windows/Fonts/arial.ttf")
    if not font.is_file():
        pytest.skip("Comparison needs the same installed font in Qt and Pillow")
    font_id = QFontDatabase.addApplicationFont(str(font))
    assert font_id >= 0
    yield application
    QFontDatabase.removeApplicationFont(font_id)


def canvas_page(style=None, text="HMPH, PARECE QUE LOS\nDE UN MORDISCO.",
                box=(50, 40, 700, 300), manual=True, source=None):
    canvas = CanvasView()
    base = QImage(800, 400, QImage.Format_RGB888)
    base.fill(Qt.white)
    if source is not None:
        pixels = source.convert("RGB").tobytes()
        base = QImage(pixels, 800, 400, 800 * 3, QImage.Format_RGB888).copy()
    canvas.set_image(base)
    region = dict(id="one", x=box[0], y=box[1], width=box[2], height=box[3],
                  applied_text=text, typeset_box_manual=manual,
                  style={**DEFAULT_STYLE, "font_family": "Arial",
                         "font_file": "C:/Windows/Fonts/arial.ttf",
                         "font_size": 24, "auto_fit": False,
                         "optical_center": False, **(style or {})})
    item = canvas._make_text_item(text, region)
    for other in canvas.scene.items():
        if other is not item and other.parentItem() is not item:
            other.setVisible(False)
    rendered = QImage(800, 400, QImage.Format_RGB888)
    rendered.fill(Qt.white)
    painter = QPainter(rendered)
    canvas.scene.render(painter, QRectF(0, 0, 800, 400), QRectF(0, 0, 800, 400), Qt.IgnoreAspectRatio)
    painter.end()
    pixels = canvas._qimage_rgb_array(rendered).copy()
    canvas.close()
    return region, pixels


def ink_rows(rgb):
    mask = np.any(rgb < 128, axis=2)
    rows = np.flatnonzero(np.any(mask, axis=1))
    # Detached accents belong to the same text row as their capital letter.
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > 4) + 1)
    boxes = []
    for group in groups:
        if not len(group):
            continue
        columns = np.flatnonzero(np.any(mask[group], axis=0))
        boxes.append([columns[0], group[0], columns[-1] + 1, group[-1] + 1])
    return np.asarray(boxes)


def assert_same_composition(preview, exported):
    expected, actual = np.any(preview < 128, axis=2), np.any(exported < 128, axis=2)
    assert expected.any() and actual.any()
    # Compare all glyph pixels in both directions. Tall fallback accents can
    # join two rows; counting disconnected ink rows misidentifies those lines.
    assert distance_transform_edt(~actual)[expected].max() <= 4
    assert distance_transform_edt(~expected)[actual].max() <= 4


@pytest.mark.parametrize("balloon", [False, True])
@pytest.mark.parametrize("alignment", ["left", "center", "right"])
def test_export_keeps_canvas_font_size_line_spacing_and_alignment(app, balloon, alignment):
    region, preview = canvas_page(dict(balloon_fit=balloon, alignment=alignment))
    image = Image.new("RGB", (800, 400), "white")
    # A saved project must retain the render measurements too.
    _draw_region_text(image, json.loads(json.dumps(region)), {})
    assert_same_composition(preview, np.asarray(image))


def test_export_keeps_scaled_text_and_optical_center(app):
    region, preview = canvas_page(dict(scale_x=85, scale_y=120, optical_center=True))
    image = Image.new("RGB", (800, 400), "white")
    _draw_region_text(image, copy.deepcopy(region), {})
    assert_same_composition(preview, np.asarray(image))


def test_moving_a_composed_box_moves_export_without_recomposing(app):
    region, preview = canvas_page()
    region["x"] += 20
    region["y"] += 15
    image = Image.new("RGB", (800, 400), "white")
    _draw_region_text(image, region, {})
    expected = ink_rows(preview) + np.asarray([20, 15, 20, 15])
    assert np.max(np.abs(ink_rows(np.asarray(image)) - expected)) <= 4


@pytest.mark.parametrize("format_name", ["PNG", "JPG", "WEBP", "PSD"])
def test_saved_page_keeps_preview_composition(app, tmp_path, format_name):
    region, preview = canvas_page()
    source = tmp_path / "source.png"
    Image.new("RGB", (800, 400), "white").save(source)
    page = Page("source.png", source, 800, 400)
    destination = tmp_path / ("result." + format_name.lower())
    export_page(destination, page, [region], None, {}, format_name)
    with Image.open(destination) as image:
        assert_same_composition(preview, np.asarray(image.convert("RGB")))


def test_changed_style_invalidates_previous_render_measurements(app):
    region, preview = canvas_page(dict(balloon_fit=False), text="HMPH")
    region["style"]["font_size"] = 32
    image = Image.new("RGB", (800, 400), "white")
    _draw_region_text(image, region, {})
    old, new = ink_rows(preview)[0], ink_rows(np.asarray(image))[0]
    assert new[2] - new[0] > old[2] - old[0] + 10


def test_original_five_line_dialogue_matches_canvas(app):
    text = ("HMPH, PARECE QUE LOS\nMÚSCULOS DE TU MANDÍBULA SE HAN\n"
            "RECUPERADO BASTANTE BIEN... HASTA\nME HICISTE SANGRAR LA LENGUA\nDE UN MORDISCO.")
    region, preview = canvas_page(dict(font_size=22), text=text)
    image = Image.new("RGB", (800, 400), "white")
    _draw_region_text(image, region, {})
    assert len(ink_rows(preview)) == 5
    assert_same_composition(preview, np.asarray(image))


def test_psd_keeps_full_balloon_text_outside_the_initial_ocr_box(app, tmp_path):
    source = Image.new("RGB", (800, 400), "#606060")
    ImageDraw.Draw(source).ellipse((40, 20, 760, 380), fill="white", outline="black", width=3)
    region, preview = canvas_page(dict(auto_fit=True), box=(360, 175, 80, 40), manual=False, source=source)
    path = tmp_path / "source.png"
    source.save(path)
    page = Page("source.png", path, 800, 400)
    destination = tmp_path / "result.psd"
    export_page(destination, page, [region], None, {}, "PSD")
    with Image.open(destination) as image:
        pixels = np.asarray(image.convert("RGB"))
    changed = np.any(pixels != np.asarray(source), axis=2)
    isolated = np.where(changed[:, :, None], pixels, 255).astype(np.uint8)
    assert_same_composition(preview, isolated)


@pytest.mark.parametrize("family", ["anime ace 2.0 bb", "10centcomics", "cc astro city int"])
def test_installed_manga_font_preserves_line_width(app, family):
    paths = [path for name, path in _installed_windows_fonts() if name.startswith(family + " (")]
    if not paths:
        pytest.skip("Optional user-installed manga font is absent")
    font_id = QFontDatabase.addApplicationFont(str(paths[0]))
    try:
        selected_family = QFontDatabase.applicationFontFamilies(font_id)[0]
        text = ("HMPH, PARECE QUE LOS\nMÚSCULOS DE TU MANDÍBULA SE HAN\n"
                "RECUPERADO BASTANTE BIEN... HASTA\nME HICISTE SANGRAR LA LENGUA\nDE UN MORDISCO.")
        region, preview = canvas_page(dict(font_family=selected_family, font_file=str(paths[0]), font_size=22), text=text)
        image = Image.new("RGB", (800, 400), "white")
        _draw_region_text(image, region, {})
        assert_same_composition(preview, np.asarray(image))
    finally:
        QFontDatabase.removeApplicationFont(font_id)


def test_export_preserves_typography_in_background_worker(app):
    from concurrent.futures import ThreadPoolExecutor

    region, preview = canvas_page(dict(alignment="right", line_spacing=5))
    saved_region = json.loads(json.dumps(region))

    def export_in_worker():
        image = Image.new("RGB", (800, 400), "white")
        _draw_region_text(image, saved_region, {})
        return np.asarray(image)

    with ThreadPoolExecutor(max_workers=1) as executor:
        exported = executor.submit(export_in_worker).result(timeout=10)
    assert_same_composition(preview, exported)


@pytest.mark.parametrize("effect", [
    {"stroke_width": 3, "stroke_color": "#202020", "text_color": "#111111"},
    {"gradient_enabled": True, "gradient_start": "#111111", "gradient_end": "#444444",
     "gradient_angle": 35, "underline": True},
])
def test_export_keeps_outlined_and_decorated_glyphs(app, effect):
    region, preview = canvas_page(effect)
    image = Image.new("RGB", (800, 400), "white")
    _draw_region_text(image, region, {})
    assert_same_composition(preview, np.asarray(image))
