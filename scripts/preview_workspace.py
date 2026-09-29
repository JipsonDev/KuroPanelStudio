"""Render the real Qt workspace offscreen with disposable settings, no API calls."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "graphify-out" / "ui-preview")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory() as temp:
        os.environ["LOCALAPPDATA"] = temp
        from unittest.mock import patch
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QColor, QImage, QPainter, QFont, QFontDatabase
        from ui.main_window import MainWindow
        app = QApplication.instance() or QApplication([])
        # Qt's offscreen plugin does not enumerate Windows system fonts.
        font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuisl.ttf"):
            if (font_root / name).exists():
                QFontDatabase.addApplicationFont(str(font_root / name))
        with (patch.object(MainWindow, "_offer_recovery"),
              patch.object(MainWindow, "_import_legacy_profiles_async"),
              patch.object(MainWindow, "_warm_up_models"),
              patch.object(MainWindow, "_toast")):
            window = MainWindow()
            window._psd_auto_sync = False
            window.show()
            for width, height in ((1600, 900), (1440, 900), (1024, 768), (800, 640), (720, 540)):
                window.resize(width, height)
                app.processEvents()
                window.grab().save(str(args.output / f"empty-{width}.png"))
                print(f"requested={width}x{height} actual={window.width()}x{window.height()} canvas={window.canvas_shell.width()} dock={window.workspace.dock.width()}")
            window.resize(1440, 900)
            window.workspace.adapt_to_width(1440)
            page = QImage(str(ROOT / "assets/sample/fantasy-fire-page.png"))
            if page.isNull():
                raise RuntimeError("Falta el arte de ejemplo del editor")
            from core.project_manager import Page
            sample_path = ROOT / "assets/sample/fantasy-fire-page.png"
            window.project.pages = [Page(sample_path.name, sample_path, page.width(), page.height(), "Muestra")]
            window.images_panel.refresh_pages(window.project.pages)
            window.canvas_shell.canvas.set_image(page)
            window.canvas_shell.canvas.set_regions([])
            window.layers.set_image_layers(True, False, {})
            window._refresh_page_statuses()
            thumbnail_wait = QEventLoop()
            QTimer.singleShot(400, thumbnail_wait.quit)
            thumbnail_wait.exec()
            window.canvas_shell.page_label.setText("1 / 1")
            window.inspector.update_page(0, 1)
            window.status.set_page(sample_path.name, page.width(), page.height(), f"{sample_path.stat().st_size / 1048576:.1f} MB")
            app.processEvents()
            window.canvas_shell.canvas.fit_image()
            app.processEvents()
            window.grab().save(str(args.output / "editor-1440.png"))
            window.resize(1600, 900)
            app.processEvents()
            window.canvas_shell.canvas.fit_image()
            window.grab().save(str(args.output / "editor-1600.png"))
            window.sidebar._select("Capas")
            app.processEvents()
            window.grab().save(str(args.output / "layers-1600.png"))
            window.sidebar._select("Páginas")
            window.images_panel.grid_button.click()
            app.processEvents()
            window.grab().save(str(args.output / "pages-grid-1600.png"))
            window.images_panel.list_button.click()
            app.processEvents()
            window.resize(1440, 900)
            app.processEvents()
            window.workspace.dock.setCurrentIndex(1)
            app.processEvents()
            window.grab().save(str(args.output / "inspector-1440.png"))
            window.workspace.show_tools()
            window.workspace.set_focus_mode(True)
            app.processEvents()
            window.canvas_shell.canvas.fit_image()
            window.grab().save(str(args.output / "focus-1440.png"))
            window.workspace.set_focus_mode(False)
            window.task_progress.start("OCR", "Esperando respuesta de Alibaba OCR…")
            app.processEvents()
            window.grab().save(str(args.output / "progress-waiting.png"))
            window.task_progress.update_progress(40)
            app.processEvents()
            window.grab().save(str(args.output / "progress-1440.png"))
            window.resize(720, 540)
            app.processEvents()
            window.grab().save(str(args.output / "progress-720.png"))
            window.canvas_shell.panel_button.click()
            app.processEvents()
            window.grab().save(str(args.output / "canvas-compact.png"))
            window.canvas_shell.panel_button.click()
            app.processEvents()
            window.sidebar._select("Páginas")
            window.workspace.dock.setCurrentWidget(window.workspace.assets)
            app.processEvents()
            window.grab().save(str(args.output / "pages-720.png"))
            window.sidebar._select("Capas")
            app.processEvents()
            window.grab().save(str(args.output / "layers-720.png"))
            window.workspace.dock.setCurrentWidget(window.workspace.tools)
            window.task_progress.finish(error="HTTP 404: modelo no disponible. Revisa la región y el modelo en Configuración.")
            app.processEvents()
            window.grab().save(str(args.output / "progress-error.png"))
            window.task_progress.hide()
            window.resize(1440, 900)
            window.ai_panel.mode_buttons["translation"].setChecked(True)
            app.processEvents()
            window.grab().save(str(args.output / "translation-workspace.png"))
            window._models_ready = True  # Preview only: do not start model warm-up threads.
            window.ai_panel.mode_buttons["clean"].setChecked(True)
            app.processEvents()
            window.grab().save(str(args.output / "clean-workspace.png"))
            window.ai_panel.set_mask_preview_active(True)
            app.processEvents()
            window.grab().save(str(args.output / "clean-review.png"))
            window.resize(720, 540)
            app.processEvents()
            window.grab().save(str(args.output / "clean-compact.png"))
            window.close()
            app.processEvents()
        import logging
        logging.shutdown()


if __name__ == "__main__":
    main()
