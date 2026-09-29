"""Entry point for KuroPanel Studio."""
import faulthandler
import multiprocessing
import os
import sys
import traceback
from pathlib import Path
from time import perf_counter

_crash_stream = None


def _install_crash_log() -> None:
    """Persist Python and native crash diagnostics instead of failing silently."""
    global _crash_stream
    log_root = Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "ManhuaSuiteEditor"
    log_root.mkdir(parents=True, exist_ok=True)
    _crash_stream = (log_root / "crash.log").open("a", encoding="utf-8", buffering=1)
    faulthandler.enable(_crash_stream, all_threads=True)

    def report_exception(exc_type, exc_value, exc_traceback) -> None:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=_crash_stream)
        _crash_stream.flush()

    sys.excepthook = report_exception


def main() -> int:
    startup_started = perf_counter()
    # Keep Qt and the complete UI graph out of multiprocessing's spawned LaMa
    # worker. This both shortens worker startup and is required by frozen
    # Windows builds, where freeze_support must run before application imports.
    multiprocessing.freeze_support()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QSplashScreen

    _install_crash_log()
    app = QApplication(sys.argv)
    app.setApplicationName("KuroPanel Studio")
    app.setOrganizationName("KuroPanel")
    from core.settings_manager import SettingsManager
    ui_language = SettingsManager(
        Path(os.environ.get("LOCALAPPDATA", str(Path.cwd())))
        / "ManhuaSuiteEditor" / "settings.json"
    ).data["general"]["ui_language"]
    english_ui = ui_language == "en"
    icon_path = Path(__file__).resolve().parent / "assets" / "icons" / "app_icon.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Show a responsive first frame before importing the complete editor
    # graph.  On a cold Windows start, Qt/OpenCV/NumPy DLL discovery can take
    # several seconds even though MainWindow itself is fast to construct.
    # Previously that whole interval looked like a frozen launch.
    splash_image = QPixmap(520, 230)
    splash_image.fill(QColor("#101317"))
    painter = QPainter(splash_image)
    gradient = QLinearGradient(0, 0, 520, 230)
    gradient.setColorAt(0.0, QColor("#101317"))
    gradient.setColorAt(1.0, QColor("#182126"))
    painter.fillRect(splash_image.rect(), gradient)
    painter.setPen(QColor("#12C6D7"))
    painter.setFont(QFont("Segoe UI", 22, QFont.Weight.DemiBold))
    painter.drawText(32, 88, "KuroPanel Studio")
    painter.setPen(QColor("#AFC2CA"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(34, 120, "Preparing workspace…" if english_ui else "Preparando el espacio de trabajo…")
    painter.setPen(QColor("#263941"))
    painter.drawLine(34, 151, 486, 151)
    painter.setPen(QColor("#6E8A94"))
    painter.drawText(34, 183, "OCR · cleaning · translation · typesetting" if english_ui else "OCR · limpieza · traducción · rotulación")
    painter.end()
    splash = QSplashScreen(splash_image)
    splash.show()
    splash.showMessage(
        "Loading interface…" if english_ui else "Cargando interfaz…",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
        QColor("#D7E5EA"),
    )
    app.processEvents()

    from ui.main_window import MainWindow

    splash.showMessage(
        "Restoring settings…" if english_ui else "Restaurando configuración…",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
        QColor("#D7E5EA"),
    )
    app.processEvents()
    window = MainWindow()
    window.show()
    splash.finish(window)
    window.record_startup_time(perf_counter() - startup_started)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
