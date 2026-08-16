"""Shared resource policy, cache accounting and operation timing."""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import RLock
from time import perf_counter
from typing import Generic, TypeVar
import os
import sys
import ctypes
from pathlib import Path


T = TypeVar("T")


_cuda_dll_lock = RLock()
_cuda_dll_handles: list[object] = []
_cuda_dll_paths: set[str] = set()
_cuda_library_handles: list[object] = []
_cuda_library_paths: set[str] = set()


def register_cuda_dll_directories(*extra_directories: Path) -> tuple[str, ...]:
    """Keep CUDA DLL search paths alive for delayed cuDNN component loads.

    ``os.add_dll_directory`` stops working as soon as its handle is closed.
    ONNX creates the CUDA provider first and loads cuDNN engines only when a
    convolution executes, so a short-lived context passes startup and then
    fails on the first OCR/LaMa/YOLO operation.
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return ()
    runtime_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    candidates = (
        runtime_root / "torch" / "lib",
        Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib",
        *extra_directories,
    )
    with _cuda_dll_lock:
        for folder in candidates:
            try:
                resolved = str(Path(folder).resolve())
            except OSError:
                continue
            key = resolved.casefold()
            if key in _cuda_dll_paths or not Path(resolved).is_dir():
                continue
            try:
                handle = os.add_dll_directory(resolved)
            except OSError:
                continue
            _cuda_dll_handles.append(handle)
            _cuda_dll_paths.add(key)
        return tuple(sorted(_cuda_dll_paths))


def preload_onnx_cuda(ort_module, *extra_directories: Path) -> bool:
    """Load CUDA/cuDNN through ONNX Runtime's supported preload API."""
    registered = register_cuda_dll_directories(*extra_directories)
    preload = getattr(ort_module, "preload_dlls", None)
    for directory in registered:
        folder = Path(directory)
        if not any(folder.glob("cublas*.dll")) and not any(folder.glob("cudnn*.dll")):
            continue
        try:
            # Load only the CUDA libraries used by our convolutional models.
            # ``preload_dlls(cuda=True)`` also searches for FFT/Solver DLLs
            # that OCR/YOLO/LaMa never use and emits false missing-DLL alerts.
            # Keep the handles alive just like the directory cookies.
            load_order = (
                "nvJitLink_130_0.dll", "nvrtc-builtins64_130.dll", "nvrtc64_130_0.dll",
                "cudart64_13.dll", "cublasLt64_13.dll", "cublas64_13.dll",
            )
            with _cuda_dll_lock:
                for name in load_order:
                    candidate = folder / name
                    key = str(candidate.resolve()).casefold()
                    if not candidate.is_file() or key in _cuda_library_paths:
                        continue
                    _cuda_library_handles.append(ctypes.WinDLL(str(candidate)))
                    _cuda_library_paths.add(key)
            if callable(preload):
                preload(cuda=False, cudnn=True, msvc=True, directory=str(folder))
            return True
        except (OSError, RuntimeError):
            continue
    return False


@dataclass(frozen=True)
class ResourcePolicy:
    """One coherent budget shared by UI, caches and model runtimes."""

    name: str
    ram_mb: int
    physical_cores: int
    image_cache_mb: int
    foreground_workers: int
    io_workers: int
    thumbnail_batch: int
    text_debounce_ms: int
    status_interval_ms: int
    auto_warmup: bool
    model_idle_minutes: int
    balloon_cache_mb: int
    ocr_cache_items: int
    cleaning_cache_mb: int
    onnx_threads: int
    page_cache_radius: int
    text_viewport_margin: float
    retain_offscreen_text: bool


def resource_policy(requested: str | None = "auto", configured_cache_mb: int = 384) -> ResourcePolicy:
    """Detect a safe profile without importing CUDA/PyTorch during startup."""
    try:
        import psutil
        ram_mb = max(1024, int(psutil.virtual_memory().total // (1024 * 1024)))
        physical = int(psutil.cpu_count(logical=False) or max(1, (os.cpu_count() or 2) // 2))
    except ImportError:
        ram_mb = 8192
        physical = max(1, (os.cpu_count() or 2) // 2)
    choice = str(requested or "auto").strip().lower()
    if choice not in {"auto", "low", "balanced", "high"}:
        choice = "auto"
    if choice == "auto":
        if ram_mb <= 8192 or physical <= 4:
            choice = "low"
        elif ram_mb >= 24576 and physical >= 8:
            choice = "high"
        else:
            choice = "balanced"
    cache = max(64, int(configured_cache_mb))
    if choice == "low":
        return ResourcePolicy(
            choice, ram_mb, physical, min(cache, 128), 1, 1, 2, 120, 5000,
            False, 2, 12, 256, 32, max(1, min(2, physical - 1)),
            0, 0.15, False,
        )
    if choice == "high":
        return ResourcePolicy(
            choice, ram_mb, physical, min(cache, 1024), 2, 3, 8, 45, 2000,
            True, 5, 48, 1024, 128, max(2, min(4, physical // 2)),
            2, 0.75, True,
        )
    return ResourcePolicy(
        choice, ram_mb, physical, min(cache, 384), 2, 2, 4, 75, 3000,
        False, 3, 24, 512, 64, max(1, min(3, physical // 2)),
        1, 0.35, True,
    )


def normalize_device_mode(value: str | None) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in {"auto", "gpu", "cpu"} else "auto"


def configure_native_threads(policy: ResourcePolicy) -> None:
    """Keep small OpenCV jobs from spawning every logical CPU core.

    Balloon masks are usually only a few hundred pixels wide. On machines
    with many logical cores, OpenCV's default pool costs more to coordinate
    than the operation itself and can make consecutive masks stutter.
    """
    try:
        import cv2
        cv2.setNumThreads(max(1, min(4, int(policy.physical_cores))))
    except (ImportError, AttributeError):
        pass


def warm_native_image_ops() -> None:
    """Initialize OpenCV kernels in an I/O worker before first text layout."""
    try:
        import cv2
        import numpy as np
        sample = np.zeros((32, 32, 3), dtype=np.uint8)
        cv2.cvtColor(sample, cv2.COLOR_RGB2LAB)
        cv2.connectedComponentsWithStats(np.full((32, 32), 255, np.uint8), 8)
    except (ImportError, AttributeError, RuntimeError):
        pass


def adaptive_gpu_batch(free_vram_mb: int | float | None) -> tuple[int, int]:
    """Return a conservative (items, pixels) budget for LaMa/YOLO batches."""
    free = max(0, int(free_vram_mb or 0))
    if free < 1400:
        return 1, 1_800_000
    if free < 3000:
        return 1, 2_800_000
    if free < 6000:
        return 2, 4_500_000
    if free < 10000:
        # Keep the same pixel ceiling while packing more small balloons into
        # one CUDA launch. This improves webtoon workloads without increasing
        # the worst-case activation footprint.
        return 6, 7_000_000
    return 8, 10_000_000


def cuda_memory_mb() -> tuple[int, int] | None:
    """Return CUDA free/total MiB without reserving new GPU memory."""
    # Query the driver first. Importing PyTorch only to ask for two integers
    # costs roughly two seconds on Windows and used to sit directly in the
    # first YOLO request. Production builds do not bundle torch at all, so
    # nvidia-smi is also the one path shared by source and packaged editions.
    try:
        import subprocess
        output = subprocess.check_output(
            [
                "nvidia-smi", "--query-gpu=memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True, timeout=2.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first = output.splitlines()[0]
        free_value, total_value = (int(value.strip()) for value in first.split(",", 1))
        return free_value, total_value
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass

    # Keep PyTorch as a fallback for systems where the driver utility is not
    # on PATH (containers and a few custom NVIDIA installations).
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        mib = 1024 * 1024
        return int(free // mib), int(total // mib)
    except (ImportError, RuntimeError, AttributeError):
        return None


class ByteLRUCache(Generic[T]):
    """Thread-safe LRU bounded by bytes instead of an arbitrary item count."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(1, int(max_bytes))
        self._items: OrderedDict[object, tuple[T, int]] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: object) -> T | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return entry[0]

    def put(self, key: object, value: T, byte_size: int) -> None:
        size = max(0, int(byte_size))
        with self._lock:
            previous = self._items.pop(key, None)
            if previous:
                self._bytes -= previous[1]
            self._items[key] = (value, size)
            self._bytes += size
            while self._bytes > self.max_bytes and len(self._items) > 1:
                _key, (_value, removed_size) = self._items.popitem(last=False)
                self._bytes -= removed_size

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def remove_if(self, predicate) -> int:
        """Discard matching keys and return the number of removed entries."""
        removed = 0
        with self._lock:
            for key in list(self._items):
                if not predicate(key):
                    continue
                _value, byte_size = self._items.pop(key)
                self._bytes -= byte_size
                removed += 1
        return removed

    def resize(self, max_bytes: int) -> None:
        with self._lock:
            self.max_bytes = max(1, int(max_bytes))
            while self._bytes > self.max_bytes and self._items:
                _key, (_value, removed_size) = self._items.popitem(last=False)
                self._bytes -= removed_size

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "items": len(self._items), "bytes": self._bytes,
                "hits": self.hits, "misses": self.misses,
            }


@dataclass(frozen=True)
class OperationMetric:
    name: str
    seconds: float
    samples: int = 1


class PerformanceTracker:
    """Thread-safe timings for UI, I/O and model operations.

    The tracker intentionally stores a short rolling history.  It is useful
    enough for the live monitor and regression tests without becoming another
    unbounded source of memory use during day-long editing sessions.
    """

    def __init__(self, history_limit: int = 120) -> None:
        self._started: dict[str, float] = {}
        self._last: OperationMetric | None = None
        self._history: deque[OperationMetric] = deque(maxlen=max(12, int(history_limit)))
        self._lock = RLock()

    def start(self, key: str) -> None:
        with self._lock:
            self._started[key] = perf_counter()

    def finish(self, key: str, display_name: str | None = None) -> OperationMetric | None:
        with self._lock:
            started = self._started.pop(key, None)
            if started is None:
                return None
            self._last = OperationMetric(display_name or key, max(0.0, perf_counter() - started))
            self._history.append(self._last)
            return self._last

    def record(self, name: str, seconds: float) -> OperationMetric:
        """Store a duration measured by a component that owns its own clock."""
        with self._lock:
            self._last = OperationMetric(str(name), max(0.0, float(seconds)))
            self._history.append(self._last)
            return self._last

    def cancel(self, key: str) -> None:
        with self._lock:
            self._started.pop(key, None)

    def summary(self) -> dict[str, OperationMetric]:
        """Return rolling averages by operation, ordered by most recent use."""
        with self._lock:
            totals: OrderedDict[str, tuple[float, int]] = OrderedDict()
            for metric in self._history:
                total, count = totals.pop(metric.name, (0.0, 0))
                totals[metric.name] = (total + metric.seconds, count + 1)
            return {
                name: OperationMetric(name, total / count, count)
                for name, (total, count) in totals.items()
            }

    def history(self) -> tuple[OperationMetric, ...]:
        with self._lock:
            return tuple(self._history)

    @property
    def last(self) -> OperationMetric | None:
        with self._lock:
            return self._last
