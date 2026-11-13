"""Glyphs on a balloon outline, outlined captions, large blocks and tiny OCR crops."""
import io
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image

from core.bubble_mask import colour_connected_balloon_mask
from core.detection_manager import DetectionManager
from core.glyph_completion import complete_outlined_lettering
from core.ocr_manager import OCRManager


def draw_glyph(rgb, x, y, w, h):
    cv2.line(rgb, (x + 3, y + 2), (x + 3, y + h - 1), (0, 0, 0), 3)
    cv2.line(rgb, (x + w - 4, y + 2), (x + w - 4, y + h - 1), (0, 0, 0), 3)
    cv2.line(rgb, (x + 3, y + h // 2), (x + w - 4, y + h // 2), (0, 0, 0), 3)


def balloon(touching: bool, gap: int = 0):
    """White ellipse on grey art with a column of letters; the last one meets the outline."""
    rgb = np.full((360, 300, 3), 120, np.uint8)
    cv2.ellipse(rgb, (150, 180), (90, 140), 0, 0, 360, (255, 255, 255), cv2.FILLED)
    cv2.ellipse(rgb, (150, 180), (90, 140), 0, 0, 360, (0, 0, 0), 3)
    glyphs = [(136, 70 + row * 34, 28, 28) for row in range(6)]
    # The ellipse's inner edge is at y = 318 on the axis; move the column end onto it.
    bottom = 318 - 28 + (2 if touching else -gap)
    glyphs.append((136, bottom, 28, 28))
    ink = np.zeros(rgb.shape[:2], bool)
    for x, y, w, h in glyphs:
        draw_glyph(rgb, x, y, w, h)
        ink[y:y + h, x:x + w] |= np.all(rgb[y:y + h, x:x + w] < 40, axis=2)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[60:322, 126:174] = 255
    return rgb, selection, glyphs, ink


def glyph_coverage(mask, glyph, ink):
    x, y, w, h = glyph
    inside = ink[y:y + h - 4, x:x + w]
    return np.count_nonzero((mask[y:y + h - 4, x:x + w] > 0) & inside) / max(1, np.count_nonzero(inside))


def test_glyph_touching_the_outline_does_not_drop_its_column():
    rgb, selection, glyphs, ink = balloon(touching=True)
    result = colour_connected_balloon_mask(rgb, selection)
    assert result is not None
    mask, fill = result
    assert fill == (255, 255, 255)
    for glyph in glyphs:
        assert glyph_coverage(mask, glyph, ink) > .95
    # The outline itself and the artwork beyond it are never painted.
    outside = np.zeros(rgb.shape[:2], np.uint8)
    cv2.ellipse(outside, (150, 180), (89, 139), 0, 0, 360, 255, cv2.FILLED)
    assert not np.any((mask > 0) & (outside == 0))


def test_glyph_close_to_the_outline_is_cleaned():
    rgb, selection, glyphs, ink = balloon(touching=False, gap=2)
    mask, _ = colour_connected_balloon_mask(rgb, selection)
    assert glyph_coverage(mask, glyphs[-1], ink) > .9


def test_artwork_beside_a_tail_is_not_treated_as_a_touching_glyph():
    rgb, selection, glyphs, ink = balloon(touching=False, gap=12)
    # A tail makes the balloon concave; the dark drawing beside it lies in the hull.
    tail = np.array([[205, 270], [285, 345], [180, 300]], np.int32)
    cv2.fillPoly(rgb, [tail], (255, 255, 255))
    cv2.polylines(rgb, [tail], True, (0, 0, 0), 3)
    cv2.ellipse(rgb, (150, 180), (87, 137), 0, 0, 360, (255, 255, 255), cv2.FILLED)
    for x, y, w, h in glyphs:
        draw_glyph(rgb, x, y, w, h)
    art = (slice(318, 340), slice(215, 250))
    rgb[art] = (10, 10, 10)
    result = colour_connected_balloon_mask(rgb, selection)
    assert result is not None
    assert not np.any(result[0][art])


def outlined_caption():
    rng = np.random.default_rng(5)
    noise = cv2.GaussianBlur(rng.integers(90, 200, (260, 320), dtype=np.uint8), (0, 0), 3)
    rgb = np.repeat(noise[:, :, None], 3, axis=2)
    # Drawn with polylines: putText ignores thickness in some OpenCV builds.
    strokes = [np.array(points, np.int32) for points in (
        [(70, 170), (70, 100), (100, 135), (130, 100), (130, 170)],
        [(165, 170), (190, 100), (215, 170)], [(176, 145), (204, 145)],
        [(250, 100), (250, 170)], [(250, 135), (285, 100)], [(250, 135), (285, 170)],
    )]
    lettering = np.zeros(rgb.shape[:2], np.uint8)
    cv2.polylines(rgb, strokes, False, (0, 0, 0), 18, cv2.LINE_AA)
    cv2.polylines(rgb, strokes, False, (255, 255, 255), 6, cv2.LINE_AA)
    cv2.polylines(lettering, strokes, False, 255, 18, cv2.LINE_AA)
    return rgb, lettering > 0


def test_outline_cut_by_the_text_box_is_completed_over_artwork():
    rgb, lettering = outlined_caption()
    ys, xs = np.nonzero(lettering)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    # A tight detector box through the border, as boundary protection leaves it.
    selection[ys.min() + 5:ys.max() - 4, xs.min() + 5:xs.max() - 4] = 255
    mask = np.where(lettering & (selection > 0), 255, 0).astype(np.uint8)
    dark = rgb.max(axis=2) <= 70
    assert np.count_nonzero(dark & (mask == 0)) > 150
    completed = complete_outlined_lettering(rgb, mask, selection)
    assert np.count_nonzero(dark & (completed == 0)) == 0
    assert np.all(completed[mask > 0])


def test_frames_and_artwork_joined_to_the_outline_are_left_alone():
    rgb, lettering = outlined_caption()
    ys, xs = np.nonzero(lettering)
    cv2.line(rgb, (0, int(ys.min()) + 3), (319, int(ys.min()) + 3), (0, 0, 0), 5)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[ys.min() + 5:ys.max() - 4, xs.min() + 5:xs.max() - 4] = 255
    mask = np.where(lettering & (selection > 0), 255, 0).astype(np.uint8)
    completed = complete_outlined_lettering(rgb, mask, selection)
    assert not np.any(completed[:, :20]) and not np.any(completed[:, 300:])
    flat = np.full((80, 80, 3), 255, np.uint8)
    empty = np.zeros((80, 80), np.uint8)
    assert complete_outlined_lettering(flat, empty, empty) is empty


def text_block():
    image = np.full((400, 700, 3), 255, np.uint8)
    for row in range(4):
        for column in range(16 - row * 2):
            draw_glyph(image, 110 + row * 30 + column * 30, 122 + row * 44, 24, 30)
    return Image.fromarray(image), SimpleNamespace(x=90, y=110, width=520, height=190, confidence=.3)


def test_large_block_of_lettering_on_a_flat_ground_is_kept():
    image, candidate = text_block()
    assert candidate.width * candidate.height > image.width * image.height * .15
    assert DetectionManager._lettering_evidence(image, candidate)


def test_large_blocks_of_artwork_are_still_rejected():
    candidate = SimpleNamespace(x=90, y=110, width=520, height=190, confidence=.9)
    rng = np.random.default_rng(3)
    texture = Image.fromarray(rng.integers(0, 256, (400, 700, 3), dtype=np.uint8))
    assert not DetectionManager._lettering_evidence(texture, candidate)
    drawing = np.full((400, 700, 3), 255, np.uint8)
    cv2.ellipse(drawing, (350, 205), (220, 80), 0, 0, 360, (0, 0, 0), 4)
    cv2.line(drawing, (100, 120), (600, 290), (0, 0, 0), 4)
    assert not DetectionManager._lettering_evidence(Image.fromarray(drawing), candidate)
    blank = Image.new("RGB", (700, 400), "white")
    assert not DetectionManager._lettering_evidence(blank, candidate)
    whole = SimpleNamespace(x=0, y=0, width=700, height=400, confidence=.9)
    assert not DetectionManager._lettering_evidence(text_block()[0], whole)


def test_tiny_two_column_crop_is_enlarged_for_the_vision_model():
    page = np.full((300, 300, 3), 255, np.uint8)
    for column in range(2):
        for row in range(4):
            cv2.rectangle(page, (104 + column * 21, 102 + row * 21), (119 + column * 21, 117 + row * 21), (0, 0, 0), 2)
    payload, size = OCRManager()._prepare_crop(Image.fromarray(page), dict(x=100, y=98, width=41, height=88))
    assert size[0] * size[1] >= OCRManager.MIN_CROP_PIXELS * .9
    assert size[0] <= (41 + 12) * 2 and size[1] <= (88 + 12) * 2
    assert Image.open(io.BytesIO(payload)).size == size


def test_strip_seam_fragment_yields_to_the_complete_box():
    from core.detection_manager import TextRegion
    whole = TextRegion(33, 24407, 824, 518, .75)
    cut = TextRegion(34, 24503, 822, 421, .85)
    lone = TextRegion(100, 30000, 300, 80, .6)
    kept = DetectionManager._drop_seam_fragments([whole, cut, lone], {id(cut), id(lone)})
    assert whole in kept and cut not in kept
    # A cut box with no complete counterpart is still better than nothing.
    assert lone in kept
    assert DetectionManager._deduplicate(kept)[0] is whole


def test_bold_glyph_is_not_mistaken_for_a_balloon():
    rgb = np.full((300, 340, 3), 255, np.uint8)
    # A heavy closed glyph with a counter, in a balloon larger than the crop.
    cv2.rectangle(rgb, (110, 100), (200, 200), (0, 0, 0), cv2.FILLED)
    cv2.rectangle(rgb, (140, 130), (170, 170), (255, 255, 255), cv2.FILLED)
    cv2.rectangle(rgb, (225, 100), (245, 200), (0, 0, 0), cv2.FILLED)
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[85:215, 95:260] = 255
    assert colour_connected_balloon_mask(rgb, selection) is None


def halo_caption():
    rng = np.random.default_rng(9)
    pink = np.clip(rng.normal((240, 170, 180), 8, (220, 300, 3)), 0, 255).astype(np.uint8)
    strokes = [np.array(points, np.int32) for points in (
        [(70, 150), (70, 80), (110, 115), (150, 80), (150, 150)], [(190, 80), (190, 150), (240, 150)],
    )]
    cv2.polylines(pink, strokes, False, (255, 255, 255), 30, cv2.LINE_AA)
    cv2.polylines(pink, strokes, False, (250, 120, 200), 10, cv2.LINE_AA)
    core = np.zeros(pink.shape[:2], np.uint8)
    cv2.polylines(core, strokes, False, 255, 14, cv2.LINE_AA)
    halo = np.zeros(pink.shape[:2], np.uint8)
    cv2.polylines(halo, strokes, False, 255, 30, cv2.LINE_AA)
    selection = np.zeros(pink.shape[:2], np.uint8)
    selection[55:175, 45:265] = 255
    return pink, np.where(core > 0, 255, 0).astype(np.uint8), halo > 0, selection


def test_mask_grows_over_a_white_border_around_lettering():
    from core.glyph_completion import cover_lettering_halo
    rgb, mask, halo, selection = halo_caption()
    grown = cover_lettering_halo(rgb, mask, selection)
    assert np.count_nonzero(halo & (grown == 0)) < np.count_nonzero(halo) * .03
    assert np.count_nonzero(grown) < np.count_nonzero(halo) * 1.5


def test_mask_does_not_grow_into_a_balloon_of_the_same_colour():
    from core.glyph_completion import cover_lettering_halo
    rgb = np.full((220, 300, 3), 255, np.uint8)
    mask = np.zeros(rgb.shape[:2], np.uint8)
    cv2.rectangle(mask, (90, 90), (210, 130), 255, cv2.FILLED)
    rgb[mask > 0] = 0
    selection = np.zeros(rgb.shape[:2], np.uint8)
    selection[70:150, 70:230] = 255
    assert np.array_equal(cover_lettering_halo(rgb, mask, selection), mask)


class _Reply:
    def __init__(self, status, text=""):
        self.status_code, self._text = status, text

    def json(self):
        if self.status_code != 200:
            return {"error": {"message": "busy"}}
        return {"choices": [{"message": {"content": self._text}}]}


def _read_one_box(tmp_path, session):
    from unittest.mock import patch
    path = tmp_path / "page.png"
    Image.new("RGB", (120, 120), "white").save(path)
    manager = OCRManager()
    manager._session = session
    with patch.object(OCRManager, "REJECTED_RETRY_DELAYS", (0.0, 0.0)):
        return manager.run_regions(
            path, [dict(x=10, y=10, width=100, height=100)], "Alibaba Cloud", "model", "key",
            lambda _: None, lambda: False,
        )


def test_rate_limited_request_is_sent_again_and_read(tmp_path):
    from unittest.mock import Mock
    session = Mock()
    session.post.side_effect = [_Reply(429), _Reply(503), _Reply(200, "Hola")]
    assert _read_one_box(tmp_path, session)[0]["text"] == "Hola"
    assert session.post.call_count == 3


def test_request_that_may_have_been_read_is_never_repeated(tmp_path):
    import pytest
    from unittest.mock import Mock
    from core.ocr_manager import OCRNetworkError
    for failure in (ConnectionResetError(10054, "reset"), _Reply(500), _Reply(504)):
        session = Mock()
        session.post.side_effect = [failure, _Reply(200, "tarde")]
        with pytest.raises(OCRNetworkError):
            _read_one_box(tmp_path, session)
        assert session.post.call_count == 1


def test_idle_connections_are_replaced_but_an_injected_session_is_kept():
    manager = OCRManager()
    first = manager._get_session()
    assert manager._get_session() is first
    manager._session_used -= OCRManager.SESSION_IDLE_SECONDS + 1
    assert manager._get_session() is not first
    injected = object()
    manager._session = injected
    manager._session_used -= OCRManager.SESSION_IDLE_SECONDS + 1
    assert manager._get_session() is injected


def cut_balloon_page():
    """A balloon drawn off the right edge of the page, over a white gutter and a panel."""
    page = np.full((600, 500, 3), 255, np.uint8)
    cv2.rectangle(page, (0, 330), (499, 599), (90, 110, 150), cv2.FILLED)
    cv2.ellipse(page, (330, 250), (260, 150), 0, 0, 360, (255, 255, 255), cv2.FILLED)
    cv2.ellipse(page, (330, 250), (260, 150), 0, 0, 360, (0, 0, 0), 4)
    return page


def test_balloon_cut_by_the_page_edge_gives_its_whole_interior_to_the_text():
    from core.balloon_typesetter import detect_full_balloon
    page = cut_balloon_page()
    box = (190, 215, 250, 60)
    assert detect_full_balloon(page, box, 8) is None
    found = detect_full_balloon(page, box, 8, (True, True, True, True))
    assert found is not None
    (x, y, width, height), mask = found
    assert x + width == 500 and height > 250 and width > 400
    # Padding also keeps the lettering away from the page edge.
    assert not np.any(mask[:, -6:])


def test_gutters_and_inner_crop_edges_are_not_balloons():
    from core.balloon_typesetter import detect_full_balloon
    page = cut_balloon_page()
    # The same pixels as an inner search crop: its right edge is not the page.
    assert detect_full_balloon(page, (190, 215, 250, 60), 8, (True, True, False, True)) is None
    gutter = np.full((400, 500, 3), 255, np.uint8)
    cv2.rectangle(gutter, (0, 0), (499, 120), (60, 60, 60), cv2.FILLED)
    cv2.rectangle(gutter, (0, 280), (499, 399), (60, 60, 60), cv2.FILLED)
    assert detect_full_balloon(gutter, (150, 170, 200, 60), 8, (True, True, True, True)) is None
