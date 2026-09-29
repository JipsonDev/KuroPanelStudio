"""ATN-inspired connected backgrounds must recover holes without losing outlines."""
import cv2
import numpy as np
import pytest
from PIL import Image
from core.bubble_mask import colour_connected_balloon_mask
from core.cleaning_manager import CleaningManager


def balloon(fill=(255, 255, 255), ink=(0, 0, 0), scale=1):
    rgb = np.full((300, 480, 3), 80, np.uint8)
    inside = np.zeros(rgb.shape[:2], np.uint8)
    cv2.ellipse(inside, (240, 150), (205, 122), 0, 0, 360, 255, -1)
    rgb[inside > 0] = fill
    glyphs = np.zeros_like(inside)
    cv2.putText(glyphs, "DIALOGUE", (95, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 255, 3, cv2.LINE_AA)
    cv2.putText(glyphs, "TEXT", (175, 197), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 255, 3, cv2.LINE_AA)
    # Disconnected fragments outside the main lines mimic incompletely erased
    # punctuation / CJK strokes; the neural locator will not be needed.
    for x, y in ((115, 90), (190, 87), (279, 217), (352, 206)):
        cv2.rectangle(glyphs, (x, y), (x+3, y+2), 255, -1)
    rgb[glyphs > 0] = ink
    selection = np.zeros_like(inside)
    selection[28:273, 35:446] = 255
    if scale != 1:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        glyphs = cv2.resize(glyphs, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        selection = cv2.resize(selection, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        inside = cv2.resize(inside, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    return rgb, glyphs, selection, inside


@pytest.mark.parametrize("fill,ink", [((255, 255, 255), (0, 0, 0)),
                                      ((246, 246, 246), (20, 20, 20)),
                                      ((220, 203, 180), (150, 30, 50)),
                                      ((32, 32, 32), (245, 245, 245))])
@pytest.mark.parametrize("scale", [.5, 1, 2])
def test_connected_fill_recovers_detached_fragments(fill, ink, scale):
    rgb, glyphs, selection, inside = balloon(fill, ink, scale)
    result = colour_connected_balloon_mask(rgb, selection)
    assert result is not None
    mask, colour = result
    assert colour == fill
    assert np.all(mask[glyphs > 0] > 0)
    assert not np.any(mask[inside == 0])
    # Large areas of background are not used as the paint mask.
    assert np.count_nonzero(mask) < np.count_nonzero(inside) * .35


def test_concave_border_and_neighbour_art_are_preserved():
    rgb, glyphs, selection, inside = balloon()
    cv2.rectangle(inside, (345, 35), (479, 108), 0, -1)
    rgb[(inside == 0) & (glyphs == 0)] = (100, 55, 75)
    result = colour_connected_balloon_mask(rgb, selection)
    assert result is not None
    mask, _ = result
    assert not np.any(mask[inside == 0])
    assert np.all(mask[glyphs > 0] > 0)


@pytest.mark.parametrize("kind", ["gradient", "texture", "open", "blank", "letter"])
def test_non_balloon_regions_are_not_flat_filled(kind):
    rgb, glyphs, selection, inside = balloon()
    if kind == "gradient":
        ramp = np.linspace(120, 250, rgb.shape[1]).astype(np.uint8)
        rgb[inside > 0] = np.repeat(ramp[None, :, None], rgb.shape[0], axis=0).repeat(3, axis=2)[inside > 0]
        rgb[glyphs > 0] = 0
    elif kind == "texture":
        rng = np.random.default_rng(42)
        rgb[inside > 0] = rng.integers(170, 255, (np.count_nonzero(inside), 3), dtype=np.uint8)
        rgb[glyphs > 0] = 0
    elif kind == "open":
        rgb[:] = 255
        rgb[glyphs > 0] = 0
    elif kind == "blank":
        rgb[:] = 255
    else:
        rgb[:] = 255
        cv2.putText(rgb, "O", (200, 170), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 8)
    assert colour_connected_balloon_mask(rgb, selection) is None


def test_cleaning_uses_colour_mask_without_neural_calls(tmp_path, monkeypatch):
    rgb, glyphs, selection, inside = balloon((220, 203, 180), (150, 30, 50))
    path = tmp_path / "balloon.png"
    Image.fromarray(rgb).save(path)
    manager = CleaningManager(tmp_path)
    monkeypatch.setattr(manager, "_text_mask", lambda _: pytest.fail("No OCR model needed"))
    monkeypatch.setattr(manager, "_lama", lambda *_: pytest.fail("No inpainting needed"))
    monkeypatch.setattr("core.cleaning_manager.complete_boundary_glyphs",
                        lambda *_: pytest.fail("Preserve the connected mask contour inset"))
    x, y, w, h = cv2.boundingRect(selection)
    plan = manager.prepare_masks(path, [dict(x=x, y=y, width=w, height=h)], lambda _: None, lambda: False)
    assert plan["solid_entries"] == 1
    result = manager.clean_prepared(path, plan, lambda _: None, lambda: False)
    cleaned = rgb.copy()
    for patch in result["patches"]:
        x, y = patch["x"], patch["y"]
        h, w = patch["mask"].shape
        np.copyto(cleaned[y:y+h, x:x+w], patch["pixels"], where=(patch["mask"] > 0)[:, :, None])
    assert np.all(cleaned[glyphs > 0] == (220, 203, 180))
    np.testing.assert_array_equal(cleaned[inside == 0], rgb[inside == 0])
