"""Cancellable workers for real CPU/GPU model inference."""
from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal


_log_root = Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "ManhuaSuiteEditor"
_log_root.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=_log_root / "errors.log",
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(message)s",
)


class WorkerSignals(QObject):
    progress = Signal(int)
    stage = Signal(str, str)
    partial = Signal(object)
    completed = Signal(object)
    failed = Signal(str)


class ModelTask(QRunnable):
    """Runs a real model operation away from the GUI thread."""

    def __init__(self, operation: Callable[[Callable[[int], None], Callable[[], bool]], object]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            def report(value: int, stage: str = "", page_key: str = "") -> None:
                if stage:
                    self.signals.stage.emit(stage, page_key)
                self.signals.progress.emit(int(value))

            result = self.operation(report, lambda: self._cancelled)
            if self._cancelled:
                self.signals.failed.emit("Operación cancelada")
            else:
                self.signals.completed.emit(result)
        except Exception as error:
            logging.exception("Fallo en una tarea de modelo")
            self.signals.failed.emit("Operación cancelada" if self._cancelled else str(error))
