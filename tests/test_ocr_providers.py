"""Gemini OCR, reasoning switched off, and the «Probar conexión» request."""
import pytest

from core.failure_messages import describe_failure
from core.ocr_manager import (GEMINI_COMPATIBLE_URL, OCRConfigurationError, OCRManager,
                              thinking_options)
from core.translation_manager import TranslationConfigurationError, TranslationManager


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": "KURO 42"}, "finish_reason": "stop"}]
        }
        self.headers = {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def post(self, url, **kwargs):
        self.sent.append((url, kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


def test_thinking_is_disabled_only_where_it_can_be():
    assert thinking_options("Alibaba Cloud", "qwen3.8-flash") == {"enable_thinking": False}
    assert thinking_options("Alibaba Cloud", "qwen-plus") == {"enable_thinking": False}
    for model in ("qwen-vl-ocr", "qwen-mt-flash", "qwen2.5-vl-7b-instruct", "qwen3-vl-8b-thinking", "qvq-max"):
        assert thinking_options("Alibaba Cloud", model) == {}
    assert thinking_options("Gemini", "gemini-2.5-flash-lite") == {"reasoning_effort": "none"}
    assert thinking_options("Gemini", "gemini-3.1-flash-lite") == {"reasoning_effort": "low"}
    assert thinking_options("Gemini", "gemini-2.5-pro") == {}


def test_gemini_ocr_uses_its_own_server_and_key(monkeypatch):
    manager = OCRManager()
    session = Session(Response())
    text = manager._post_ocr(session, "Gemini", manager.endpoint_for("Gemini"), "gemini-2.5-flash",
                             "key", "QUJD", timeout=(1, 1))
    url, request = session.sent[0]
    assert text == "KURO 42"
    assert url == GEMINI_COMPATIBLE_URL
    assert request["json"]["reasoning_effort"] == "none"
    assert request["headers"]["Authorization"] == "Bearer key"
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert manager.validate("Gemini") == "from-env"
    with pytest.raises(OCRConfigurationError):
        manager.validate("OpenAI", "x")


def test_a_model_that_refuses_the_switch_is_sent_as_before():
    manager = OCRManager()
    refused = Response(400, {"error": {"message": "The parameter enable_thinking is not supported"}})
    session = Session(refused, Response(), Response())
    assert manager._post_ocr(session, "Alibaba Cloud", manager.endpoint, "qwen3.8-27b", "k", "QQ==", (1, 1)) == "KURO 42"
    assert "enable_thinking" in session.sent[0][1]["json"]
    assert "enable_thinking" not in session.sent[1][1]["json"]
    # Remembered: no second refused request for the same model.
    manager._post_ocr(session, "Alibaba Cloud", manager.endpoint, "qwen3.8-27b", "k", "QQ==", (1, 1))
    assert len(session.sent) == 3 and "enable_thinking" not in session.sent[2][1]["json"]


def test_gemini_errors_are_explained_in_gemini_terms():
    quota = Response(429, [{"error": {"code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}])
    with pytest.raises(Exception) as caught:
        OCRManager._response_content(quota, "Gemini")
    failure = describe_failure("ocr", str(caught.value))
    assert failure.title == "Gemini alcanzó su límite de uso"
    assert "Quota exceeded" in str(caught.value)
    bad_key = Response(400, [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}])
    with pytest.raises(OCRConfigurationError) as caught:
        OCRManager._response_content(bad_key, "Gemini")
    assert describe_failure("ocr", str(caught.value)).kind == "credentials"


def test_connection_test_sends_one_small_image(monkeypatch):
    import core.ocr_manager as module
    session = Session(Response())
    monkeypatch.setattr(module.requests, "Session", lambda: session)
    assert OCRManager().test_connection("Gemini", "gemini-3.1-flash-lite", "k") == "KURO 42"
    assert len(session.sent) == 1
    image = session.sent[0][1]["json"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,") and len(image) < 20000


def test_translation_drops_the_switch_when_refused(monkeypatch):
    calls = []

    def request(url, payload, headers, timeout=35.0):
        calls.append(payload)
        if "enable_thinking" in payload:
            raise TranslationConfigurationError("Alibaba Cloud respondió HTTP 400: enable_thinking not supported")
        return {"choices": [{"message": {"content": '{"translations": ["hola"], "terms": []}'}}]}

    monkeypatch.setattr(TranslationManager, "_request", staticmethod(request))
    result = TranslationManager()._translate_structured_batch(
        ["你好"], "Alibaba Cloud", "qwen3.8-flash", "k", "ZH", "ES", [], "", "")
    assert result["translations"] == ["hola"]
    assert calls[0]["enable_thinking"] is False and "enable_thinking" not in calls[1]
