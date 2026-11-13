"""Chapter OCR keeps every page's result, shares connections and stops on a dead link."""
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image

from core.ocr_manager import OCRManager
from core.project_io import page_key
from ui.main_window import MainWindow


class Reply:
    def __init__(self, text):
        self.status_code, self.text = 200, text

    def json(self):
        return {"choices": [{"message": {"content": self.text}}]}


def chapter(tmp_path, boxes_per_page=(3, 1, 2, 3)):
    pages, regions = [], {}
    for number, count in enumerate(boxes_per_page):
        path = tmp_path / f"{number:02d}.png"
        image = Image.new("RGB", (100, 40 * count), "white")
        for box in range(count):
            image.paste((number * 40, box * 60, 0), (5, 5 + box * 40, 30, 30 + box * 40))
        image.save(path)
        page = SimpleNamespace(path=path, name=path.name)
        pages.append(page)
        regions[page_key(page)] = [
            dict(id=f"{number}-{box}", x=0, y=box * 40, width=100, height=40) for box in range(count)
        ]
    return pages, regions


def window(pages, regions, ocr):
    started = {}
    stub = SimpleNamespace(
        current_task=None, project=SimpleNamespace(pages=pages), page_regions=regions,
        _ordered_regions_for_key=lambda key: regions[key], _update_provider_statuses=lambda: None,
        ai_panel=SimpleNamespace(is_ready=lambda _: True, ocr_configuration=lambda: ("Alibaba Cloud", "m"),
                                 set_status=Mock(), STATUS_OK="ok", STATUS_WARNING="warn"),
        credentials=SimpleNamespace(get=lambda _: "key"), ocr=ocr,
        settings=SimpleNamespace(data={"ocr": {}}), _toast=Mock(), open_settings=Mock(),
        _source_layer_states=lambda _: {},
        _start_task=lambda operation, *args, **kwargs: started.setdefault("operation", operation),
    )
    MainWindow.run_ocr_api_all(stub)
    return started["operation"]


def test_every_page_is_read_and_progress_only_moves_forward(tmp_path):
    pages, regions = chapter(tmp_path)
    ocr = OCRManager()
    ocr._session = Mock()
    active, peak, lock = [0], [0], threading.Lock()

    def post(*_args, **_kwargs):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(.05)
        with lock:
            active[0] -= 1
        return Reply("texto")

    ocr._session.post.side_effect = post
    seen = []
    results = window(pages, regions, ocr)(lambda value, *_: seen.append(value), lambda: False)
    assert set(results) == set(regions)
    assert all(len(results[key]) == len(regions[key]) for key in regions)
    assert peak[0] <= ocr.parallelism
    assert seen[-1] == 100


def test_a_dead_connection_stops_the_following_pages(tmp_path):
    pages, regions = chapter(tmp_path, (6, 6, 6, 6))
    ocr = OCRManager()
    ocr._session = Mock()
    ocr._session.post.side_effect = ConnectionResetError(10054, "reset")
    results = window(pages, regions, ocr)(lambda *_: None, lambda: False)
    sent = ocr._session.post.call_count
    # Each of the two pages in progress stops after a few failures (plus the
    # requests already in flight); the remaining pages are never started.
    assert sent <= (OCRManager.NETWORK_ABORT_AFTER + 2) * 2
    assert len(results) == 2
    assert all(item.get(OCRManager.FAILED_KEY) for items in results.values() for item in items)


def test_a_configuration_error_still_fails_the_task(tmp_path):
    from core.ocr_manager import OCRConfigurationError
    pages, regions = chapter(tmp_path)
    ocr = OCRManager()
    ocr._session = Mock()
    ocr._session.post.return_value = Mock(status_code=401, json=lambda: {"error": {"message": "bad key"}})
    with pytest.raises(OCRConfigurationError):
        window(pages, regions, ocr)(lambda *_: None, lambda: False)
