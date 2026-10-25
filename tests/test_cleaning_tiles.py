"""Bound LaMa work without resampling artwork or losing masked edge pixels."""
from pathlib import Path

import numpy as np
import pytest

from core.cleaning_manager import CleaningManager


class RecordingSession:
    """Replace only neural inference; exercise the real crop and batch paths."""

    def __init__(self, provider="CPUExecutionProvider"):
        self.provider = provider
        self.shapes = []

    def get_providers(self):
        return [self.provider]

    def run(self, _outputs, inputs):
        image, mask = inputs["image"], inputs["mask"]
        self.shapes.append(image.shape)
        assert image.shape[2] % 8 == image.shape[3] % 8 == 0
        assert mask.shape == (image.shape[0], 1, *image.shape[2:])
        # The source is binary artwork. Resampling would introduce grey pixels.
        assert np.all((image == 0) | (image == 1))
        return [np.full_like(image, .75)]


def cleaner(monkeypatch, provider="CPUExecutionProvider"):
    manager = CleaningManager(Path("Models"))
    session = RecordingSession(provider)
    monkeypatch.setattr(manager, "_get_lama_session", lambda: session)
    monkeypatch.setattr("core.cleaning_manager.cuda_memory_mb", lambda: (4096, 8192))
    return manager, session


def artwork(height, width):
    source = np.zeros((height, width, 3), np.uint8)
    source[::2, ::2] = 255
    source[1::2, 1::2] = 255
    return source


@pytest.mark.parametrize("shape", [(2053, 317), (319, 2051), (1231, 1409)])
def test_large_connected_mask_is_fully_cleaned_in_bounded_native_tiles(monkeypatch, shape):
    manager, session = cleaner(monkeypatch)
    source = artwork(*shape)
    mask = np.full(shape, 255, np.uint8)
    original = source.copy()

    result = manager._lama(source, mask)

    assert result.shape == source.shape
    assert result.dtype == np.uint8
    assert np.all(result == 191), "Every selected pixel, including tile seams, must be written"
    assert len(session.shapes) > 1
    assert all(max(size[2:]) <= 1024 for size in session.shapes)
    np.testing.assert_array_equal(source, original)


def test_sparse_long_page_only_infers_near_selected_pixels(monkeypatch):
    manager, session = cleaner(monkeypatch)
    source = artwork(6001, 257)
    mask = np.zeros(source.shape[:2], np.uint8)
    mask[:9, :7] = 255
    mask[-11:, -13:] = 255

    result = manager._lama(source, mask)

    assert len(session.shapes) == 2
    assert all(max(size[2:]) <= 1024 for size in session.shapes)
    assert np.all(result[mask > 0] == 191)
    np.testing.assert_array_equal(result[mask == 0], source[mask == 0])


@pytest.mark.parametrize("provider", ["CPUExecutionProvider", "CUDAExecutionProvider"])
def test_mixed_batch_does_not_send_large_crop_directly_to_onnx(monkeypatch, provider):
    manager, session = cleaner(monkeypatch, provider)
    large = artwork(1241, 1059)
    small = artwork(55, 71)
    masks = [np.full(image.shape[:2], 255, np.uint8) for image in (large, small)]

    results = manager._lama_many(list(zip((large, small), masks)))

    assert [result.shape for result in results] == [large.shape, small.shape]
    assert all(np.all(result == 191) for result in results)
    assert all(max(size[2:]) <= 1024 for size in session.shapes)


def test_empty_large_mask_skips_model_and_returns_independent_copy(monkeypatch):
    manager, session = cleaner(monkeypatch)
    source = artwork(2100, 100)

    result = manager._lama(source, np.zeros(source.shape[:2], np.uint8))

    assert not session.shapes
    np.testing.assert_array_equal(result, source)
    assert not np.shares_memory(result, source)
