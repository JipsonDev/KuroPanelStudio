"""Foreground feedback independent of dock tabs and focus mode."""
from time import monotonic
from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout
from core.failure_messages import describe_failure, format_remaining
from ui.widgets.icons import icon


class TaskProgress(QFrame):
    active_changed = Signal(bool)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskProgress")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(5)
        row = QHBoxLayout()
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        self.label.setMinimumWidth(0)
        row.addWidget(self.label, 1)
        self.elapsed = QLabel()
        row.addWidget(self.elapsed)
        self.complete_icon = QLabel()
        self.complete_icon.setObjectName("ProgressCompleteIcon")
        self.complete_icon.setPixmap(icon("check", "#20D985", 20).pixmap(20, 20))
        self.complete_icon.hide()
        row.addWidget(self.complete_icon)
        self.alert_icon = QLabel("!")
        self.alert_icon.setObjectName("ProgressAlertIcon")
        self.alert_icon.setAlignment(Qt.AlignCenter)
        self.alert_icon.setFixedSize(20, 20)
        self.alert_icon.hide()
        row.addWidget(self.alert_icon)
        self.details = QPushButton("Ver detalle")
        self.details.clicked.connect(self._show_details)
        row.addWidget(self.details)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setAccessibleName("Cancelar operación en curso")
        self.cancel_button.clicked.connect(self.request_cancel)
        row.addWidget(self.cancel_button)
        self.dismiss = QPushButton("Cerrar")
        self.dismiss.setObjectName("ProgressDismiss")
        self.dismiss.clicked.connect(self.hide)
        row.addWidget(self.dismiss)
        layout.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setAccessibleName("Progreso de la operación")
        self.bar.setFixedHeight(9)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self._text = ""
        self._error = ""
        self._explanation = ""
        self._dialog = None
        self._owner = None
        self._stage = ""
        self._cancel_pending = False
        self.label.installEventFilter(self)
        self.hide()

    def start(self, title: str, stage: str = "", owner: str = "foreground"):
        self._owner = owner
        self._title = title.removesuffix(" completado")
        self._stage = stage or "Preparando…"
        self._text = f"{self._title} · {self._stage}"
        self._started = monotonic()
        self._error = ""
        self._explanation = ""
        self._cancel_pending = False
        self._set_state("running")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.details.hide()
        self.complete_icon.hide()
        self.alert_icon.hide()
        self.dismiss.hide()
        self.bar.show()
        self.bar.setRange(0, 0)
        self._tick()
        self.timer.start()
        self.show()
        self.active_changed.emit(True)

    def update_progress(self, value: int, owner: str = "foreground"):
        if self._owner != owner or self._cancel_pending:
            return
        value = max(0, min(100, int(value)))
        if value == 0:
            return
        if value == 100:
            self.bar.setRange(0, 0)
            self._text = f"{self._title} · Finalizando…"
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(value)
            self._text = f"{self._title} · {self._stage} · {value}%"
        self._refresh_label()

    def set_stage(self, stage: str, owner: str = "foreground") -> None:
        if self._owner != owner or self._cancel_pending:
            return
        self._stage = stage
        suffix = f" · {self.bar.value()}%" if self.bar.maximum() else ""
        self._text = f"{self._title} · {stage}{suffix}"
        self._refresh_label()

    def request_cancel(self) -> None:
        if self._owner is None or self._cancel_pending:
            return
        self._cancel_pending = True
        self._text = f"{self._title} · Cancelando…"
        self.cancel_button.setEnabled(False)
        self._refresh_label()
        self.cancel_requested.emit()

    def finish(
        self, error: str = "", owner: str = "foreground", cancelled: bool = False,
        warning: str = "", warning_detail: str = "",
    ):
        """Show the outcome: completed, completed with warnings, cancelled or failed.

        An error is shown as its cause and the action to take; the raw text
        stays available under «Ver detalle».
        """
        if self._owner != owner:
            return
        self.timer.stop()
        self._error = error
        if cancelled:
            outcome, state = "Cancelado · no se aplicó ningún resultado", "cancelled"
            self._explanation = ""
        elif error:
            failure = describe_failure(self._title, error)
            outcome, state = failure.title, "error"
            self._explanation = f"{failure.title}.\n\n{failure.action}\n\nDetalle técnico:\n{error}"
        elif warning:
            outcome, state = warning, "warning"
            self._explanation = f"{warning}\n\n{warning_detail}".strip()
        else:
            outcome, state = "Completado", "done"
            self._explanation = ""
        self._text = f"{self._title} · {outcome}"
        self.bar.setRange(0, 100)
        self.bar.setValue(0 if error or cancelled else 100)
        self.bar.setVisible(state in {"done", "warning"})
        self.details.setVisible(state in {"error", "warning"})
        self.complete_icon.setVisible(state == "done")
        self.alert_icon.setVisible(state in {"error", "warning"})
        self.cancel_button.hide()
        self.dismiss.show()
        self._owner = None
        self._set_state(state)
        self._refresh_label()
        self.active_changed.emit(False)

    def _set_state(self, state: str) -> None:
        if self.property("state") == state:
            return
        self.setProperty("state", state)
        style = self.style()
        for widget in (self, self.label, self.alert_icon, self.bar):
            style.unpolish(widget)
            style.polish(widget)

    def _tick(self):
        elapsed = monotonic() - self._started
        seconds = int(elapsed)
        clock = f"{seconds // 60:02d}:{seconds % 60:02d}"
        remaining = format_remaining(elapsed, self.bar.value()) if self.bar.maximum() == 100 else ""
        self.elapsed.setText(f"{clock} · {remaining}" if remaining and self.timer.isActive() else clock)
        self._refresh_label()

    def _refresh_label(self):
        self.label.setText(self.label.fontMetrics().elidedText(self._text, Qt.ElideRight, self.label.width()))
        self.label.setToolTip(self._text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_label()

    def eventFilter(self, watched, event):
        if watched is self.label and event.type() == QEvent.Resize:
            self._refresh_label()
        return super().eventFilter(watched, event)

    def _show_details(self):
        title = "Proceso incompleto" if self.property("state") == "warning" else "Error de proceso"
        text = self._explanation or self._error
        self._dialog = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, self)
        self._dialog.setTextFormat(Qt.PlainText)
        self._dialog.open()
