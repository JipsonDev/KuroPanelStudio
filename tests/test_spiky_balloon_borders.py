"""Recovering missed lettering must never reintroduce the impact crown."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from core.cleaning_manager import CleaningManager


def radial_balloon(spikes):
    # A cropped thought balloon: white gaps separate the radial strokes, and
    # the background remains connected to the image's top and bottom edges.
    rgb = np.full((400, 700, 3), 255, np.uint8)
    rgb[:, 630:] = (80, 105, 135)
    crown = np.zeros(rgb.shape[:2], np.uint8)
    for angle in np.linspace(0, 2 * np.pi, spikes, endpoint=False):
        inner = np.array([340 + 280 * np.cos(angle), 190 + 220 * np.sin(angle)])
        outer = np.array([340 + 330 * np.cos(angle), 190 + 265 * np.sin(angle)])
        normal = np.array([-np.sin(angle), np.cos(angle)]) * 2.2
        cv2.fillPoly(crown, [np.array([outer - normal, outer + normal, inner], np.int32)],
                     255, cv2.LINE_AA)
    rgb[crown > 0] = 255 - crown[crown > 0, None]
    glyphs = np.zeros_like(crown)
    cv2.putText(glyphs, "KEEP THE BORDER", (150, 175), cv2.FONT_HERSHEY_SIMPLEX,
                .85, 255, 2, cv2.LINE_AA)
    cv2.putText(glyphs, "CLEAN THE TEXT!", (170, 230), cv2.FONT_HERSHEY_SIMPLEX,
                .85, 255, 2, cv2.LINE_AA)
    rgb[glyphs > 0] = 255 - glyphs[glyphs > 0, None]
    return rgb, crown, glyphs


@pytest.mark.parametrize("spikes", [100, 140, 180])
def test_automatic_clean_preserves_disconnected_radial_strokes(tmp_path, monkeypatch, spikes):
    rgb, crown, glyphs = radial_balloon(spikes)
    source = tmp_path / "thought.png"
    Image.fromarray(rgb).save(source)
    manager = CleaningManager(tmp_path)

    def locate(crop):
        # Replace only the ONNX inference with known correct text locations.
        # The real geometry/recovery/export-mask pipeline still executes.
        assert crop.shape == (330, 700, 3)
        seed = np.zeros((400, 700), np.uint8)
        seed[150:180, 145:585] = 255
        seed[205:235, 165:570] = 255
        return seed[20:350].copy()

    monkeypatch.setattr(manager, "_text_mask", locate)
    monkeypatch.setattr(manager, "_lama", lambda *_: pytest.fail("A flat balloon needs no inpainting"))
    plan = manager.prepare_masks(source, [dict(x=15, y=100, width=665, height=170)],
                                 lambda _: None, lambda: False)
    assert plan["solid_entries"] == 1
    result = manager.clean_prepared(source, plan, lambda _: None, lambda: False)
    cleaned = rgb.copy()
    mask = np.zeros_like(crown)
    for patch in result["patches"]:
        x, y = patch["x"], patch["y"]
        height, width = patch["mask"].shape
        mask[y:y + height, x:x + width] |= patch["mask"]
        np.copyto(cleaned[y:y + height, x:x + width], patch["pixels"],
                  where=(patch["mask"] > 0)[:, :, None])
    assert not np.any(mask[crown > 0]), "Automatic glyph recovery selected the balloon crown"
    np.testing.assert_array_equal(cleaned[crown > 0], rgb[crown > 0])
    assert np.all(cleaned[glyphs > 0] == 255), "Protecting the crown must still remove the complete text"
    assert not np.any(mask[:, 630:]), "The illustration beside the balloon must remain intact"


@pytest.mark.parametrize("box", [(20, 130, 635, 230), (0, 0, 692, 399)])
def test_user_radial_balloon_example_preserves_all_artwork(box):
    source = Path("local-builds/spiky-balloons/source.png")
    if not source.exists():
        pytest.skip("The user's example is intentionally kept outside the repository")
    model = Path("Models/ocr.onnx")
    if not model.is_file() or model.stat().st_size <= 1000:
        pytest.skip("The real OCR model is unavailable")
    rgb = np.asarray(Image.open(source).convert("RGB"))
    text_area = np.zeros(rgb.shape[:2], bool)
    text_area[154:340, 108:575] = True
    x, y, width, height = box
    manager = CleaningManager(Path("Models"))
    manager.set_device_mode("cpu")
    plan = manager.prepare_masks(source, [dict(x=x, y=y, width=width, height=height)],
                                 lambda _: None, lambda: False)
    result = manager.clean_prepared(source, plan, lambda _: None, lambda: False)
    cleaned = rgb.copy()
    for patch in result["patches"]:
        px, py = patch["x"], patch["y"]
        ph, pw = patch["mask"].shape
        np.copyto(cleaned[py:py + ph, px:px + pw], patch["pixels"],
                  where=(patch["mask"] > 0)[:, :, None])
    np.testing.assert_array_equal(cleaned[~text_area], rgb[~text_area])
    glyphs = text_area & (np.min(rgb, axis=2) < 140)
    assert np.all(cleaned[glyphs] >= 250)
