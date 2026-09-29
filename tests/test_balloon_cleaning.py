"""Geometry and pixel-preservation regressions using deterministic artwork."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from core.balloon_geometry import enclosed_balloon_mask
from core.balloon_typesetter import detect_balloon_interior
from core.cleaning_manager import CleaningManager
from core.detection_manager import DetectionManager, TextRegion, _OnnxTextDetector


def thin_balloon(fill=(255, 255, 255), ink=(0, 0, 0)):
    rgb = np.full((240, 360, 3), 255, np.uint8)
    interior = np.zeros(rgb.shape[:2], np.uint8)
    cv2.ellipse(interior, (180, 120), (145, 95), 0, 0, 360, 255, -1)
    rgb[interior > 0] = fill
    cv2.ellipse(rgb, (180, 120), (145, 95), 0, 0, 360, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(rgb, "DIALOGUE", (70, 115), cv2.FONT_HERSHEY_SIMPLEX, 1.2, ink, 4, cv2.LINE_AA)
    cv2.putText(rgb, "TEXT!", (125, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.1, ink, 3, cv2.LINE_AA)
    return rgb, interior


@pytest.mark.parametrize("fill,ink", [((255, 255, 255), (0, 0, 0)),
                                     ((235, 205, 150), (20, 20, 20)),
                                     ((35, 35, 35), (255, 255, 255))])
def test_thin_outline_on_white_page_stays_inside_balloon(fill, ink):
    rgb, interior = thin_balloon(fill, ink)
    mask = detect_balloon_interior(rgb, padding=6)
    assert np.count_nonzero((mask > 0) & (interior == 0)) == 0
    assert np.count_nonzero(mask) > np.count_nonzero(interior) * .65
    assert mask[120, 180] == 255  # letters are filled geometry, not obstacles


def test_enclosed_detection_does_not_turn_plain_page_into_balloon():
    rgb = np.full((240, 360, 3), 255, np.uint8)
    assert enclosed_balloon_mask(rgb) is None


def test_small_gap_in_outline_does_not_leak_into_page():
    rgb, interior = thin_balloon()
    rgb[24:28, 180:182] = 255
    mask = detect_balloon_interior(rgb, padding=6)
    assert np.count_nonzero((mask > 0) & (interior == 0)) == 0


def test_text_selection_cannot_claim_two_separate_balloons():
    rgb = np.full((280, 260, 3), 255, np.uint8)
    for y in (75, 205):
        cv2.ellipse(rgb, (130, y), (110, 55), 0, 0, 360, (0, 0, 0), 2)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[50:100, 70:190] = 255
    selection[180:230, 70:190] = 255
    assert enclosed_balloon_mask(rgb, selection) is None


def test_lines_in_one_balloon_join_across_real_line_spacing():
    rgb, _ = thin_balloon()
    first = TextRegion(95, 90, 175, 25, .9)
    second = TextRegion(120, 140, 120, 25, .85)
    with Image.fromarray(rgb) as image:
        result = DetectionManager._merge_same_balloon_lines([second, first], image)
    assert len(result) == 1
    assert (result[0].x, result[0].y, result[0].width, result[0].height) == (95, 90, 175, 75)


@pytest.mark.parametrize("box,expected", [([60, 81, 252, 169], 1), ([0, 0, 359, 239], 0)])
def test_large_detected_text_requires_balloon_support(tmp_path, monkeypatch, box, expected):
    rgb, _ = thin_balloon()
    source = tmp_path / "balloon.png"
    Image.fromarray(rgb).save(source)
    model = _OnnxTextDetector.__new__(_OnnxTextDetector)
    model.predict_boxes = lambda *_: [[(np.array(box), .8)]]
    manager = DetectionManager(tmp_path)
    monkeypatch.setattr(manager, "_load_model", lambda: model)
    result = manager.detect(source, lambda _: None, lambda: False)
    assert len(result) == expected


def test_touching_text_boxes_in_separate_closed_balloons_remain_separate():
    rgb = np.full((220, 240, 3), 255, np.uint8)
    cv2.rectangle(rgb, (55, 30), (185, 100), (0, 0, 0), 2)
    cv2.rectangle(rgb, (55, 104), (185, 180), (0, 0, 0), 2)
    first = TextRegion(80, 60, 80, 40, .9)
    second = TextRegion(80, 104, 80, 40, .85)
    assert DetectionManager._are_adjacent_lines(first, second)
    with Image.fromarray(rgb) as image:
        result = DetectionManager._merge_same_balloon_lines([first, second], image)
    assert len(result) == 2


def neighbouring_balloons():
    rgb = np.full((240, 600, 3), 60, np.uint8)
    interiors = np.zeros(rgb.shape[:2], np.uint8)
    glyphs = np.zeros_like(interiors)
    for x in (150, 450):
        cv2.ellipse(interiors, (x, 120), (130, 95), 0, 0, 360, 255, -1)
        cv2.putText(glyphs, "TEXT", (x - 70, 125), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 3, cv2.LINE_AA)
    rgb[interiors > 0] = 250
    rgb[glyphs > 0] = (15, 15, 15)
    # An illustration between balloons must not be interpreted as a glyph.
    cv2.putText(rgb, "!", (287, 145), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (220, 50, 80), 4, cv2.LINE_AA)
    selection = np.zeros_like(interiors)
    selection[8:-8, 8:-8] = 255
    return rgb, interiors, glyphs, selection


def test_solid_cleaning_keeps_neighbouring_balloons_separate():
    rgb, interiors, glyphs, selection = neighbouring_balloons()
    mask, fill = CleaningManager._solid_balloon_text_mask(rgb, selection)
    assert fill is not None
    assert np.count_nonzero((mask > 0) & (interiors == 0)) == 0
    assert np.count_nonzero((mask > 0) & (glyphs > 0)) / np.count_nonzero(glyphs) > .95


def test_solid_cleaning_preserves_concave_silhouette():
    rgb, interiors, glyphs, selection = neighbouring_balloons()
    # Cut an inward notch into a balloon without reaching the lettering.
    cv2.rectangle(interiors, (200, 15), (290, 75), 0, -1)
    rgb[(interiors == 0) & (glyphs == 0)] = (60, 60, 60)
    mask, fill = CleaningManager._solid_balloon_text_mask(rgb, selection)
    assert fill is not None
    assert np.count_nonzero((mask > 0) & (interiors == 0)) == 0


def test_whole_page_cleaning_preserves_artwork_between_balloons(tmp_path):
    rgb, interiors, glyphs, _ = neighbouring_balloons()
    source = tmp_path / "neighbours.png"
    Image.fromarray(rgb).save(source)
    manager = CleaningManager(tmp_path)
    region = dict(x=0, y=0, width=rgb.shape[1], height=rgb.shape[0])
    plan = manager.prepare_masks(source, [region], lambda _: None, lambda: False)
    assert plan["solid_entries"] == 1
    result = manager.clean_prepared(source, plan, lambda _: None, lambda: False)
    assert len(result["patches"]) == 1
    cleaned = result["patches"][0]["pixels"]
    np.testing.assert_array_equal(cleaned[interiors == 0], rgb[interiors == 0])
    assert np.all(cleaned[glyphs > 0] == 250)


def test_overlapping_cleaning_patches_preserve_both_results(tmp_path):
    source = tmp_path / "page.png"
    original = np.full((70, 100, 3), 40, np.uint8)
    Image.fromarray(original).save(source)
    first = np.zeros((20, 40), np.uint8)
    first[:, 5:15] = 255
    second = np.zeros_like(first)
    second[:, 25:35] = 255
    target = dict(x=0, y=0, width=100, height=70)
    plan = dict(targets=[target], entries=[
        dict(x=20, y=20, mask=first, method="solid_fill", fill_color=[250, 240, 230], target=target),
        dict(x=25, y=20, mask=second, method="solid_fill", fill_color=[230, 240, 250], target=target),
    ])
    result = CleaningManager(tmp_path).clean_prepared(source, plan, lambda _: None, lambda: False)
    for patch in result["patches"]:
        np.testing.assert_array_equal(patch["pixels"][25, 28], [250, 240, 230])
        np.testing.assert_array_equal(patch["pixels"][25, 52], [230, 240, 250])
        np.testing.assert_array_equal(patch["pixels"][patch["mask"] == 0], original[patch["mask"] == 0])
