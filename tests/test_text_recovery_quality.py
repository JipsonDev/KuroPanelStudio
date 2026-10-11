"""Offline checks for missed balloons, image token inputs and safe ink recovery."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.balloon_geometry import closed_balloon_text_candidates
from core.detection_manager import DetectionManager, TextRegion, _OnnxTextDetector
from core.glyph_completion import complete_anchored_glyph_fragments
from core.ocr_manager import OCRManager, OCRIncompleteError


def balloon_page(text=True):
    image = Image.new("RGB", (500, 800), "gray")
    draw = ImageDraw.Draw(image)
    draw.ellipse((100, 100, 400, 350), fill="white", outline="black", width=4)
    if text:
        draw.text((200, 190), "Hello world!", fill="black")
        draw.text((200, 230), "Missed text", fill="black")
    return image


def detector_with_mock():
    model = _OnnxTextDetector.__new__(_OnnxTextDetector)
    model.batch_size = None
    model.predict_boxes = Mock()
    manager = DetectionManager(Path("models"))
    manager._load_model = lambda: model
    return manager, model


def test_missed_closed_balloon_gets_detail_pass_without_cloud(tmp_path):
    page = tmp_path / "missed.png"
    image = balloon_page()
    image.save(page)
    candidate = closed_balloon_text_candidates(np.asarray(image))
    assert len(candidate) == 1
    bx, by, bw, bh, tx, ty, tw, th = candidate[0]
    left, top = bx - 16, by - 16
    manager, model = detector_with_mock()
    model.predict_boxes.side_effect = [[[]], [[((tx-left, ty-top, tx+tw-left, ty+th-top), .3)]]]
    progress = []
    regions = manager.detect(page, progress.append, lambda: False)
    assert len(regions) == 1
    assert regions[0]["x"] == tx and regions[0]["y"] == ty
    assert model.predict_boxes.call_count == 2
    assert model.predict_boxes.call_args.args[1] == .18
    assert progress == sorted(progress) and progress[-1] == 100
    manager.detect(page, lambda _: None, lambda: False)
    assert model.predict_boxes.call_count == 2


def test_found_balloon_and_empty_balloon_need_no_recovery(tmp_path):
    image = balloon_page()
    _, _, _, _, tx, ty, tw, th = closed_balloon_text_candidates(np.asarray(image))[0]
    manager, model = detector_with_mock()
    model.predict_boxes.return_value = [[((tx, ty, tx+tw, ty+th), .9)]]
    page = tmp_path / "found.png"
    image.save(page)
    assert len(manager.detect(page, lambda _: None, lambda: False)) == 1
    assert model.predict_boxes.call_count == 1
    assert closed_balloon_text_candidates(np.asarray(balloon_page(False))) == []


def test_detail_rejects_predictions_outside_text_and_cancellation():
    manager, model = detector_with_mock()
    model.predict_boxes.return_value = [[((0, 0, 10, 10), .9), ((20, 20, 40, 35), .3)]]
    windows = [(100, 100, 200, 200, 120, 120, 30, 30)]
    assert len(manager._recover_balloon_text(model, Image.new("RGB", (300, 300)), windows, 1, lambda _: None, lambda: False)) == 1
    model.predict_boxes.reset_mock()
    assert manager._recover_balloon_text(model, Image.new("RGB", (300, 300)), windows, 1, lambda _: None, lambda: True) == []
    model.predict_boxes.assert_not_called()


def test_trim_preserves_faint_letters_and_isolated_punctuation():
    image = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((155, 135), "Text", fill=(245, 245, 245))
    draw.rectangle((235, 132, 237, 134), fill="black")
    trimmed = OCRManager._trim_uniform_margins(image)
    assert trimmed.width * trimmed.height < image.width * image.height * .2
    original_ink = np.max(np.abs(np.asarray(image).astype(int) - 255), axis=2) > 3
    trimmed_ink = np.max(np.abs(np.asarray(trimmed).astype(int) - 255), axis=2) > 3
    assert np.count_nonzero(original_ink) == np.count_nonzero(trimmed_ink)


def test_textured_margin_is_not_trimmed():
    rng = np.random.default_rng(22)
    image = Image.fromarray(rng.integers(150, 256, (120, 220, 3), dtype=np.uint8))
    assert OCRManager._trim_uniform_margins(image) is image


def test_normal_dialogue_is_no_longer_upscaled_fourfold():
    image = Image.new("RGB", (500, 150), "white")
    ImageDraw.Draw(image).rectangle((25, 25, 425, 74), fill="black")
    payload, size = OCRManager()._prepare_crop(image, dict(x=25, y=25, width=401, height=50))
    assert size[0] <= 413 and size[1] <= 62
    assert Image.open(BytesIO(payload)).size == size


def test_equal_pixels_across_pages_share_one_request_and_survive_restart(tmp_path):
    page = tmp_path / "one.png"
    other = tmp_path / "two.png"
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).text((55, 40), "Same text", fill="black")
    image.save(page)
    image.save(other)
    regions = [dict(id="1", x=20, y=20, width=150, height=60), dict(id="2", x=25, y=20, width=145, height=60)]
    manager = OCRManager(cache_path=tmp_path / "cache.sqlite3")
    session = Mock()
    entered, release = Event(), Event()
    def post(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        assert kwargs['json']['max_tokens'] == 4096
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "Same text"}}]})
    session.post.side_effect = post
    manager._session = session
    def run(mgr, path):
        return mgr.run_regions(path, regions, "Alibaba Cloud", "qwen-vl-ocr", "fake", lambda _: None, lambda: False)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run, manager, page)
        try:
            assert entered.wait(5)
        finally:
            release.set()
        assert [item['id'] for item in future.result(5)] == ['1', '2']
    assert session.post.call_count == 1
    reopened = OCRManager(cache_path=tmp_path / "cache.sqlite3")
    reopened._session = session
    assert run(reopened, other)[0]['text'] == 'Same text'
    assert session.post.call_count == 1


def test_truncated_reading_is_not_cached_or_automatically_retried(tmp_path):
    page = tmp_path / 'source.png'
    Image.new('RGB', (100, 100), 'white').save(page)
    manager = OCRManager()
    manager._session = Mock()
    manager._session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {
        'choices': [{'finish_reason': 'length', 'message': {'content': 'cut'}}]})
    with pytest.raises(OCRIncompleteError):
        manager.run_regions(page, [dict(x=10, y=10, width=50, height=50)], 'Alibaba Cloud', 'qwen-vl-ocr', 'fake', lambda _: None, lambda: False)
    assert manager._session.post.call_count == 1 and manager.cache_stats()['items'] == 0


@pytest.mark.parametrize('background,ink', [((255,255,255),(180,180,180)), ((250,240,220),(245,125,230)), ((20,20,20),(230,230,230))])
def test_anchored_cleaning_recovers_faint_colored_and_inverted_strokes(background, ink):
    rgb = np.full((100, 180, 3), background, np.uint8)
    rgb[40:60, 60:70] = ink
    # An unrelated mark must remain outside the recovered mask.
    rgb[40:60, 120:130] = ink
    mask = np.zeros(rgb.shape[:2], np.uint8)
    mask[40:59, 60:70] = 255
    selection = np.zeros_like(mask)
    selection[20:80, 20:160] = 255
    result = complete_anchored_glyph_fragments(rgb, mask, selection)
    assert np.all(result[40:60, 60:70] == 255)
    assert not np.any(result[40:60, 120:130])


def test_anchored_cleaning_rejects_texture_and_unanchored_outline():
    rgb = np.random.default_rng(18).integers(100, 256, (100,180,3), dtype=np.uint8)
    mask = np.zeros((100,180), np.uint8); mask[40:59,60:70] = 255
    selection = np.full_like(mask,255)
    assert np.array_equal(complete_anchored_glyph_fragments(rgb,mask,selection),mask)


def test_large_block_needs_aligned_lines_and_flat_background():
    image = Image.new("RGB", (300,200), "white")
    block = TextRegion(20,20,240,100,.9)
    lines = [TextRegion(80,y,120,16,.3) for y in (30,55,80)]
    assert DetectionManager._supported_text_block(image,block,[block,*lines])
    assert not DetectionManager._supported_text_block(image,block,[block,*lines[:2]])
    scattered = [TextRegion(x,y,120,16,.3) for x,y in ((25,30),(80,55),(140,80))]
    assert not DetectionManager._supported_text_block(image,block,[block,*scattered])
    texture = Image.fromarray(np.random.default_rng(9).integers(0,256,(200,300,3),dtype=np.uint8))
    assert not DetectionManager._supported_text_block(texture,block,[block,*lines])


def test_real_large_balloon_retains_all_four_lines():
    source = Path("graphify-out/real-cleaning/final-v6/179-02-dialogo-grueso/source.png")
    if not source.is_file() or not Path("models/yolo12s_animetext.onnx").is_file():
        pytest.skip("Local untracked real crop/model is unavailable")
    manager = DetectionManager(Path("models"))
    manager.set_device_mode("cpu")
    boxes = manager.detect(source,lambda _:None,lambda:False)
    assert len(boxes) == 1
    box = boxes[0]
    for x,y,w,h in ((360,104,225,53),(284,164,385,53),(285,224,408,53),(151,284,670,53)):
        intersection = max(0,min(x+w,box['x']+box['width'])-max(x,box['x'])) * max(0,min(y+h,box['y']+box['height'])-max(y,box['y']))
        assert intersection / (w*h) >= .98
