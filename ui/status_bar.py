from __future__ import annotations

import threading

import psutil
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class StatusBar(QFrame):
    """Live, non-blocking CPU/RAM/GPU/VRAM and operation monitor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(31)
        layout = QHBoxLayout(self); layout.setContentsMargins(12, 0, 12, 0); layout.setSpacing(14)
        self.resources = QLabel(); self.resources.setStyleSheet("color:#00CFE8; font-size:10px;")
        layout.addWidget(self.resources)
        self.operation = QLabel("Sin operaciones medidas"); self.operation.setObjectName("Muted")
        layout.addWidget(self.operation)
        layout.addStretch()
        self.metadata = QLabel("Sin imagen"); self.metadata.setObjectName("Muted"); layout.addWidget(self.metadata)
        layout.addStretch()
        version = QLabel("v0.1.0"); version.setObjectName("Muted"); layout.addWidget(version)
        sync = QLabel("●  Todo sincronizado"); sync.setStyleSheet("color:#38D477; font-size:10px;"); layout.addWidget(sync)
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
        self.metadata.setText(f"{name}   |   {width} × {height} px   |   {size}")
