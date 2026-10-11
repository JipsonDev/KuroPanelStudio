"""Faint compression fringes must be cleaned without admitting nearby line art."""
import cv2
import numpy as np
import pytest

from core.glyph_completion import complete_soft_text_halos
from core.cleaning_manager import CleaningManager


@pytest.mark.parametrize("background, fringe", [
    ((255, 255, 255), (245, 245, 245)),
    ((240, 220, 190), (232, 207, 184)),
    ((25, 25, 25), (35, 35, 35)),
])
def test_soft_fringe_and_detached_compression_specks_are_completed(background, fringe):
    rgb = np.full((100, 160, 3), background, np.uint8)
    mask = np.zeros((100, 160), np.uint8)
    mask[35:65, 50:60] = 255
    rgb[mask > 0] = (120, 30, 80)
    rgb[35:65, 60:62] = fringe
    rgb[42:44, 64:66] = fringe
    # The same faint mark, far from a confirmed glyph, is not text evidence.
    rgb[42:44, 110:112] = fringe
    selection = np.zeros_like(mask)
    selection[15:85, 20:140] = 255
    result = complete_soft_text_halos(rgb, mask, selection)
    assert np.all(result[35:65, 60:62] > 0)
    assert np.all(result[42:44, 64:66] > 0)
    assert not np.any(result[42:44, 110:112])
    assert np.all(result[mask > 0] > 0)


def test_soft_completion_does_not_admit_long_line_touching_a_letter():
    rgb = np.full((100, 160, 3), 255, np.uint8)
    mask = np.zeros((100, 160), np.uint8)
    mask[35:60, 60:70] = 255
    rgb[mask > 0] = 0
    rgb[61:63, 25:140] = 235
    selection = np.zeros_like(mask)
    selection[15:85, 20:145] = 255
    result = complete_soft_text_halos(rgb, mask, selection)
    np.testing.assert_array_equal(result[61:63], mask[61:63])


def test_soft_completion_rejects_screentone_and_empty_seed():
    y, x = np.indices((100, 160))
    rgb = np.repeat(np.where((x + y) % 4 < 2, 220, 255).astype(np.uint8)[..., None], 3, axis=2)
    selection = np.full((100, 160), 255, np.uint8)
    mask = np.zeros_like(selection)
    np.testing.assert_array_equal(complete_soft_text_halos(rgb, mask, selection), mask)
    mask[35:65, 50:60] = 255
    np.testing.assert_array_equal(complete_soft_text_halos(rgb, mask, selection), mask)


def test_soft_completion_preserves_selection_edge_and_high_contrast_art():
    rgb = np.full((100, 160, 3), 255, np.uint8)
    mask = np.zeros((100, 160), np.uint8)
    mask[35:65, 50:60] = 255
    rgb[mask > 0] = 0
    rgb[40:44, 62:66] = (80, 120, 140)
    selection = np.zeros_like(mask)
    selection[15:85, 20:140] = 255
    mask[18:30, 50:60] = 255
    rgb[15:18, 50:60] = 245
    result = complete_soft_text_halos(rgb, mask, selection)
    assert not np.any(result[40:44, 62:66])
    assert not np.any(result[15:18, 50:60])


def test_background_estimation_excludes_already_masked_dense_text():
    rgb = np.full((100, 160, 3), 255, np.uint8)
    mask = np.zeros((100, 160), np.uint8)
    mask[10:90, 20:130] = 255
    rgb[mask > 0] = 0
    rgb[40:44, 131:133] = 245
    selection = np.full_like(mask, 255)
    result = complete_soft_text_halos(rgb, mask, selection)
    assert np.all(result[40:44, 131:133] > 0)


def test_gradient_correction_does_not_flatten_illustration_crossing_text():
    xx = np.linspace(225, 250, 180, dtype=np.float32)[None, :, None]
    background = np.repeat(np.repeat(xx, 100, axis=0), 3, axis=2).astype(np.uint8)
    cv2.line(background, (20, 20), (160, 80), (100, 120, 130), 9)
    mask = np.zeros((100, 180), np.uint8)
    mask[42:58, 45:135] = 255
    original = background.copy()
    original[mask > 0] = 0  # text obscures the known drawing
    # Ideal neural reconstruction: keep the line through the removed text.
    guided, used = CleaningManager._gradient_guided_restoration(original, background, mask)
    assert not used
    np.testing.assert_array_equal(guided, background)
