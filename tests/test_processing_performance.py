"""Offline regression coverage for detection geometry and bounded OCR work."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image

from core.detection_manager import DetectionManager, TextRegion, _OnnxTextDetector
from core.ocr_manager import OCRManager


@pytest.mark.parametrize("height, expected", [
    (500, [0]), (1701, [0]), (2000, [0]), (2001, [0, 1]),
    (3700, [0, 1700]), (3800, [0, 1700, 1800]),
    (5400, [0, 1700, 3400]), (60000, None),
])
def test_tiles_cover_every_pixel_with_full_size_tail(height, expected):
    starts = DetectionManager._tile_starts(height, 2000, 300)
    if expected is not None:
        assert starts == expected
    assert starts[0] == 0
    assert starts[-1] + 2000 >= height
    assert all(0 < b - a <= 1700 for a, b in zip(starts, starts[1:]))
    assert all(start + 2000 <= max(height, 2000) for start in starts)


@pytest.mark.parametrize("tile, overlap", [(0, 0), (2000, 2000), (2000, -1)])
def test_invalid_tiles_fail_instead_of_creating_thousands_of_strips(tile, overlap):
    with pytest.raises(ValueError):
        DetectionManager._tile_starts(10000, tile, overlap)


def reference_deduplicate(regions):
    accepted = []
    for candidate in sorted(regions, key=lambda r: r.confidence, reverse=True):
        for current in accepted:
            intersection = max(0, min(candidate.x + candidate.width, current.x + current.width) - max(candidate.x, current.x)) * max(0, min(candidate.y + candidate.height, current.y + current.height) - max(candidate.y, current.y))
            area = candidate.width * candidate.height
            union = area + current.width * current.height - intersection
            if (union and intersection / union > .35) or intersection / max(1, area) > .75:
                break
        else:
            accepted.append(candidate)
    return sorted(accepted, key=lambda r: (r.y, r.x))


def test_vectorized_suppression_matches_reference_with_ties_and_nested_boxes():
    rng = np.random.default_rng(71)
    regions = [TextRegion(int(x), int(y), int(w), int(h), float(c))
               for x, y, w, h, c in zip(rng.integers(0, 800, 400), rng.integers(0, 2000, 400),
                                      rng.integers(1, 300, 400), rng.integers(1, 200, 400),
                                      rng.choice([.4, .7, .9], 400))]
    regions += [TextRegion(0, 0, 100, 100, .99), TextRegion(10, 10, 20, 20, .8)]
    assert DetectionManager._deduplicate(regions) == reference_deduplicate(regions)
    assert DetectionManager._deduplicate([]) == []


def make_detector(batch_size=None):
    detector = _OnnxTextDetector.__new__(_OnnxTextDetector)
    detector.input_name = "images"
    detector.size = 640
    detector.batch_size = batch_size
    detector.cv2 = cv2
    detector.uses_cuda = False
    return detector


def test_fixed_batches_pad_last_call_without_extra_results():
    detector = make_detector(2)
    seen = []

    def run(_outputs, inputs):
        tensor = inputs["images"]
        seen.append(tensor.shape)
        return [np.tile(np.array([320, 320, 100, 100, .9], np.float32)[None, :, None], (2, 1, 1))]

    detector.session = SimpleNamespace(run=run)
    result = detector.predict_boxes([np.zeros((640, 640, 3), np.uint8)] * 3, .25, .7)
    assert seen == [(2, 3, 640, 640)] * 2
    assert len(result) == 3
    assert all(len(boxes) == 1 for boxes in result)


def test_letterbox_restores_coordinates_and_discards_padding_and_nonfinite():
    detector = make_detector()
    predictions = np.array([
        [320, 320, 128, 64, .9],  # scale .32, left 192 -> (200, 900, 600, 1100)
        [50, 300, 20, 20, .95],  # entirely in padding
        [320, 320, -20, 20, .9],
        [np.nan, 100, 20, 20, .9],
        [320, 200, 20, 20, np.inf],
    ], np.float32)
    detector.session = SimpleNamespace(run=lambda *_: [predictions.T[None]])
    result = detector.predict_boxes([np.zeros((2000, 800, 3), np.uint8)], .25, .7)
    assert len(result[0]) == 1
    np.testing.assert_allclose(result[0][0][0], [200, 900, 600, 1100])


def test_static_rectangular_onnx_metadata_and_directml_options():
    session = SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(name="images", shape=[1, 3, 320, 640])],
        get_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    with (patch("core.detection_manager.preload_onnx_cuda"),
          patch("core.detection_manager.register_cuda_dll_directories"),
          patch("onnxruntime.get_available_providers", return_value=session.get_providers()),
          patch("onnxruntime.InferenceSession", return_value=session) as factory):
        detector = _OnnxTextDetector(Path("unused.onnx"), "auto", 2)
    assert (detector.batch_size, detector.input_height, detector.input_width) == (1, 320, 640)
    assert factory.call_args.kwargs["sess_options"].enable_mem_pattern is False


def test_pytorch_fallback_receives_bgr_and_no_redundant_tile(tmp_path):
    source = tmp_path / "page.png"
    Image.new("RGB", (80, 2000), (255, 80, 10)).save(source)
    seen = []

    def predict(strips, **_kwargs):
        seen.extend(strips)
        return [SimpleNamespace(boxes=None) for _ in strips]

    manager = DetectionManager(tmp_path)
    with patch.object(manager, "_load_model", return_value=SimpleNamespace(predict=predict)):
        assert manager.detect(source, lambda _: None, lambda: False) == []
    assert len(seen) == 1
    np.testing.assert_array_equal(seen[0][0, 0], [10, 80, 255])


def test_detection_cancellation_does_not_cache_partial_results(tmp_path):
    source = tmp_path / "page.png"
    Image.new("RGB", (80, 1000), "white").save(source)
    cancelled = Event()
    detector = make_detector()
    detector.predict_boxes = lambda *_: [[(np.array([5, 5, 40, 40]), .9)]]
    manager = DetectionManager(tmp_path)
    with patch.object(manager, "_load_model", return_value=detector):
        assert manager.detect(source, lambda _: cancelled.set(), cancelled.is_set) == []
    assert not manager._result_cache


def response(text="dialogue"):
    return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": text}}]})


@pytest.fixture
def ocr_page(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (200, 3000), "white").save(path)
    regions = [dict(id=str(i), x=10, y=10 + i * 70, width=150, height=40) for i in range(30)]
    manager = OCRManager()
    manager._session = SimpleNamespace(post=lambda *_args, **_kwargs: response())
    return path, regions, manager


def run_ocr(path, regions, manager, **kwargs):
    return manager.run_regions(path, regions, "Alibaba Cloud", "test-model", "test-key",
                               kwargs.pop("progress", lambda _: None),
                               kwargs.pop("cancelled", lambda: False), **kwargs)


def test_fully_cached_ocr_never_opens_or_decodes_page(ocr_page):
    path, regions, manager = ocr_page
    first = run_ocr(path, regions, manager)
    with patch("core.ocr_manager.Image.open", side_effect=AssertionError("cache must avoid decoding")):
        assert run_ocr(path, regions, manager) == first


def test_ocr_starts_network_before_preparing_entire_page_and_bounds_crops(ocr_page):
    path, regions, manager = ocr_page
    first_request, window_full, release = Event(), Event(), Event()
    prepared = []
    original = manager._prepare_crop

    def prepare(page, region):
        prepared.append(region["id"])
        if len(prepared) == manager.parallelism:
            window_full.set()
        return original(page, region)

    def post(*_args, **_kwargs):
        first_request.set()
        assert release.wait(5)
        return response()

    manager._session.post = post
    with patch.object(manager, "_prepare_crop", side_effect=prepare), ThreadPoolExecutor(1) as executor:
        job = executor.submit(run_ocr, path, regions, manager)
        try:
            assert first_request.wait(5)
            assert window_full.wait(5)
            assert len(prepared) == manager.parallelism
        finally:
            release.set()
        result = job.result(timeout=10)
    assert [r["id"] for r in result] == [r["id"] for r in regions]


def test_ocr_cancellation_stops_new_crops_and_requests(ocr_page):
    path, regions, manager = ocr_page
    manager._parallelism = 1
    cancelled = Event()
    calls = []

    def post(*_args, **_kwargs):
        calls.append(1)
        cancelled.set()
        return response()

    manager._session.post = post
    assert run_ocr(path, regions, manager, cancelled=cancelled.is_set) == []
    assert len(calls) == 1
    # A completed response may already be billed even if cancellation arrives
    # during the request. Keep it reusable without applying it to the canvas.
    assert manager.cache_stats()["items"] == 1


def test_ocr_failed_request_retains_previous_successes(ocr_page):
    path, regions, manager = ocr_page
    manager._parallelism = 1
    calls = []

    def post(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("invalid response")
        return response()

    manager._session.post = post
    with pytest.raises(ValueError, match="invalid response"):
        run_ocr(path, regions, manager)
    assert len(calls) == 2
    assert manager.cache_stats()["items"] == 1


def test_ocr_cache_tracks_quality_profile(ocr_page):
    path, regions, manager = ocr_page
    calls = []
    manager._session.post = lambda *_args, **_kwargs: (calls.append(1) or response())
    run_ocr(path, regions[:1], manager)
    manager.set_resource_policy(SimpleNamespace(name="high", ocr_cache_items=512))
    run_ocr(path, regions[:1], manager)
    assert len(calls) == 2


def test_low_resource_profile_keeps_two_requests_after_fast_responses():
    manager = OCRManager()
    manager.set_resource_policy(SimpleNamespace(name="low", ocr_cache_items=256))
    for _ in range(8):
        manager._observe_latency(.1)
    assert manager.parallelism == 2


@pytest.mark.parametrize("text", ["We begin at the end.", "THE END IS NEAR", "Begin the journey!"])
def test_ocr_preserves_real_english_words(text):
    assert OCRManager.normalize_cjk_text(text) == text


@pytest.mark.parametrize("region", [dict(x=0, y=0, width=0, height=10),
                                    dict(x=100, y=0, width=20, height=20)])
def test_invalid_ocr_crops_report_geometry_error(region):
    with Image.new("RGB", (100, 100)) as page, pytest.raises(ValueError, match="caja OCR"):
        OCRManager()._prepare_crop(page, region)
