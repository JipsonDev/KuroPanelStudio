"""Moved boxes near/outside page edges must not stop chapter export."""
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from core.balloon_typesetter import fit_balanced_text, text_layout_geometry
from core.export_manager import export_page
from core.project_manager import Page


@pytest.mark.parametrize("box", [(-12, 30, 100, 50), (30, -12, 100, 50),
                                (160, 30, 100, 50), (30, 160, 100, 50)])
def test_partial_page_box_keeps_requested_layout_shape(box):
    rgb = np.full((180, 180, 3), 255, np.uint8)
    rect, mask = text_layout_geometry(rgb, box, 8, True)
    assert rect == box
    assert mask.shape == (box[3], box[2])
    layout = fit_balanced_text("Hola mundo", mask, 20, 6, True,
                               lambda size: size, lambda size, text: size * len(text) * .5)
    assert not layout["overflow"]


@pytest.mark.parametrize("shape", [(0, 0), (50, 0), (0, 50), (50, 100)])
def test_empty_layout_is_an_overflow_instead_of_an_argmax_exception(shape):
    layout = fit_balanced_text("Conservar texto", np.zeros(shape, np.uint8), 20, 6, True,
                               lambda size: size, lambda size, text: size * len(text) * .5)
    assert layout["overflow"]
    assert layout["text"] == "Conservar texto"
    assert layout["intervals"] == []


@pytest.mark.parametrize("format_name", ["PNG", "JPG", "WEBP", "PSD"])
def test_image_export_survives_partial_and_fully_off_page_text(tmp_path, format_name):
    source = tmp_path / "page.png"
    Image.new("RGB", (180, 180), "white").save(source)
    page = Page("page.png", source, 180, 180)
    style = dict(balloon_fit=True, auto_fit=True, font_size=20, stroke_width=0)
    regions = [dict(id="edge", x=-12, y=30, width=100, height=60,
                    applied_text="Hola mundo", style=style, typeset_box_manual=True)]
    destination = tmp_path / f"result.{format_name.lower()}"
    export_page(destination, page, deepcopy(regions), None, {}, format_name)
    with Image.open(destination) as image:
        assert image.size == (180, 180)
        assert np.any(np.asarray(image.convert("RGB")) < 200)
    regions.append(dict(id="outside", x=1000, y=1000, width=100, height=60,
                        applied_text="Fuera", style=style, typeset_box_manual=True))
    export_page(destination, page, regions, None, {}, format_name)
    assert destination.is_file()
