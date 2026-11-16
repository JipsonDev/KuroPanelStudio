import numpy as np

from core.cleaning_manager import CleaningManager, inpaint_model_name
from core.settings_manager import SettingsManager


def test_flat_holes_use_migan_and_textured_ones_lama():
    mask = np.zeros((120, 120), np.uint8)
    mask[40:80, 40:80] = 255
    flat = np.full((120, 120, 3), 250, np.uint8)
    flat[38:82, 38] = 0  # a balloon outline beside the text does not count as texture
    rng = np.random.default_rng(1)
    textured = rng.integers(0, 255, (120, 120, 3), dtype=np.uint8)
    gradient = np.repeat(np.linspace(30, 220, 120, dtype=np.uint8)[None, :, None], 120, 0).repeat(3, 2)
    assert CleaningManager._flat_surroundings(flat, mask)
    assert not CleaningManager._flat_surroundings(textured, mask)
    assert not CleaningManager._flat_surroundings(gradient, mask)

    engine = CleaningManager.__new__(CleaningManager)
    engine.inpaint_model = "auto"
    calls = []
    engine._migan = lambda image, hole: calls.append("migan") or image
    engine._lama_many = lambda jobs: calls.extend(["lama"] * len(jobs)) or [image for image, _ in jobs]
    results = engine._inpaint_many([(flat, mask), (textured, mask), (flat, mask)])
    assert calls == ["migan", "migan", "lama"] and results[1] is textured


def test_auto_is_a_valid_choice_and_old_default_moves_to_it(tmp_path):
    assert inpaint_model_name("Automático · según el fondo") == "auto"
    assert inpaint_model_name("MI-GAN · migan_pipeline_v2.onnx") == "migan"
    path = tmp_path / "settings.json"
    path.write_text('{"clean": {"model": "MI-GAN · migan_pipeline_v2.onnx"}}', encoding="utf-8")
    settings = SettingsManager(path)
    assert settings.data["clean"]["model"] == "Automático · según el fondo"
    settings.data["clean"]["model"] = "MI-GAN · migan_pipeline_v2.onnx"
    settings.save()
    assert SettingsManager(path).data["clean"]["model"] == "MI-GAN · migan_pipeline_v2.onnx"
