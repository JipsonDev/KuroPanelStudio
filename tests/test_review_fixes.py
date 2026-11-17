"""Fixes from the 0.2.28 review: lost readings, cut balloons, stale sizes."""
import os
import time
from types import SimpleNamespace

import pytest

from core.chapter_font_size import unify_chapter_font_size
from core.ocr_manager import OCRManager
from core.project_io import _local_file
from core.seam_balloons import purge_stitched_files
from core.workflow_status import page_workflow_status


def test_seam_child_counts_as_done_and_is_not_read_alone():
    anchor = {"id": "a", "text": "TODO EL GLOBO", "translation": "x", "translation_completed": True,
              "applied_text": "x", "style": {"a": 1}, "seam_partner": "b"}
    child = {"id": "b", "text": "", "seam_partner": "a", "seam_child": True, "ocr_checked": True}
    status = page_workflow_status([child])
    assert not status["detected"]  # nothing of its own left to do on that page
    status = page_workflow_status([anchor, child])
    assert status["ocr"] and status["translated"] and status["typeset"]
    assert not OCRManager.needs_recognition(child)
    assert OCRManager.needs_recognition({**child, "ocr_stale": True})


def test_old_joined_crops_are_purged(tmp_path):
    old, new = tmp_path / "old.png", tmp_path / "new.png"
    old.write_bytes(b"x"); new.write_bytes(b"x")
    past = time.time() - 30 * 86400
    os.utime(old, (past, past))
    assert purge_stitched_files(tmp_path) == 1
    assert not old.exists() and new.exists()


def test_network_paths_in_a_project_are_not_opened(tmp_path):
    from pathlib import Path
    local = tmp_path / "a.png"
    local.write_bytes(b"x")
    assert _local_file(local)
    assert not _local_file(Path("//server/share/a.png"))
    assert not _local_file(Path("\\\\server\\share\\a.png"))


def test_a_size_set_after_unifying_is_respected():
    region = {"text": "Hola", "style": {"font_size": 40, "auto_fit": True}}
    pages = {"1": [region]}
    unify_chapter_font_size(pages, 28)
    assert region["style"]["font_size"] == 28
    region["style"]["font_size"] = 22  # a font role chosen later
    unify_chapter_font_size(pages, 30)
    assert region["style"]["font_size"] == 22
    unify_chapter_font_size(pages, 18)
    assert region["style"]["font_size"] == 18


def window_stub(**extra):
    pytest.importorskip("PySide6")
    return SimpleNamespace(**extra)


def test_ocr_reading_reaches_the_box_that_replaced_the_original():
    pytest.importorskip("PySide6")
    from ui.main_window import MainWindow
    original = {"id": "r1", "text": "", "x": 1, "style": {"font_size": 10}}
    replacement = {"id": "r1", "text": "", "x": 50, "style": {"font_size": 30}}  # moved while the OCR ran
    live = [replacement]
    recognized = [{**original, "_ocr_request_token": "t", "text": "你好"}]
    MainWindow._merge_ocr_results(None, recognized, {"t": original}, live)
    assert replacement["text"] == "你好" and replacement["x"] == 50 and replacement["style"]["font_size"] == 30
    # A deleted box drops the reading instead of failing.
    MainWindow._merge_ocr_results(None, recognized, {"t": original}, [])


def test_rereading_half_of_a_cut_balloon_releases_both_halves():
    pytest.importorskip("PySide6")
    from ui.main_window import MainWindow
    anchor = {"id": "a", "text": "TODO", "seam_partner": "b", "ocr_checked": True}
    child = {"id": "b", "text": "", "seam_partner": "a", "seam_child": True, "ocr_checked": True}
    orphan = {"id": "c", "text": "", "seam_partner": "gone", "seam_child": True, "ocr_checked": True}
    window = window_stub(page_regions={"1": [anchor], "2": [child, orphan]})
    MainWindow._release_broken_seams(window)
    assert anchor.get("seam_partner") == "b" and "seam_partner" not in orphan and orphan["ocr_stale"]
    MainWindow._release_broken_seams(window, [anchor])
    assert "seam_partner" not in anchor and "seam_child" not in child
    assert OCRManager.needs_recognition(anchor) and OCRManager.needs_recognition(child)


def test_unsaved_work_is_kept_unless_discarded():
    pytest.importorskip("PySide6")
    from ui.main_window import MainWindow
    saved = []
    window = window_stub(current_task=None, project=SimpleNamespace(pages=[1]), _dirty=True,
                         _toast=lambda *a, **k: None)
    window.save_project = lambda: (saved.append(1), setattr(window, "_dirty", False))
    window._unsaved_choice = lambda action: "cancel"
    assert not MainWindow._may_replace_work(window, "abrir otro capítulo")
    window._unsaved_choice = lambda action: "save"
    assert MainWindow._may_replace_work(window, "abrir otro capítulo") and saved
    window._dirty, window.current_task = True, object()
    assert not MainWindow._may_replace_work(window, "abrir otro capítulo")
