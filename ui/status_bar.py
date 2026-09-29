from __future__ import annotations

import threading

import psutil
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QWidget


class StatusBar(QFrame):
    """Live, non-blocking CPU/RAM/GPU/VRAM and operation monitor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(38)
        layout = QHBoxLayout(self); layout.setContentsMargins(14, 0, 14, 0); layout.setSpacing(12)
        # Keep collecting diagnostics, but expose them on demand instead of
        # filling the status strip with four constantly changing counters.
        self.resources = QLabel()
        self.operation = QLabel("Sin operaciones medidas")
        self._page_details = "Sin imagen"
        self.activity = QLabel("●  Listo")
        self.activity.setStyleSheet("color:#44E58A; font-size:12px; font-weight:600;")
        layout.addWidget(self.activity)
        layout.addStretch(1)
        self.metadata = QLabel("Sin imagen")
        self.metadata.setObjectName("StatusPage")
        self.metadata.setMinimumWidth(0)
        self.metadata.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.metadata, 3)
        layout.addStretch(1)
        self.details = QToolButton()
        self.details.setObjectName("StatusDetails")
        self.details.setText("Detalles")
        self.details.setToolTip("Ver uso de recursos y tiempos de procesamiento")
        self.details.setPopupMode(QToolButton.InstantPopup)
        self.details_menu = QMenu(self.details)
        self.details_menu.aboutToShow.connect(self._populate_details_menu)
        self.details.setMenu(self.details_menu)
        layout.addWidget(self.details)
        self._nvml = None
        self._gpu_handle = None
        self._gpu_snapshot = "GPU —   VRAM —"
        self._gpu_process = QProcess(self)
        self._gpu_process.finished.connect(self._gpu_query_finished)
        self._gpu_process.errorOccurred.connect(self._gpu_query_failed)
        self._gpu_query_supported = True
        self._profile_name = "Equilibrado"
        self._performance_tooltip = ""
        QTimer.singleShot(2500, self._initialize_gpu_monitor)
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(2000); self.refresh()

    def _initialize_gpu_monitor(self) -> None:
        threading.Thread(target=self._load_gpu_monitor, name="gpu-monitor-init", daemon=True).start()

    def _load_gpu_monitor(self) -> None:
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            pass

    def refresh(self) -> None:
        memory = psutil.virtual_memory()
        cpu = psutil.cpu_percent()
        snapshot = self._gpu_snapshot
        if self._nvml and self._gpu_handle:
            try:
                gpu = self._nvml.nvmlDeviceGetUtilizationRates(self._gpu_handle).gpu
                gpu_memory = self._nvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                snapshot = f"GPU {gpu}%   VRAM {gpu_memory.used / 2**30:.1f}/{gpu_memory.total / 2**30:.1f} GB"
            except Exception:
                pass
        elif self._gpu_query_supported and self._gpu_process.state() == QProcess.NotRunning:
            self._gpu_process.start(
                "nvidia-smi",
                ["--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            )
        self.resources.setText(
            f"RAM {memory.percent:.0f}% ({memory.used / 2**30:.1f}/{memory.total / 2**30:.1f} GB)   "
            f"CPU {cpu:.0f}%   {snapshot}"
        )

    def _gpu_query_finished(self, _exit_code: int, _status) -> None:
        output = bytes(self._gpu_process.readAllStandardOutput()).decode("utf-8", "ignore").strip().splitlines()
        if not output:
            self._gpu_query_supported = False
            return
        try:
            usage, used, total = (int(value.strip()) for value in output[0].split(",")[:3])
            self._gpu_snapshot = f"GPU {usage}%   VRAM {used / 1024:.1f}/{total / 1024:.1f} GB"
        except (ValueError, IndexError):
            self._gpu_query_supported = False

    def _gpu_query_failed(self, _error) -> None:
        self._gpu_query_supported = False
        self._gpu_snapshot = "GPU —   VRAM —"

    def set_refresh_interval(self, milliseconds: int) -> None:
        self.timer.setInterval(max(1500, int(milliseconds)))

    def set_operation_time(self, name: str, seconds: float) -> None:
        milliseconds = seconds * 1000.0
        duration = f"{milliseconds:.0f} ms" if milliseconds < 1000 else f"{seconds:.2f} s"
        self.operation.setText(f"Última: {name} · {duration}")

    def set_busy(self, active: bool) -> None:
        self.activity.setText("●  Procesando" if active else "●  Listo")
        self.activity.setStyleSheet(
            f"color:{'#FFC928' if active else '#44E58A'}; font-size:12px; font-weight:600;"
        )

    def _populate_details_menu(self) -> None:
        self.details_menu.clear()
        for line in (self._page_details, self.resources.text(), self.operation.text()):
            action = self.details_menu.addAction(line)
            action.setEnabled(False)
        if self._performance_tooltip:
            self.details_menu.addSeparator()
            for line in self._performance_tooltip.splitlines():
                if line:
                    action = self.details_menu.addAction(line)
                    action.setEnabled(False)

    def set_performance_snapshot(self, metrics: dict, cache_stats: dict, profile: str) -> None:
        """Expose rolling timings and decoded-page memory without adding UI clutter."""
        profile_names = {
            "low": "Bajo consumo", "balanced": "Equilibrado", "high": "Máximo rendimiento",
        }
        self._profile_name = profile_names.get(str(profile), str(profile).title())
        lines = [f"Perfil activo: {self._profile_name}", "", "Promedio por operación:"]
        if metrics:
            for metric in list(metrics.values())[-8:]:
                milliseconds = float(metric.seconds) * 1000.0
                duration = f"{milliseconds:.0f} ms" if milliseconds < 1000 else f"{metric.seconds:.2f} s"
                lines.append(f"• {metric.name}: {duration} ({metric.samples} muestra(s))")
        else:
            lines.append("• Todavía no hay mediciones")
        pages = dict(cache_stats.get("pages", {}))
        thumbnails = dict(cache_stats.get("thumbnails", {}))
        cache_mb = (int(pages.get("bytes", 0)) + int(thumbnails.get("bytes", 0))) / (1024 * 1024)
        lines.extend([
            "", f"Caché: {cache_mb:.1f} MB · {int(pages.get('items', 0))} página(s)",
            "Pasa el cursor por esta barra para ver los promedios.",
        ])
        self._performance_tooltip = "\n".join(lines)
        self.operation.setToolTip(self._performance_tooltip)
        self.resources.setToolTip(self._performance_tooltip)

    def set_page(self, name: str, width: int, height: int, size: str) -> None:
        self._page_details = f"{name} · {width} × {height} px · {size}"
        self.metadata.setText(f"{name} · {width} × {height} px")
        self.metadata.setToolTip(self._page_details)
