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
    assert status["detected"] and status["ocr"]  # nothing of its own left to do on that page
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


def test_only_the_terms_a_batch_needs_are_sent():
    from core.translation_manager import TranslationManager, relevant_glossary
    glossary = [
        {"source": "林枫", "target": "Lin Feng", "category": "personaje", "note": "protagonista"},
        {"source": "小灵", "target": "Xiao Ling"},
        {"source": "Tianyun Clan", "target": "Clan Tianyun"},
    ]
    assert [term["source"] for term in relevant_glossary(glossary, ["林枫来了"])] == ["林枫", "Tianyun Clan"]
    prompt = TranslationManager._prompt(["林枫来了"], "ZH", "ES", glossary, "", "")
    assert "林枫 = Lin Feng (protagonista)" in prompt and "小灵" not in prompt
    # What changes per batch comes last, after the project's fixed instructions.
    assert prompt.index("Instrucciones permanentes") < prompt.index("Glosario obligatorio") < prompt.index("Entradas:")


def test_a_read_timeout_is_not_sent_again(monkeypatch):
    import requests
    from core.translation_manager import TranslationConfigurationError, TranslationManager
    calls = []

    class Session:
        def post(self, *args, **kwargs):
            calls.append(1)
            raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(TranslationManager, "_get_http_session", classmethod(lambda cls: Session()))
    with pytest.raises(TranslationConfigurationError):
        TranslationManager._request("https://dashscope-intl.aliyuncs.com/x", {}, {})
    assert len(calls) == 1


def test_repeated_watermark_captions_are_flagged_but_dialogue_is_not():
    from core.chapter_detection import mark_repeated_captions
    def box(w, h, confidence=.3, **extra):
        return {"x": 0, "y": 0, "width": w, "height": h, "confidence": confidence, **extra}
    captions = [box(300, 32), box(304, 33), box(298, 31)]
    dialogue = [box(300, 32, confidence=.8), box(200, 150), box(202, 151), box(199, 149)]
    read = box(301, 32, text="ya leído")
    pages = {"1": captions[:2] + dialogue, "2": [captions[2], read]}
    assert mark_repeated_captions(pages) == 3
    assert all(item.get("repeated_caption") for item in captions)
    assert not any(item.get("repeated_caption") for item in dialogue + [read])
    assert not OCRManager.needs_recognition(captions[0])
    assert page_workflow_status([captions[0]])["detected"]


def test_process_chapter_runs_each_step_and_stops_on_a_problem():
    pytest.importorskip("PySide6")
    from ui.main_window import MainWindow
    ran = []
    window = window_stub(current_task=None, _toast=lambda *a, **k: None,
                         inspector=SimpleNamespace(set_stage=lambda *a: None), PIPELINE_STEPS=MainWindow.PIPELINE_STEPS)
    for _label, name in MainWindow.PIPELINE_STEPS:
        setattr(window, name, lambda step=name: ran.append(step))
    window._pipeline = [name for _label, name in MainWindow.PIPELINE_STEPS]
    MainWindow._advance_pipeline(window)  # no step starts a task: all four run in order
    assert ran == [name for _label, name in MainWindow.PIPELINE_STEPS] and not window._pipeline
    window._pipeline = ["run_translation_chapter"]
    MainWindow._pipeline_step_finished(window, ok=False)
    assert window._pipeline == []
