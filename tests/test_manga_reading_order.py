"""Keep staggered manga boxes aligned across detection, numbering and text."""
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from core.project_manager import Page
from core.project_io import load_project_bundle
from core.typography_manager import TypographyManager
from test_strip_view import app_wait
from ui.main_window import MainWindow


def manga_boxes():
    return [
        {"id": "top", "x": 120, "y": 0, "width": 80, "height": 40, "text": "Arriba"},
        {"id": "left", "x": 20, "y": 98, "width": 80, "height": 40, "text": "Izquierda"},
        {"id": "right", "x": 210, "y": 113, "width": 70, "height": 40, "text": "Derecha"},
        {"id": "bottom", "x": 220, "y": 160, "width": 70, "height": 40, "text": "Abajo"},
    ]


def separated_manga_blocks():
    # The IDs are the original overlay numbers in the reported screenshot.
    return [
        {"id": str(number), "x": x, "y": y, "width": width, "height": height, "text": str(number)}
        for number, x, y, width, height in (
            (1, 863, 55, 73, 211),
            (2, 119, 161, 90, 154),
            (3, 781, 358, 72, 153),
            (4, 178, 358, 52, 54),
            (5, 45, 398, 35, 106),
            (6, 611, 472, 44, 87),
            (7, 155, 481, 75, 106),
        )
    ]


def test_manga_finishes_the_right_block_before_crossing_the_panel():
    regions = separated_manga_blocks()
    ordered = TypographyManager.ordered(regions, True)
    assert [region["id"] for region in ordered] == ["1", "3", "6", "2", "4", "5", "7"]
    assert TypographyManager.ordered(regions[::-1], True) == ordered


def test_manga_finishes_an_upper_panel_row_before_a_lower_one():
    regions = separated_manga_blocks()
    lower = [{**region, "id": f'lower-{region["id"]}', "y": region["y"] + 800} for region in regions]
    ordered = TypographyManager.ordered(lower + regions, True)
    one_panel = ["1", "3", "6", "2", "4", "5", "7"]
    assert [region["id"] for region in ordered] == one_panel + [f"lower-{key}" for key in one_panel]


def test_tall_boxes_below_the_reported_panel_do_not_interrupt_its_order():
    # Full-page geometry from the saved chapter includes three more boxes
    # below the screenshot. Their height must not mask the gap above them.
    regions = [
        {"id": str(number), "x": x, "y": y, "width": width, "height": height}
        for number, x, y, width, height in (
            (1, 1155, 754, 91, 263),
            (2, 225, 886, 112, 192),
            (3, 1053, 1133, 89, 191),
            (4, 299, 1132, 65, 68),
            (5, 133, 1183, 43, 132),
            (6, 840, 1275, 55, 108),
            (7, 271, 1286, 93, 133),
            (8, 770, 1520, 78, 371),
            (9, 120, 1551, 173, 387),
            (10, 1101, 1765, 180, 225),
        )
    ]
    ordered = [box["id"] for box in TypographyManager.ordered(regions, True)]
    assert ordered == ["1", "3", "6", "2", "4", "5", "7", "10", "8", "9"]


def test_regular_grid_reads_each_row_before_the_next():
    regions = [
        {"id": f"{column}-{y}", "x": x, "y": y, "width": 70, "height": 100}
        for y in (0, 120) for column, x in (("left", 0), ("right", 500))
    ]
    assert [region["id"] for region in TypographyManager.ordered(regions, True)] == [
        "right-0", "left-0", "right-120", "left-120",
    ]


def test_staggered_balloons_in_one_manga_row_read_right_first():
    assert [r["id"] for r in TypographyManager.ordered(manga_boxes(), True)] == [
        "top", "right", "left", "bottom",
    ]


def test_reordering_mixed_height_manga_boxes_is_stable():
    regions = [
        {"id": "tall-left", "x": 10, "y": 0, "width": 40, "height": 80},
        {"id": "short-right", "x": 160, "y": 0, "width": 40, "height": 12},
        {"id": "middle", "x": 80, "y": 20, "width": 40, "height": 40},
    ]
    expected = ["short-right", "middle", "tall-left"]
    ordered = TypographyManager.ordered(regions[::-1], True)
    assert [region["id"] for region in ordered] == expected
    assert TypographyManager.ordered(ordered, True) == ordered


@pytest.fixture
def editor(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    pages = []
    for index in range(2):
        path = tmp_path / f"{index}.png"
        Image.new("RGB", (1000, 640), "white").save(path)
        pages.append(Page(path.name, path, 1000, 640))
    with (
        patch.object(MainWindow, "_offer_recovery"),
        patch.object(MainWindow, "_import_legacy_profiles_async"),
        patch.object(MainWindow, "_warm_up_models"),
        patch.object(MainWindow, "_check_for_updates"),
    ):
        window = MainWindow()
        window.project.pages = pages
        window.page_styles = {
            str(page.path): {"ocr": {"manga_mode": False}, "translation": {"manga_mode": False}}
            for page in pages
        }
        window.show()
        window._set_page(0)
        app_wait(app, lambda: window._displayed_page_path == pages[0].path)
        yield window, pages, app
        window.close()


def test_existing_boxes_get_the_new_numbers_and_keep_their_text(editor):
    window, pages, _ = editor
    key = str(pages[0].path)
    original = separated_manga_blocks()
    for box in original:
        box["number"] = int(box["id"])
        box["translation"] = "Translation " + box["id"]
    window.page_regions[key] = original
    window.page_styles[key]["translation"]["manga_mode"] = True
    window._set_page(0)

    assert [item.region["number"] for item in window.canvas_shell.canvas._regions] == [1, 4, 2, 5, 6, 3, 7]
    assert window._numbered_page_content(key, "translation").splitlines() == [
        f"{number}. Translation {old}" for number, old in enumerate((1, 3, 6, 2, 4, 5, 7), 1)
    ]


@pytest.mark.parametrize("chapter", [False, True])
def test_detection_uses_one_order_for_layers_numbers_and_copied_text(editor, monkeypatch, tmp_path, chapter):
    window, pages, app = editor
    window.ai_panel._style_widgets["ocr"]["manga"].setChecked(True, emit=True)
    monkeypatch.setattr(window.detector, "detect", lambda *args: manga_boxes())
    window.ai_panel.detect_scope.setCurrentIndex(int(chapter))
    window.ai_panel.detect_button.click()
    app_wait(app, lambda: window.current_task is None)

    expected = ["top", "right", "left", "bottom"]
    for page in pages if chapter else pages[:1]:
        key = str(page.path)
        assert [r["id"] for r in window.page_regions[key]] == expected
        assert [r["number"] for r in window.page_regions[key]] == [1, 2, 3, 4]
        assert window._numbered_page_content(key, "ocr").splitlines() == [
            "1. Arriba", "2. Derecha", "3. Izquierda", "4. Abajo",
        ]

    if chapter:
        window._set_page(1)
        app_wait(app, lambda: window._displayed_page_path == pages[1].path)
        assert window.ai_panel.style_options("ocr")["manga_mode"]
        assert window.ai_panel.style_options("translation")["manga_mode"]
        assert [item.region["id"] for item in window.canvas_shell.canvas._regions] == expected
        window.project_file = tmp_path / "manga.mseproj"
        window.save_project()
        saved = load_project_bundle(window.project_file)
        for page in pages:
            assert saved["page_styles"][str(page.path)]["ocr"]["manga_mode"]
            assert [r["id"] for r in saved["page_regions"][str(page.path)]] == expected
