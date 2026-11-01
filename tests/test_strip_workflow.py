"""The chapter overview, manual marks and export share project coordinates."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from core.project_manager import Page
from core.watermark_manager import normalized_watermark
from ui.main_window import MainWindow
from test_strip_view import app_wait


def test_watermark_summary_explains_saved_positions_that_override_automatic_layout():
    from ui.watermark_dialog import WatermarkDialog
    app = QApplication.instance() or QApplication([])
    dialog = WatermarkDialog(dict(page_positions={"a.png": [[1, 2], [3, 4]], "b.png": [[5, 6]]}))
    dialog.set_preview_info((800, 1000), dict(page_positions={"a.png": [[1, 2], [3, 4]], "b.png": [[5, 6]]}), 4)
    assert "3" in dialog.plan_summary.text()
    assert "manual" in dialog.plan_summary.text()
    assert "Redistribuir" in dialog.plan_summary.text()
    dialog.close()


def test_main_window_strip_export_and_watermark_delete_do_not_edit_hidden_page(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    pages = []
    for i in range(2):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", (800, 1000), "white").save(path)
        pages.append(Page(path.name, path, 800, 1000))
    stream = BytesIO()
    Image.new("RGBA", (100, 50), "red").save(stream, "PNG")
    with (patch.object(MainWindow, "_offer_recovery"),
          patch.object(MainWindow, "_import_legacy_profiles_async"),
          patch.object(MainWindow, "_warm_up_models"),
          patch.object(MainWindow, "_check_for_updates")):
        window = MainWindow()
        window.resize(1280, 800)
        window.show()
        window.project.pages = pages
        window._set_page(0)
        app_wait(app, lambda: window._displayed_page_path == pages[0].path)
        window.watermark_settings = normalized_watermark(dict(
            png_bytes=stream.getvalue(), repeat=True, repeat_count=2, auto_count=False,
            minimum_gap=100, size_mode="pixels", width_px=100, opacity=100,
            enabled_pages=[page.name for page in pages]))
        window.canvas_shell.strip_button.click()
        view = window.canvas_shell.strip_view
        app_wait(app, lambda: bool(view._tiles))
        assert window.canvas_shell.stage_layout.currentWidget() is view
        assert window.canvas_shell.canvas._pixmap_item.pixmap().isNull()
        assert not window.tool_panel.isEnabled()
        assert len(window.project.pages) == 2
        view.jump_to_page(1)
        app_wait(app, lambda: view.visible_page() == 1)
        item = view._watermarks["1.png"][0]
        item.setPos(24, 333)
        view._emit_watermark_positions("1.png")
        app.processEvents()
        assert window.watermark_settings["page_positions"]["1.png"] == [[24, 333]]
        output = tmp_path / "combined.png"
        monkeypatch.setattr("ui.main_window.QFileDialog.getSaveFileName", lambda *_: (str(output), "PNG"))
        # Replace scheduling only: use the actual snapshot, compositor and PNG writer.
        window._start_task = lambda work, *_args, **_kw: work(lambda _: None, lambda: False)
        window.save_strip()
        with Image.open(output) as image:
            assert image.size == (800, 2000)
            assert image.getpixel((50, 1340)) == (255, 0, 0)
        view._watermarks["1.png"][0].setSelected(True)
        window._delete_active_region_action()
        assert window.watermark_settings["page_positions"]["1.png"] == []
        window._remove_watermark_current()
        assert window.watermark_settings["enabled_pages"] == ["0.png"]
        window._apply_watermark_current()
        assert set(window.watermark_settings["enabled_pages"]) == {"0.png", "1.png"}
        window._edit_strip_page(1)
        assert window.project.active_index == 1
        assert window.tool_panel.isEnabled()
        assert not window.canvas_shell.strip_button.isChecked()
        window.watermark_settings["page_positions"] = {"0.png": [[20, 20]], "1.png": [[20, 40]]}
        window.open_watermark_editor()
        window.watermark_dialog.recommended_button.click()
        assert window.watermark_settings["page_positions"] == {}
        assert window.watermark_settings["auto_count_limit"] == 24
        assert window.watermark_settings["enabled_pages"] == ["0.png", "1.png"]
        window.watermark_dialog.close()
        window.close()
        from core.settings_manager import SettingsManager
        saved = SettingsManager(window.settings.path).data["watermark"]
        assert saved["auto_count_limit"] == 24 and saved["interval_px"] == 7000
        assert not window._memory_pressure_timer.isActive()
        assert not window._model_idle_timer.isActive()
