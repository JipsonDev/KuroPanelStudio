"""Count outgoing OCR requests without contacting a provider."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from core.ocr_cache import OCRCache
from core.ocr_manager import OCRManager, OCRConfigurationError
from ui.main_window import MainWindow


def response(text="Texto", usage=None):
    body = {"choices": [{"message": {"content": text}}]}
    if usage is not None:
        body["usage"] = usage
    return SimpleNamespace(status_code=200, json=lambda: body)


@pytest.fixture
def page(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (300, 1500), "white").save(path)
    regions = [dict(id=str(i), x=20, y=20 + i * 65, width=100, height=45) for i in range(20)]
    return path, regions, tmp_path / "ocr.sqlite3"


def run(manager, path, regions, **kwargs):
    return manager.run_regions(path, regions, "Alibaba Cloud", "qwen-vl-ocr", "fake-key",
                               kwargs.pop("progress", lambda _: None), lambda: False, **kwargs)


def test_twenty_boxes_repeated_and_reopened_need_only_twenty_requests(page):
    path, regions, cache = page
    session = Mock()
    session.post.return_value = response(usage={"prompt_tokens": 200, "completion_tokens": 10})
    first_manager = OCRManager(cache_path=cache)
    first_manager._session = session
    first = run(first_manager, path, regions)
    assert session.post.call_count == 20
    with patch("core.ocr_manager.Image.open", side_effect=AssertionError("Cache should avoid image decoding")):
        assert run(first_manager, path, regions) == first
        reopened = OCRManager(cache_path=cache)
        reopened._session = session
        assert run(reopened, path, regions) == first
    assert session.post.call_count == 20
    assert reopened.usage_stats()["requests"] == 0
    assert first_manager.usage_stats() == dict(requests=20, reported_responses=20, input_tokens=4000, output_tokens=200)
    forced = OCRManager()
    forced._session = Mock()
    forced._session.post.return_value = response()
    for _ in range(3):
        run(forced, path, regions, force_refresh=True)
    assert forced._session.post.call_count == 60


def test_identical_boxes_share_request_but_keep_ids_and_progress(page):
    path, regions, _ = page
    manager = OCRManager()
    manager._session = Mock()
    manager._session.post.return_value = response()
    boxes = [{**regions[0], "id": str(i), "_ocr_request_token": f"token{i}"} for i in range(4)]
    progress = []
    result = run(manager, path, boxes, progress=progress.append)
    assert manager._session.post.call_count == 1
    assert [r["id"] for r in result] == [r["id"] for r in boxes]
    assert [r["_ocr_request_token"] for r in result] == [r["_ocr_request_token"] for r in boxes]
    assert progress[0] == 0 and progress[-1] == 100


def test_explicit_reread_is_one_request_and_updates_disk_cache(page):
    path, regions, cache = page
    manager = OCRManager(cache_path=cache)
    manager._session = Mock()
    manager._session.post.side_effect = [response("Anterior"), response("Corregido")]
    run(manager, path, regions[:1])
    run(manager, path, regions[:1], force_refresh=True)
    assert manager._session.post.call_count == 2
    reopened = OCRManager(cache_path=cache)
    reopened._session = Mock()
    result = run(reopened, path, regions[:1])
    assert result[0]["text"] == "Corregido"
    reopened._session.post.assert_not_called()


@pytest.mark.parametrize("change", ["geometry", "source", "endpoint", "model"])
def test_persistent_cache_is_invalidated_when_inputs_change(page, change):
    path, regions, cache = page
    manager = OCRManager(cache_path=cache)
    session = Mock()
    session.post.return_value = response()
    manager._session = session
    run(manager, path, regions[:1])
    reopened = OCRManager(cache_path=cache)
    reopened._session = session
    if change == "geometry":
        regions[0]["x"] += 5
    elif change == "source":
        Image.new("RGB", (300, 1500), "black").save(path)
    elif change == "endpoint":
        reopened.set_endpoint("https://dashscope.aliyuncs.com")
    if change == "model":
        reopened.run_regions(path, regions[:1], "Alibaba Cloud", "other-model", "fake-key", lambda _: None, lambda: False)
    else:
        run(reopened, path, regions[:1])
    assert session.post.call_count == 2


def test_empty_reading_is_cached_across_restart(page):
    path, regions, cache = page
    session = Mock()
    session.post.return_value = response("")
    for _ in range(2):
        manager = OCRManager(cache_path=cache)
        manager._session = session
        assert run(manager, path, regions[:1])[0]["text"] == ""
    assert session.post.call_count == 1


def test_late_success_after_another_request_fails_is_not_billed_again(page):
    path, regions, cache = page
    manager = OCRManager(cache_path=cache)
    manager._parallelism = 2
    late_started, failure_returned, release_late = Event(), Event(), Event()
    # Coordinate by crop preparation order, independently of HTTP thread order.
    original = manager._prepare_crop

    def prepare(image, region):
        payload, size = original(image, region)
        return (b"first" if region["id"] == "0" else b"late"), size

    def post(_url, **kwargs):
        import base64
        content = kwargs["json"]["messages"][0]["content"]
        encoded = next(x["image_url"]["url"] for x in content if x["type"] == "image_url").split(",", 1)[1]
        if base64.b64decode(encoded) == b"first":
            assert late_started.wait(5)
            failure_returned.set()
            return SimpleNamespace(status_code=404, json=lambda: {})
        late_started.set()
        assert release_late.wait(5)
        return response("Guardado")

    manager._session = SimpleNamespace(post=post)
    with patch.object(manager, "_prepare_crop", side_effect=prepare), ThreadPoolExecutor(1) as pool:
        job = pool.submit(run, manager, path, regions[:2])
        try:
            assert failure_returned.wait(5)
        finally:
            release_late.set()
        with pytest.raises(OCRConfigurationError):
            job.result(timeout=5)
    reopened = OCRManager(cache_path=cache)
    reopened._session = Mock()
    assert run(reopened, path, regions[1:2])[0]["text"] == "Guardado"
    reopened._session.post.assert_not_called()


def test_cache_is_bounded_and_stores_empty_strings(tmp_path):
    cache = OCRCache(tmp_path / "cache.sqlite3", limit=2)
    cache.put((1,), "old")
    cache.put((2,), "")
    cache.put((3,), "new")
    assert cache.get_many([(1,), (2,), (3,)]) == {(2,): "", (3,): "new"}


def test_completed_empty_box_stays_checked_and_moving_it_invalidates_ocr():
    region = dict(id="a", x=0, y=0, width=50, height=50, text="")
    MainWindow._merge_ocr_results(None, [{**region, "_ocr_request_token": "t"}], {"t": region})
    MainWindow._promote_ocr_overlay(region)
    assert not OCRManager.needs_recognition(region)
    moved = {**region, "x": 10}
    assert MainWindow._mark_moved_regions_ocr_stale([region], [moved]) == 1
    assert OCRManager.needs_recognition(moved)


def test_normal_page_action_with_completed_boxes_never_starts_task():
    window = SimpleNamespace(
        current_task=None,
        project=SimpleNamespace(pages=[1], active_page=SimpleNamespace(path="page.png")),
        ai_panel=SimpleNamespace(set_status=Mock(), STATUS_OK="ok"), _toast=Mock(), _start_task=Mock(),
    )
    MainWindow._run_ocr_for_regions(window, [dict(text="Leído"), dict(text="", ocr_checked=True)])
    window._start_task.assert_not_called()


def test_only_explicit_reread_forces_fresh_request():
    window = SimpleNamespace(layers=SimpleNamespace(current_index=lambda: 0),
                             regions=[dict(text="Leído")], _run_ocr_for_regions=Mock())
    MainWindow.run_ocr_api_one(window)
    window._run_ocr_for_regions.assert_called_with(window.regions, force=False)
    MainWindow.run_ocr_api_one(window, force=True)
    window._run_ocr_for_regions.assert_called_with(window.regions, force=True)
