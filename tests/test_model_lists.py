"""Typed models stay in the lists, and the panel shows what Settings saved."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.settings_manager import SettingsManager


def test_typed_models_are_remembered_newest_first(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    settings.update_provider("ocr", "Alibaba Cloud", " qwen3.8-flash ")
    settings.update_provider("ocr", "Alibaba Cloud", "qwen-vl-ocr")
    settings.update_provider("ocr", "Alibaba Cloud", "qwen3.8-flash")
    reopened = SettingsManager(tmp_path / "settings.json")
    assert reopened.data["ocr"]["model"] == "qwen3.8-flash"
    assert reopened.saved_models("ocr") == ["qwen3.8-flash", "qwen-vl-ocr"]
    for number in range(30):
        reopened.remember_model("translate", f"modelo-{number}")
    assert len(reopened.saved_models("translate")) == SettingsManager.MAX_SAVED_MODELS
    assert not reopened.remember_model("translate", "  ")


def test_settings_dialog_values_are_remembered(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    settings.update_workflow({
        "ocr_model": "qwen3.8-27b", "ocr_base_url": "", "provider": "Alibaba Cloud",
        "model": "qwen3.8-flash", "source_language": "ZH", "target_language": "ES",
        "autosave_minutes": 2,
    })
    assert "qwen3.8-27b" in settings.saved_models("ocr")
    assert settings.saved_models("translate")[0] == "qwen3.8-flash"


@pytest.fixture
def panel():
    QApplication = pytest.importorskip("PySide6.QtWidgets").QApplication
    from ui.ai_panel import AIOptionsPanel
    app = QApplication.instance() or QApplication([])
    widget = AIOptionsPanel()
    yield widget
    widget.close()
    app.processEvents()


def configuration(ocr="qwen-vl-ocr", translate=("Alibaba Cloud", "qwen-mt-flash"), saved=()):
    return {
        "ocr": {"platform": "Alibaba Cloud", "model": ocr, "saved_models": list(saved)},
        "translate": {"platform": translate[0], "model": translate[1], "saved_models": list(saved)},
        "clean": {"platform": "Local (AI)", "model": "LaMa · lama.onnx"},
    }


def test_panel_shows_saved_models_and_lists_custom_ones(panel):
    saved = []
    panel.provider_changed.connect(lambda *values: saved.append(values))
    panel.set_configuration(configuration("qwen3.8-flash", ("Alibaba Cloud", "qwen3.8-27b"), ["mi-modelo"]))
    assert panel.ocr_configuration() == ("Alibaba Cloud", "qwen3.8-flash")
    assert panel.translate_configuration() == ("Alibaba Cloud", "qwen3.8-27b")
    for name in ("qwen3.8-flash", "mi-modelo"):
        assert panel.ocr_model.findText(name) >= 0
    # Showing a configuration never writes it back or resets the model.
    assert saved == []


def test_typed_model_is_added_to_the_list_once(panel):
    saved = []
    panel.provider_changed.connect(lambda *values: saved.append(values))
    panel.ocr_model.setEditText("qwen3.8-flash")
    panel.ocr_model.lineEdit().editingFinished.emit()
    panel.ocr_model.lineEdit().editingFinished.emit()
    assert panel.ocr_model.findText("qwen3.8-flash") >= 0
    assert [panel.ocr_model.itemText(i) for i in range(panel.ocr_model.count())].count("qwen3.8-flash") == 1
    assert saved[-1] == ("ocr", "Alibaba Cloud", "qwen3.8-flash")


def test_translation_model_is_saved_once_and_with_its_provider(panel):
    saved = []
    panel.provider_changed.connect(lambda *values: saved.append(values))
    panel.translate_model.setEditText("qwen3.8-fla")
    assert saved == []  # no partial identifiers while typing
    panel.translate_model.setEditText("qwen3.8-flash")
    panel.translate_model.lineEdit().editingFinished.emit()
    assert saved[-1] == ("translate", panel.translate_provider.currentText(), "qwen3.8-flash")
    panel.translate_provider.setCurrentText("DeepSeek")
    assert saved[-1] == ("translate", "DeepSeek", "deepseek-chat")


def test_ocr_provider_switch_lists_that_providers_models(panel):
    saved = []
    panel.provider_changed.connect(lambda *values: saved.append(values))
    panel.set_configuration(configuration("qwen3.8-flash", saved=["gemini-mio", "qwen-mio"]))
    panel.ocr_provider.setCurrentText("Gemini")
    assert saved[-1] == ("ocr", "Gemini", "gemini-3.1-flash-lite")
    items = [panel.ocr_model.itemText(i) for i in range(panel.ocr_model.count())]
    assert "gemini-mio" in items and "qwen-mio" not in items and "qwen-vl-ocr" not in items
    panel.ocr_provider.setCurrentText("Alibaba Cloud")
    assert saved[-1] == ("ocr", "Alibaba Cloud", "qwen-vl-ocr")
