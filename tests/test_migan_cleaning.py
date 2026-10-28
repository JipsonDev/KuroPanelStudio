"""MI-GAN must use its uint8/inverted-mask contract without touching artwork."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import pytest

from core.cleaning_manager import CleaningManager
from core.cleaning_process_manager import CleaningProcessManager
from core.settings_manager import SettingsManager


class PipelineSession:
    """Only replace expensive neural inference; keep dispatch/tiling/compositing real."""

    def __init__(self):
        self.shapes = []

    def run(self, _outputs, inputs):
        image, mask = inputs["image"], inputs["mask"]
        assert image.dtype == mask.dtype == np.uint8
        assert image.shape[0:2] == (1, 3)
        assert mask.shape == (1, 1, *image.shape[2:])
        assert set(np.unique(mask)) <= {0, 255}
        self.shapes.append(image.shape)
        result = image.copy()
        # Erase only the model's zero-valued hole: catches reversed polarity.
        result[:, :, mask[0, 0] == 0] = 219
        return [result]


def manager_with_pipeline(tmp_path, monkeypatch):
    manager = CleaningManager(tmp_path)
    manager.set_inpaint_model("migan")
    session = PipelineSession()
    monkeypatch.setattr(manager, "_get_migan_session", lambda: session)
    monkeypatch.setattr(manager, "_get_lama_session", lambda: pytest.fail("MI-GAN selected LaMa"))
    return manager, session


def test_migan_manual_mask_uses_uint8_rgb_and_preserves_unselected_pixels(tmp_path, monkeypatch):
    manager, session = manager_with_pipeline(tmp_path, monkeypatch)
    image = np.full((75, 91, 3), (15, 85, 160), np.uint8)
    # Textured context disables the editor's existing smooth-background fit,
    # so the visible result must come from the selected neural backend.
    image[::2, ::2] = (245, 225, 195)
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[30:40, 35:48] = 255
    source = image.copy()

    result = manager.clean_mask(image, mask)

    assert np.all(result[mask > 0] == 219)
    np.testing.assert_array_equal(result[mask == 0], source[mask == 0])
    np.testing.assert_array_equal(image, source)
    assert len(session.shapes) == 1


def test_migan_empty_mask_does_not_load_model(tmp_path, monkeypatch):
    manager, session = manager_with_pipeline(tmp_path, monkeypatch)
    image = np.full((45, 59, 3), 80, np.uint8)
    result = manager.clean_mask(image, np.zeros(image.shape[:2], np.uint8))
    np.testing.assert_array_equal(result, image)
    assert not session.shapes
    assert not np.shares_memory(result, image)


@pytest.mark.parametrize("shape", [(1201, 317), (319, 1231)])
def test_migan_tiles_clean_every_selected_pixel_including_seams(tmp_path, monkeypatch, shape):
    manager, session = manager_with_pipeline(tmp_path, monkeypatch)
    image = np.full((*shape, 3), (15, 85, 160), np.uint8)
    mask = np.full(shape, 255, np.uint8)

    result = manager.clean_mask(image, mask)

    assert result.shape == image.shape
    assert np.all(result == 219)
    assert len(session.shapes) > 1
    assert all(max(size[2:]) <= 512 for size in session.shapes)


def test_migan_batch_preserves_order_and_skips_empty_masks(tmp_path, monkeypatch):
    manager, session = manager_with_pipeline(tmp_path, monkeypatch)
    images = [np.full((80, 90, 3), value, np.uint8) for value in (40, 60, 80)]
    masks = [np.zeros((80, 90), np.uint8) for _ in images]
    masks[0][20:28, 25:35] = masks[2][45:55, 50:60] = 255

    results = manager._inpaint_many(list(zip(images, masks)))

    assert len(results) == 3
    assert len(session.shapes) == 2
    for result, image, mask in zip(results, images, masks):
        assert np.all(result[mask > 0] == 219)
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])


def test_migan_sparse_long_page_skips_unselected_tiles(tmp_path, monkeypatch):
    manager, session = manager_with_pipeline(tmp_path, monkeypatch)
    image = np.full((6001, 83, 3), 50, np.uint8)
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[:8, :6] = mask[-8:, -6:] = 255

    result = manager._inpaint(image, mask)

    assert len(session.shapes) == 2
    assert np.all(result[mask > 0] == 219)
    np.testing.assert_array_equal(result[mask == 0], image[mask == 0])


def test_reviewed_automatic_plan_uses_migan_and_preserves_mask(tmp_path, monkeypatch):
    from PIL import Image

    manager, session = manager_with_pipeline(tmp_path, monkeypatch)
    image = np.full((100, 120, 3), 45, np.uint8)
    image[::2, ::2] = 240
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[35:50, 50:65] = 255
    source = tmp_path / "page.png"
    Image.fromarray(image).save(source)
    # This is also the serialized legacy plan format: switching models must
    # keep a user's reviewed mask instead of rebuilding it with OCR.
    plan = {"entries": [{"x": 0, "y": 0, "mask": mask, "method": "lama"}]}
    monkeypatch.setattr(manager, "_text_mask", lambda crop: np.zeros(crop.shape[:2], np.uint8))

    result = manager.clean_prepared(source, plan, lambda _: None, lambda: False)

    assert result["model"] == "migan"
    assert session.shapes
    patch = result["patches"][0]
    np.testing.assert_array_equal(patch["mask"], mask)
    assert np.all(patch["pixels"][mask > 0] == 219)
    np.testing.assert_array_equal(patch["pixels"][mask == 0], image[mask == 0])


def test_switching_models_releases_neural_sessions_but_keeps_reviewed_cache(tmp_path):
    manager = CleaningManager(tmp_path)
    manager._lama_session = object()
    manager._mask_cache["reviewed"] = {"mask": np.array([[255]], np.uint8)}
    manager.set_inpaint_model("MI-GAN · migan_pipeline_v2.onnx")
    assert manager._lama_session is None
    assert manager._mask_cache["reviewed"]["mask"][0, 0] == 255
    assert not manager._warmed


def test_explicit_missing_migan_reports_error_instead_of_using_lama(tmp_path):
    manager = CleaningManager(tmp_path)
    manager.set_inpaint_model("migan")
    with pytest.raises(FileNotFoundError, match="migan_pipeline_v2.onnx"):
        manager.clean_mask(np.zeros((32, 32, 3), np.uint8), np.full((32, 32), 255, np.uint8))


def test_cleaning_model_choice_survives_panel_and_settings_roundtrip(tmp_path):
    from PySide6.QtWidgets import QApplication
    from ui.ai_panel import AIOptionsPanel

    app = QApplication.instance() or QApplication([])
    settings = SettingsManager(tmp_path / "settings.json")
    panel = AIOptionsPanel()
    panel.set_configuration(settings.data)
    assert panel.clean_configuration()[1].startswith("MI-GAN")
    panel.clean_model.setCurrentText("LaMa · lama.onnx")
    settings.update_provider("clean", *panel.clean_configuration())
    reloaded = SettingsManager(settings.path)
    panel.set_configuration(reloaded.data)
    assert panel.clean_configuration() == ("Local (AI)", "LaMa · lama.onnx")
    panel.close()


def test_real_migan_worker_uses_selected_model_and_keeps_artwork():
    model = Path("Models/migan_pipeline_v2.onnx")
    if not model.is_file() or model.stat().st_size < 1_000_000:
        pytest.skip("MI-GAN weights unavailable")
    image = np.full((160, 240, 3), (205, 220, 245), np.uint8)
    mask = np.zeros(image.shape[:2], np.uint8)
    cv2.putText(mask, "TEST", (48, 90), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 2)
    image[mask > 0] = 0
    worker = CleaningProcessManager(Path("Models"))
    worker.set_inpaint_model("migan")
    worker.set_device_mode("cpu")
    try:
        result = worker.clean_mask(image, mask)
        assert np.mean(result[mask > 0]) > 150
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        assert "MI-GAN" in worker.runtime_label
    finally:
        worker.close()
