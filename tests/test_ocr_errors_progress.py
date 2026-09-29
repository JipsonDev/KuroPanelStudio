"""Regressions for real provider failures and hidden foreground feedback."""
import io
import json
import os
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.ocr_manager import OCRManager, OCRConfigurationError, ALIBABA_COMPATIBLE_URL
from core.project_manager import Page
from core.settings_manager import SettingsManager
from ui.main_window import MainWindow
from ui.task_progress import TaskProgress


@pytest.mark.parametrize("model,minimum", [
    ("qwen-vl-ocr", 3136), ("qwen-vl-ocr-latest", 4096), ("qwen-vl-plus", 65536),
])
def test_request_uses_provider_pixel_defaults(tmp_path, model, minimum):
    path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(path)
    manager = OCRManager()
    session = Mock()

    def post(url, **kwargs):
        body = kwargs["json"]
        assert body["model"] == model
        for item in body["messages"][0]["content"]:
            if item["type"] == "image_url":
                # Simulate the two observed 400 failures when old hardcoded
                # values override the model's supported defaults.
                if item.get("min_pixels", minimum) < minimum:
                    return Mock(status_code=400, json=lambda: {"error": {
                        "message": f"Parameter min_pixels must be greater than or equal to {minimum}"}})
                assert "min_pixels" not in item
                assert "max_pixels" not in item
                assert set(item["image_url"]) == {"url"}
        return Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": "Texto"}}]})

    session.post.side_effect = post
    manager._session = session
    result = manager.run_regions(path, [dict(x=0, y=0, width=100, height=100)],
                                 "Alibaba Cloud", model, " key ", lambda _: None, lambda: False)
    assert result[0]["text"] == "Texto"
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer key"


@pytest.mark.parametrize("size", [(1, 1), (1, 5000), (5000, 1), (10000, 70)])
def test_thin_crops_meet_image_dimensions_without_changing_box(size):
    page = Image.new("RGB", size, "white")
    region = dict(x=0, y=0, width=size[0], height=size[1])
    payload, dimensions = OCRManager()._prepare_crop(page, region)
    with Image.open(io.BytesIO(payload)) as crop:
        assert crop.size == dimensions
        assert min(crop.size) >= 32
        assert max(crop.size) <= 720
        assert crop.width * crop.height <= 320000
    assert (region["width"], region["height"]) == size


def test_404_has_actionable_context_and_no_network_retries(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (80, 80), "white").save(path)
    manager = OCRManager()
    base = "https://workspace123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    manager.set_endpoint(base)
    session = Mock()
    session.post.return_value = Mock(status_code=404, json=lambda: {"error": {"message": "Model not found"}})
    manager._session = session
    with pytest.raises(OCRConfigurationError) as error:
        manager.run_regions(path, [dict(x=0, y=0, width=80, height=80)],
                            "Alibaba Cloud", "unavailable-model", "secret-key", lambda _: None, lambda: False)
    assert session.post.call_count == 1
    assert session.post.call_args.args[0] == base + "/chat/completions"
    assert "404" in str(error.value)
    assert "unavailable-model" in str(error.value)
    assert "workspace123.cn-beijing.maas.aliyuncs.com" in str(error.value)
    assert "secret-key" not in str(error.value)


def test_endpoint_defaults_and_rejects_malformed_routes():
    assert OCRManager.normalize_endpoint("") == ALIBABA_COMPATIBLE_URL
    for value in ("https://example.com", "http://dashscope.aliyuncs.com",
                  "https://dashscope.aliyuncs.com/wrong", "https://dashscope.aliyuncs.com?key=x",
                  "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"):
        with pytest.raises(OCRConfigurationError):
            OCRManager.normalize_endpoint(value)


def test_explicit_model_version_survives_restart(tmp_path):
    path = tmp_path / "settings.json"
    data = {"ocr": {"model": "qwen-vl-ocr-2025-11-20", "base_url": "https://dashscope.aliyuncs.com"}}
    path.write_text(json.dumps(data), encoding="utf-8")
    settings = SettingsManager(path)
    assert settings.data["ocr"]["model"] == data["ocr"]["model"]
    assert settings.data["ocr"]["base_url"] == data["ocr"]["base_url"]


def test_stale_import_cannot_replace_foreground_progress():
    app = QApplication.instance() or QApplication([])
    banner = TaskProgress()
    banner.start("Cargar capítulo", owner="folder:1")
    banner.start("OCR")
    banner.update_progress(30)
    banner.finish(error="Error anterior", owner="folder:1")
    banner.update_progress(99, owner="folder:1")
    assert banner.timer.isActive()
    assert banner.bar.value() == 30
    banner.finish()
    assert not banner.timer.isActive()
    banner.close()


def test_quota_exhaustion_is_not_reported_as_invalid_key():
    response = Mock(status_code=403, json=lambda: {"message": "The free quota has been exhausted."})
    with pytest.raises(OCRConfigurationError, match="cuota gratuita"):
        OCRManager._response_content(response)


def wait_until(condition, timeout=5):
    deadline = monotonic() + timeout
    while not condition() and monotonic() < deadline:
        QTest.qWait(10)
    assert condition()


def test_unconfigured_or_rejected_ocr_routes_to_settings_without_api_call(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with (patch.object(MainWindow, "_offer_recovery"),
          patch.object(MainWindow, "_import_legacy_profiles_async"),
          patch.object(MainWindow, "_warm_up_models")):
        window = MainWindow()
        try:
            primary = window.ai_panel._primary_buttons["ocr"]
            assert primary.text() == "Configurar OCR"
            with patch.object(window, "open_settings") as open_settings:
                primary.click()
                open_settings.assert_called_once()
                path = tmp_path / "sample.png"
                Image.new("RGB", (40, 70), "white").save(path)
                window.project.pages = [Page(path.name, path, 40, 70)]
                window._run_ocr_for_regions([{}], force=True)
                assert open_settings.call_count == 2
            assert window.current_task is None
            with patch.object(window.credentials, "get", return_value="test-key"):
                window._update_provider_statuses()
                assert primary.text() == "Leer texto"
                provider, model = window.ai_panel.ocr_configuration()
                window._ocr_rejected_config = (provider, model.strip(), window.settings.data["ocr"].get("base_url", ""))
                window._update_provider_statuses()
                assert primary.text() == "Configurar OCR"
                assert "404" in window.ai_panel._status_labels["ocr"].text()
                window.ai_panel.ocr_model.setCurrentText("qwen-vl-ocr-latest")
                window._save_provider_selection("ocr", provider, "qwen-vl-ocr-latest")
                assert primary.text() == "Leer texto"
        finally:
            window.close()
            app.processEvents()


def test_foreground_cancel_stops_result_and_keeps_progress_in_cancelled_state(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    gate = Event()
    with (patch.object(MainWindow, "_offer_recovery"),
          patch.object(MainWindow, "_import_legacy_profiles_async"),
          patch.object(MainWindow, "_warm_up_models")):
        window = MainWindow()
        window.show()
        finished = Mock()

        def operation(progress, cancelled):
            progress(40, "Página 1 de 2 · OCR", "page-a")
            gate.wait(5)
            return "resultado"

        try:
            window._start_task(operation, "OCR del capítulo", "Listo", finished)
            wait_until(lambda: "Página 1 de 2" in window.task_progress.label.toolTip())
            window.task_progress.cancel_button.click()
            assert "Cancelando" in window.task_progress.label.toolTip()
            gate.set()
            wait_until(lambda: window.current_task is None)
            assert "Cancelado" in window.task_progress.label.toolTip()
            assert window.task_progress.cancel_button.isHidden()
            finished.assert_not_called()
            assert not window._page_failures
        finally:
            gate.set()
            window.thread_pool.waitForDone(5000)
            window.close()
            app.processEvents()


@pytest.mark.parametrize("failure", [False, True])
def test_real_worker_progress_visible_with_inspector_hidden(tmp_path, monkeypatch, failure):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    gate = Event()
    with (patch.object(MainWindow, "_offer_recovery"),
          patch.object(MainWindow, "_import_legacy_profiles_async"),
          patch.object(MainWindow, "_warm_up_models")):
        window = MainWindow()
        window.resize(720, 540)
        window.show()
        window.workspace.set_focus_mode(True)
        app.processEvents()
        result_callback = Mock()

        def operation(progress, cancelled):
            progress(40)
            if not gate.wait(5):
                raise TimeoutError("Test worker was not released")
            if failure:
                raise OCRConfigurationError("HTTP 404: modelo no disponible\nModelo: test-model")
            return "recognized"

        try:
            window._start_task(operation, "OCR", "Listo", result_callback)
            assert window.task_progress.isVisible()
            assert window.task_progress.bar.maximum() == 0
            assert not window.inspector.isVisible()
            wait_until(lambda: window.task_progress.bar.value() == 40)
            assert window.task_progress.timer.isActive()
            assert "Procesando" in window.status.activity.text()
            gate.set()
            wait_until(lambda: window.current_task is None)
            assert window.task_progress.isVisible()
            assert not window.task_progress.timer.isActive()
            assert "Listo" in window.status.activity.text()
            if failure:
                result_callback.assert_not_called()
                assert window.task_progress.details.isVisible()
                assert "404" in window.task_progress._error
            else:
                result_callback.assert_called_once_with("recognized")
                assert window.task_progress.bar.value() == 100
            assert window.width() == 720
            window.task_progress.dismiss.click()
            assert window.task_progress.isHidden()
        finally:
            gate.set()
            window.thread_pool.waitForDone(5000)
            window.close()
            app.processEvents()
