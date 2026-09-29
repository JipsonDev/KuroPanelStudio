"""Tight text boxes must not leave the tops and bottoms of letters behind."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import pytest

from core.cleaning_manager import CleaningManager
from core.glyph_completion import (
    complete_anchored_glyph_fragments, complete_boundary_glyphs,
)


def clipped_dialogue(fill=(255, 255, 255), ink=(0, 0, 0)):
    rgb = np.full((220, 620, 3), fill, np.uint8)
    glyphs = np.zeros(rgb.shape[:2], np.uint8)
    cv2.putText(glyphs, "DIALOGUE TEXT", (40, 92), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 255, 4, cv2.LINE_AA)
    cv2.putText(glyphs, "HELLO WORLD!", (40, 155), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 255, 4, cv2.LINE_AA)
    rgb[glyphs > 0] = ink
    # Unrelated ornament remains untouched below the dialogue.
    cv2.ellipse(rgb, (5, 219), (50, 18), 0, 180, 350, (185, 155, 200), 2)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[68:143, 35:455] = 255
    seed = cv2.bitwise_and(glyphs, selection)
    return rgb, glyphs, selection, seed


@pytest.mark.parametrize("fill,ink", [((255, 255, 255), (0, 0, 0)), ((236, 220, 201), (140, 35, 60))])
def test_complete_letters_across_tight_box_preserves_ornament(fill, ink):
    rgb, glyphs, selection, seed = clipped_dialogue(fill, ink)
    completed = complete_boundary_glyphs(rgb, seed, selection)
    assert np.count_nonzero((glyphs > 0) & (completed == 0)) == 0
    cleaned = rgb.copy()
    cleaned[completed > 0] = fill
    assert np.all(cleaned[glyphs > 0] == np.array(fill))
    np.testing.assert_array_equal(cleaned[180:], rgb[180:])


def test_completion_rejects_outline_crossing_selection():
    rgb, glyphs, selection, seed = clipped_dialogue()
    cv2.line(rgb, (15, 70), (605, 70), (0, 0, 0), 2)
    baseline = seed.copy()
    completed = complete_boundary_glyphs(rgb, seed, selection)
    assert np.count_nonzero(completed[:, 500:]) == 0
    assert np.count_nonzero(completed[:, :15]) == 0
    assert np.all(completed[baseline > 0] > 0)


def test_completion_does_not_expand_over_textured_art():
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (160, 200, 3), dtype=np.uint8)
    selection = np.zeros((160, 200), np.uint8)
    selection[40:120, 50:150] = 255
    seed = selection.copy()
    np.testing.assert_array_equal(complete_boundary_glyphs(rgb, seed, selection), seed)


def test_nearly_masked_glyph_finishes_without_absorbing_border_art():
    rgb = np.full((180, 240, 3), 255, np.uint8)
    glyph = np.zeros(rgb.shape[:2], np.uint8)
    cv2.putText(glyph, "A", (80, 105), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 255, 6)
    rgb[glyph > 0] = 0
    cv2.line(rgb, (15, 145), (225, 145), (0, 0, 0), 3)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[30:155, 40:200] = 255
    mask = cv2.dilate(glyph, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    x, _, _, _ = cv2.boundingRect(glyph)
    mask[:, x:x + 3] = 0
    assert np.count_nonzero((glyph > 0) & (mask == 0)) > 0

    completed = complete_anchored_glyph_fragments(rgb, mask, selection)

    assert np.count_nonzero((glyph > 0) & (completed == 0)) == 0
    assert np.count_nonzero(completed[140:150, :]) == 0
    assert np.count_nonzero(completed[:, :40]) == 0


def test_reviewed_mask_is_not_clipped_again_when_building_patch(tmp_path):
    rgb, glyphs, selection, seed = clipped_dialogue()
    completed = complete_boundary_glyphs(rgb, seed, selection)
    x, y, w, h = cv2.boundingRect(completed)
    source = tmp_path / "original.png"
    Image.fromarray(rgb).save(source)
    plan = dict(entries=[dict(x=x, y=y, mask=completed[y:y+h, x:x+w],
                             target=dict(x=35, y=68, width=420, height=75),
                             method="solid_fill", fill_color=[255, 255, 255])])
    result = CleaningManager(tmp_path).clean_prepared(source, plan, lambda _: None, lambda: False)
    output = rgb.copy()
    for patch in result["patches"]:
        px, py = patch["x"], patch["y"]
        ph, pw = patch["mask"].shape
        np.copyto(output[py:py+ph, px:px+pw], patch["pixels"], where=(patch["mask"] > 0)[:, :, None])
    assert np.all(output[glyphs > 0] == 255)
    np.testing.assert_array_equal(output[180:], rgb[180:])


def test_preparation_and_cleaning_recover_tight_detected_box(tmp_path):
    rgb, glyphs, _, _ = clipped_dialogue()
    source = tmp_path / "dialogue.png"
    Image.fromarray(rgb).save(source)
    manager = CleaningManager(tmp_path)
    manager._text_mask = lambda crop: np.where(np.min(crop, axis=2) < 80, 255, 0).astype(np.uint8)
    plan = manager.prepare_masks(source, [dict(x=35, y=68, width=420, height=75)], lambda _: None, lambda: False)
    assert plan["entries"]
    assert all(entry["method"] == "solid_fill" for entry in plan["entries"])
    result = manager.clean_prepared(source, plan, lambda _: None, lambda: False)
    output = rgb.copy()
    for patch in result["patches"]:
        x, y = patch["x"], patch["y"]
        h, w = patch["mask"].shape
        np.copyto(output[y:y+h, x:x+w], patch["pixels"], where=(patch["mask"] > 0)[:, :, None])
    assert np.all(output[glyphs > 0] == 255)
    np.testing.assert_array_equal(output[180:], rgb[180:])
