"""Non-destructive cleanup using the bundled OCR-mask and LaMa ONNX models."""
from __future__ import annotations

import os
import threading
import ctypes
import gc
from time import monotonic
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

from core.model_paths import ModelPaths
from core.performance_manager import (
    adaptive_gpu_batch, cuda_memory_mb, normalize_device_mode,
    preload_onnx_cuda, register_cuda_dll_directories,
)
from core.psd_manager import load_source_image, state_signature


class CleaningManager:
    """Detect text with ``ocr.onnx`` and reconstruct it with ``lama.onnx``.

    A region is only a search boundary. It is never used as the inpainting
    mask. Flat white balloons use the same colour-range + contract/grow
    workflow as the supplied Photoshop scripts; transparent, gradient and
    illustrated regions continue through the OCR-mask model and LaMa.
    """

    # The mask network only needs enough neighbourhood to avoid convolution
    # edge effects. 150 px almost doubled many crops and made pages with lots
    # of boxes scale poorly on CPU.
    DETECTION_CONTEXT = 80
    INPAINT_CONTEXT = 64
    MASK_PIPELINE_VERSION = 35

    def __init__(self, models_root: Path) -> None:
        self.paths = ModelPaths(models_root)
        self._ocr_session: ort.InferenceSession | None = None
        self._lama_session: ort.InferenceSession | None = None
        self._session_lock = threading.RLock()
        self.provider_name = "CPUExecutionProvider"
        self.runtime_label = "CPU · ONNX Runtime"
        self.aggressiveness = 90
        self._mask_cache: OrderedDict[tuple, dict] = OrderedDict()
        self._mask_cache_lock = threading.Lock()
        self._mask_cache_limit = 8
        self._mask_cache_max_bytes = 128 * 1024 * 1024
        self._mask_cache_bytes = 0
        self._mask_cache_hits = 0
        self._mask_cache_misses = 0
        self._mask_entry_cache: OrderedDict[tuple, dict] = OrderedDict()
        self._mask_entry_cache_limit = 192
        self._mask_entry_cache_max_bytes = 64 * 1024 * 1024
        self._mask_entry_cache_bytes = 0
        self._mask_entry_cache_hits = 0
        self._mask_entry_cache_misses = 0
        self._source_cache_key: tuple | None = None
        self._source_cache: np.ndarray | None = None
        self._source_cache_max_bytes = 192 * 1024 * 1024
        self._warmed = False
        self.device_mode = "auto"
        self.last_used = monotonic()
        self.resource_profile = "balanced"
        self._onnx_threads = max(1, min(3, (os.cpu_count() or 2) // 2))

    def set_resource_profile(self, profile: str) -> None:
        name = str(profile or "balanced").lower()
        self.resource_profile = name if name in {"low", "balanced", "high"} else "balanced"
        if self.resource_profile == "low":
            limit, max_bytes, entry_limit, entry_bytes, threads, source_bytes = (
                2, 32 * 1024 * 1024, 48, 16 * 1024 * 1024, 1, 0,
            )
        elif self.resource_profile == "high":
            limit, max_bytes, entry_limit, entry_bytes, threads, source_bytes = (
                8, 128 * 1024 * 1024, 384, 128 * 1024 * 1024, 4, 384 * 1024 * 1024,
            )
        else:
            limit, max_bytes, entry_limit, entry_bytes, threads, source_bytes = (
                4, 64 * 1024 * 1024, 192, 64 * 1024 * 1024, 2, 192 * 1024 * 1024,
            )
        self._onnx_threads = max(1, min(threads, max(1, (os.cpu_count() or 2) - 1)))
        self._source_cache_max_bytes = source_bytes
        if self._source_cache is not None and self._source_cache.nbytes > source_bytes:
            self._source_cache = None
            self._source_cache_key = None
        with self._mask_cache_lock:
            self._mask_cache_limit = limit
            self._mask_cache_max_bytes = max_bytes
            self._mask_entry_cache_limit = entry_limit
            self._mask_entry_cache_max_bytes = entry_bytes
            while self._mask_cache and (
                len(self._mask_cache) > self._mask_cache_limit
                or self._mask_cache_bytes > self._mask_cache_max_bytes
            ):
                _, removed = self._mask_cache.popitem(last=False)
                self._mask_cache_bytes -= self._mask_plan_bytes(removed)
            while self._mask_entry_cache and (
                len(self._mask_entry_cache) > self._mask_entry_cache_limit
                or self._mask_entry_cache_bytes > self._mask_entry_cache_max_bytes
            ):
                _, removed = self._mask_entry_cache.popitem(last=False)
                self._mask_entry_cache_bytes -= self._mask_entry_bytes(removed.get("entry"))

    def set_aggressiveness(self, value: int) -> None:
        self.aggressiveness = max(1, min(100, int(value)))

    def set_device_mode(self, mode: str) -> None:
        normalized = normalize_device_mode(mode)
        if normalized != self.device_mode:
            self.device_mode = normalized
            self.unload_models()

    def unload_models(self) -> bool:
        """Release ONNX sessions without discarding reusable mask plans."""
        with self._session_lock:
            had_sessions = self._ocr_session is not None or self._lama_session is not None
            self._ocr_session = None
            self._lama_session = None
            self._warmed = False
            # Memory-pressure and idle-release requests should also release a
            # decoded 60k-pixel page. Mask plans remain compact and reusable.
            self._source_cache = None
            self._source_cache_key = None
        if had_sessions:
            gc.collect()
            try:
                import torch
                if hasattr(torch, "cuda") and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except (ImportError, RuntimeError, AttributeError):
                pass
        self.provider_name = "Modelo no cargado"
        self.runtime_label = "Modelos descargados · caché conservada"
        return had_sessions

    def _load_source_page(
        self, image_path: Path, source_states: dict[str, dict] | None = None,
    ) -> np.ndarray:
        """Decode one page once across mask preparation and LaMa execution."""
        stat = image_path.stat()
        key = (
            str(image_path.resolve()).casefold(), int(stat.st_mtime_ns), int(stat.st_size),
            state_signature(source_states),
        )
        if self._source_cache_key == key and self._source_cache is not None:
            return self._source_cache
        page = np.ascontiguousarray(
            np.asarray(load_source_image(image_path, source_states)), dtype=np.uint8,
        )
        if self._source_cache_max_bytes > 0 and page.nbytes <= self._source_cache_max_bytes:
            self._source_cache_key = key
            self._source_cache = page
        else:
            self._source_cache_key = None
            self._source_cache = None
        return page

    @staticmethod
    def _cuda_runtime_ready() -> bool:
        """Verify the provider DLL itself, not just ORT's advertised name.

        ``get_available_providers`` reports CUDA when the provider plugin is
        installed even if its CUDA/cuDNN DLLs are missing. Trying such a
        provider during session creation adds a long failed initialization
        before CPU fallback.
        """
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            return False
        if os.name != "nt":
            return True
        try:
            capi = Path(ort.__file__).resolve().parent / "capi"
            provider = capi / "onnxruntime_providers_cuda.dll"
            if not provider.is_file():
                return False
            register_cuda_dll_directories(capi)
            preload_onnx_cuda(ort, capi)
            ctypes.WinDLL(str(provider))
            return True
        except (OSError, AttributeError):
            return False

    def _create_session(self, model_path: Path) -> ort.InferenceSession:
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        # ONNX Runtime's automatic CPU pool uses physical cores and applies
        # affinity. Explicitly using every logical thread was ~75% slower on a
        # Ryzen 7 7435HS in the production LaMa crop benchmark.
        # Leave CPU capacity for Qt painting, scrolling and input. ONNX's
        # automatic pool can otherwise saturate every core and make the app
        # appear frozen even though inference already runs in a worker.
        logical_cores = max(1, os.cpu_count() or 1)
        # Two independent crops may run concurrently. Four threads per crop
        # was faster in the production CPU benchmark while still leaving Qt
        # enough CPU time to paint and scroll smoothly.
        options.intra_op_num_threads = max(1, min(self._onnx_threads, max(1, logical_cores - 1)))
        options.inter_op_num_threads = 1
        options.add_session_config_entry(
            "session.intra_op.allow_spinning", "0" if self.resource_profile == "low" else "1"
        )
        available = ort.get_available_providers()
        providers: list = ["CPUExecutionProvider"]
        cuda_ready = "CUDAExecutionProvider" in available and self._cuda_runtime_ready()
        dml_ready = "DmlExecutionProvider" in available
        if self.device_mode == "gpu" and not (cuda_ready or dml_ready):
            raise RuntimeError("El modo GPU está activo, pero ni CUDA ni DirectML están disponibles para LaMa.")
        if self.device_mode != "cpu":
            if cuda_ready:
                options.enable_mem_pattern = False
                providers.insert(0, (
                    "CUDAExecutionProvider",
                    {
                        "device_id": 0,
                        "arena_extend_strategy": "kSameAsRequested",
                        "cudnn_conv_algo_search": "HEURISTIC",
                        "do_copy_in_default_stream": True,
                    },
                ))
            elif dml_ready:
                options.enable_mem_pattern = False
                providers.insert(0, ("DmlExecutionProvider", {"device_id": 0, "enable_dynamic_graph_fusion": "1"}))
        if not providers or providers[0] == "CPUExecutionProvider":
            options.enable_mem_pattern = True
        session = ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
        self.provider_name = session.get_providers()[0]
        active = session.get_providers()
        self.runtime_label = (
            "GPU CUDA + respaldo CPU"
            if active and active[0] == "CUDAExecutionProvider"
            else "GPU DirectML (AMD/Intel) + respaldo CPU"
            if active and active[0] == "DmlExecutionProvider"
            else "CPU optimizada · núcleos físicos"
        )
        self.last_used = monotonic()
        return session

    def _get_ocr_session(self) -> ort.InferenceSession:
        if self._ocr_session is None:
            with self._session_lock:
                if self._ocr_session is None:
                    self._ocr_session = self._create_session(self.paths.validate("ocr_mask"))
        return self._ocr_session

    def _get_lama_session(self) -> ort.InferenceSession:
        if self._lama_session is None:
            with self._session_lock:
                if self._lama_session is None:
                    self._lama_session = self._create_session(self.paths.validate("lama"))
        return self._lama_session

    def warm_up(
        self,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        """Load sessions and pay the first-inference cost before user action."""
        progress = progress or (lambda _value: None)
        cancelled = cancelled or (lambda: False)
        if self._warmed:
            progress(100)
            return self.runtime_label
        progress(5)
        self._get_ocr_session()
        progress(32)
        if cancelled():
            return self.runtime_label
        self._get_lama_session()
        progress(68)
        if cancelled():
            return self.runtime_label
        sample = np.full((256, 256, 3), 255, dtype=np.uint8)
        sample_mask = np.zeros((256, 256), dtype=np.uint8)
        sample_mask[96:160, 80:176] = 255
        self._text_mask(sample)
        progress(84)
        if cancelled():
            return self.runtime_label
        self._lama(sample, sample_mask)
        self._warmed = True
        self.last_used = monotonic()
        progress(100)
        return self.runtime_label

    def clean_mask(self, rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Run LaMa with an exact user-painted mask over an already composed crop."""
        source = np.ascontiguousarray(rgb, dtype=np.uint8)
        exact_mask = np.where(mask > 8, 255, 0).astype(np.uint8)
        if source.ndim != 3 or source.shape[2] != 3 or source.shape[:2] != exact_mask.shape:
            raise ValueError("El trazo del pincel no coincide con la imagen.")
        if not np.any(exact_mask):
            return source.copy()
        x, y, width, height = cv2.boundingRect(exact_mask)
        if max(width, height) > 4096 or width * height > 4_000_000:
            raise ValueError(
                "La máscara fue descartada porque contiene un salto demasiado grande. "
                "Vuelve a pintar sin desplazar el lienzo durante el trazo."
            )
        restored = self._lama(source, exact_mask)
        cleaned = self._composite_lama(source, restored, exact_mask)
        # A manually painted mask is already the user's final selection. One
        # LaMa pass is enough; OCR residue retries belong to automatic masks
        # and only multiply latency for the manual brush.
        return cleaned

    def _text_mask(self, rgb: np.ndarray) -> np.ndarray:
        """Return the neural text mask without expanding it into the balloon."""
        height, width = rgb.shape[:2]
        # A canonical 64 px canvas makes the single and batched paths
        # numerically equivalent. It also keeps the network boundary farther
        # from glyphs near the search crop edge.
        padded_height = (height + 63) // 64 * 64
        padded_width = (width + 63) // 64 * 64
        padded = cv2.copyMakeBorder(
            rgb,
            0,
            padded_height - height,
            0,
            padded_width - width,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        tensor = np.transpose(padded.astype(np.float32) / 255.0, (2, 0, 1))[None]
        session = self._get_ocr_session()
        heatmap = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0, :height, :width]
        threshold = max(0.05, 0.5 - (self.aggressiveness / 100.0) * 0.45)
        mask = np.where(heatmap > threshold, 255, 0).astype(np.uint8)

        return self._expand_text_mask(mask, self.aggressiveness)

    def _text_mask_many(self, crops: list[np.ndarray]) -> list[np.ndarray]:
        """Run the text locator in shape-compatible dynamic batches.

        Long webtoon pages contain many distant balloons that cannot share one
        spatial crop. They can still share one ONNX launch: every crop is
        padded independently, batched with similarly sized crops and sliced
        back to its exact geometry. The black padding is identical to
        :meth:`_text_mask`, so batching does not change the detector input.
        """
        if not crops:
            return []
        # Tests, plugins and diagnostic tools may replace _text_mask on an
        # instance. Preserve that extension point instead of bypassing it.
        if len(crops) == 1 or "_text_mask" in self.__dict__:
            return [self._text_mask(crop) for crop in crops]

        session = self._get_ocr_session()
        providers = session.get_providers() or []
        gpu = any(
            provider in ("CUDAExecutionProvider", "TensorrtExecutionProvider", "DmlExecutionProvider")
            for provider in providers
        )
        if gpu:
            memory = cuda_memory_mb()
            lama_limit, lama_pixels = adaptive_gpu_batch(memory[0] if memory else None)
            batch_limit = max(4, min(16, lama_limit * 2))
            pixel_budget = max(3_600_000, lama_pixels * 2)
        else:
            batch_limit = {"low": 2, "balanced": 4, "high": 8}.get(self.resource_profile, 4)
            pixel_budget = {"low": 2_000_000, "balanced": 4_000_000, "high": 6_000_000}.get(
                self.resource_profile, 4_000_000,
            )

        results: list[np.ndarray | None] = [None] * len(crops)
        buckets: dict[tuple[int, int], list[int]] = {}
        padded_shapes: list[tuple[int, int]] = []
        for index, crop in enumerate(crops):
            height, width = crop.shape[:2]
            padded_height = (height + 63) // 64 * 64
            padded_width = (width + 63) // 64 * 64
            padded_shapes.append((padded_height, padded_width))
            # A 64 px bucket keeps padding modest while allowing balloons on
            # distant parts of the strip to share the same inference.
            buckets.setdefault((padded_height, padded_width), []).append(index)

        input_name = session.get_inputs()[0].name
        threshold = max(0.05, 0.5 - (self.aggressiveness / 100.0) * 0.45)
        for indices in buckets.values():
            cursor = 0
            while cursor < len(indices):
                chosen: list[int] = []
                max_height = max_width = 0
                while cursor < len(indices) and len(chosen) < batch_limit:
                    candidate = indices[cursor]
                    height, width = padded_shapes[candidate]
                    next_height, next_width = max(max_height, height), max(max_width, width)
                    if chosen and next_height * next_width * (len(chosen) + 1) > pixel_budget:
                        break
                    chosen.append(candidate)
                    max_height, max_width = next_height, next_width
                    cursor += 1

                tensors: list[np.ndarray] = []
                for index in chosen:
                    crop = crops[index]
                    height, width = crop.shape[:2]
                    padded = cv2.copyMakeBorder(
                        crop, 0, max_height - height, 0, max_width - width,
                        cv2.BORDER_CONSTANT, value=(0, 0, 0),
                    )
                    tensors.append(np.transpose(padded.astype(np.float32) / 255.0, (2, 0, 1)))
                try:
                    output = session.run(None, {input_name: np.stack(tensors)})[0]
                    if output.shape[0] != len(chosen):
                        raise ValueError("El detector devolvió un lote con tamaño inesperado.")
                    for batch_index, index in enumerate(chosen):
                        height, width = crops[index].shape[:2]
                        heatmap = output[batch_index, 0, :height, :width]
                        raw = np.where(heatmap > threshold, 255, 0).astype(np.uint8)
                        results[index] = self._expand_text_mask(raw, self.aggressiveness)
                except Exception:
                    # Some custom exports advertise a dynamic batch axis but
                    # only execute N=1. Fall back without failing the cleanup.
                    for index in chosen:
                        results[index] = self._text_mask(crops[index])

        self.last_used = monotonic()
        return [
            result if result is not None else self._text_mask(crops[index])
            for index, result in enumerate(results)
        ]

    @staticmethod
    def _expand_text_mask(mask: np.ndarray, aggressiveness: int) -> np.ndarray:
        """Cover glyph antialiasing without growing into balloon contours."""
        value = max(1, min(100, int(aggressiveness)))
        closed = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        kernel_size = 3 + 2 * round((value / 100.0) * 2)  # 3..7, always odd
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        return cv2.dilate(closed, kernel, iterations=1)

    @staticmethod
    def _protect_selection_boundary(
        mask: np.ndarray, selection: np.ndarray, preserve_thick_bands: bool = False,
    ) -> np.ndarray:
        """Discard long mask components attached to the user-box boundary.

        Impact rays and balloon frames commonly enter through one edge of a
        large selection. Text glyphs normally remain internal; small glyphs
        touching a tight selection are retained to avoid losing punctuation.
        """
        sx, sy, sw, sh = cv2.boundingRect(selection)
        if sw <= 0 or sh <= 0 or not np.any(mask):
            return mask
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        protected = mask.copy()
        edge_tolerance = max(2, min(6, round(min(sw, sh) * 0.012)))
        for label in range(1, component_count):
            x, y, width, height, area = (int(value) for value in stats[label])
            touches_edge = (
                x <= sx + edge_tolerance
                or y <= sy + edge_tolerance
                or x + width >= sx + sw - edge_tolerance
                or y + height >= sy + sh - edge_tolerance
            )
            long_component = (
                width >= sw * 0.18
                or height >= sh * 0.18
                or max(width, height) >= max(1, min(width, height)) * 5
            )
            thin_limit = max(7, round(min(sw, sh) * 0.035))
            thin_component = min(width, height) <= thin_limit
            if (
                touches_edge and long_component and area >= 18
                and (not preserve_thick_bands or thin_component)
            ):
                protected[labels == label] = 0
        return protected

    @staticmethod
    def _selection_border_guard(rgb: np.ndarray, selection: np.ndarray) -> np.ndarray:
        """Protect balloon outlines, spikes and impact rays entering through box edges.

        This guard examines original pixels to estimate local background, finds
        ink/colour strokes connected to the selection perimeter, and reserves
        an antialias margin around them so spiky and shout balloon contours are never
        mistaken for text or erased by inpainting.
        """
        sx, sy, sw, sh = cv2.boundingRect(selection)
        guard = np.zeros(selection.shape, dtype=np.uint8)
        if sw <= 0 or sh <= 0 or rgb.shape[:2] != selection.shape:
            return guard
        selected = selection > 0
        samples = rgb[selected]
        if samples.shape[0] < 64:
            return guard

        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        sample_lab = lab[selected]
        # The 80th percentile reliably represents white/light balloon fill;
        # on coloured balloons the median fallback below remains local.
        background = np.percentile(sample_lab, 80, axis=0)
        distances = np.linalg.norm(lab - background, axis=2)
        candidate = np.where((distances > 9.0) & selected, 255, 0).astype(np.uint8)
        candidate = cv2.morphologyEx(
            candidate,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )

        band_width = max(4, min(14, round(min(sw, sh) * 0.045)))
        inner = np.zeros_like(candidate)
        inner[sy:sy + sh, sx:sx + sw] = 255
        inset = np.zeros_like(candidate)
        if sw > band_width * 2 and sh > band_width * 2:
            inset[
                sy + band_width:sy + sh - band_width,
                sx + band_width:sx + sw - band_width,
            ] = 255
        border_band = (inner > 0) & (inset == 0)

        # Inner dialogue core definition (inner 40% of the box)
        core_margin_x = int(sw * 0.25)
        core_margin_y = int(sh * 0.25)
        central_core = np.zeros_like(candidate)
        central_core[
            sy + core_margin_y:sy + sh - core_margin_y,
            sx + core_margin_x:sx + sw - core_margin_x,
        ] = 255
        core_area = max(1, int(np.count_nonzero(central_core)))
        selected_area = max(1, int(np.count_nonzero(selection)))

        count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
        minimum_span = max(6, round(min(sw, sh) * 0.03))
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            component = labels == label
            if area < 6 or not np.any(component & border_band):
                continue
            core_overlap = int(np.count_nonzero(component & (central_core > 0)))
            if core_overlap / core_area > 0.85 and area > selected_area * 0.70:
                continue
            aspect = max(width, height) / max(1, min(width, height))
            line_like = aspect >= 1.5 or max(width, height) >= minimum_span
            if line_like:
                guard[component] = 255

        if not np.any(guard):
            return guard

        # Include the antialiased fringe around preserved spikes and borders
        guard = cv2.dilate(
            guard,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            iterations=1,
        )
        return cv2.bitwise_and(guard, selection)

    def _lama(self, rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
        height, width = rgb.shape[:2]
        padded_height = (height + 7) // 8 * 8
        padded_width = (width + 7) // 8 * 8
        image = cv2.copyMakeBorder(
            rgb, 0, padded_height - height, 0, padded_width - width, cv2.BORDER_REFLECT
        )
        padded_mask = cv2.copyMakeBorder(
            mask, 0, padded_height - height, 0, padded_width - width, cv2.BORDER_REFLECT
        )
        image_tensor = np.transpose(image.astype(np.float32) / 255.0, (2, 0, 1))[None]
        mask_tensor = (padded_mask > 127).astype(np.float32)[None, None]
        output = self._get_lama_session().run(
            None, {"image": image_tensor, "mask": mask_tensor}
        )[0][0]
        self.last_used = monotonic()
        restored = np.clip(np.transpose(output, (1, 2, 0)) * 255, 0, 255).astype(np.uint8)
        return restored[:height, :width]

    def _lama_many(self, jobs: list[tuple[np.ndarray, np.ndarray]]) -> list[np.ndarray]:
        """Schedule independent crops efficiently for the active hardware.

        CPU uses two bounded concurrent runs; GPU uses the model's dynamic
        batch axis. Unrelated balloons are never joined into one image, and a
        pixel budget prevents large webtoon crops from causing memory spikes.
        """
        if not jobs:
            return []
        # Unit tests and optional integrators can replace _lama on an instance;
        # preserve that contract rather than bypassing their implementation.
        if len(jobs) == 1 or "_lama" in self.__dict__:
            return [self._lama(image, mask) for image, mask in jobs]

        session = self._get_lama_session()
        providers = session.get_providers() or []
        gpu = bool(any(p in ("CUDAExecutionProvider", "TensorrtExecutionProvider") for p in providers))
        if not gpu:
            # ORT's CPU batch path did not improve this model. Two concurrent
            # runs reduce multi-box latency.
            workers = 1 if self.resource_profile == "low" else min(4, len(jobs))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                return list(executor.map(lambda job: self._lama(*job), jobs))

        memory = cuda_memory_mb()
        batch_limit, pixel_budget = adaptive_gpu_batch(memory[0] if memory else None)
        results: list[np.ndarray | None] = [None] * len(jobs)
        buckets: dict[tuple[int, int], list[int]] = {}
        for index, (image, _) in enumerate(jobs):
            height, width = image.shape[:2]
            # Similar dimensions share a batch. A 160 px bucket substantially
            # reduces CUDA launches on mixed-size dialogue balloons while the
            # pixel budget below still caps padded activation memory.
            buckets.setdefault(((height + 159) // 160, (width + 159) // 160), []).append(index)

        for indices in buckets.values():
            cursor = 0
            while cursor < len(indices):
                chosen: list[int] = []
                max_height = max_width = 0
                while cursor < len(indices) and len(chosen) < batch_limit:
                    candidate = indices[cursor]
                    height, width = jobs[candidate][0].shape[:2]
                    next_height, next_width = max(max_height, height), max(max_width, width)
                    if chosen and next_height * next_width * (len(chosen) + 1) > pixel_budget:
                        break
                    chosen.append(candidate)
                    max_height, max_width = next_height, next_width
                    cursor += 1
                padded_height = (max_height + 7) // 8 * 8
                padded_width = (max_width + 7) // 8 * 8
                image_batch, mask_batch = [], []
                for index in chosen:
                    image, mask = jobs[index]
                    height, width = image.shape[:2]
                    padded_image = cv2.copyMakeBorder(
                        image, 0, padded_height - height, 0, padded_width - width,
                        cv2.BORDER_REFLECT,
                    )
                    padded_mask = cv2.copyMakeBorder(
                        mask, 0, padded_height - height, 0, padded_width - width,
                        cv2.BORDER_CONSTANT, value=0,
                    )
                    image_batch.append(np.transpose(padded_image.astype(np.float32) / 255.0, (2, 0, 1)))
                    mask_batch.append((padded_mask > 127).astype(np.float32)[None])
                output = session.run(None, {
                    "image": np.stack(image_batch),
                    "mask": np.stack(mask_batch),
                })[0]
                for batch_index, index in enumerate(chosen):
                    height, width = jobs[index][0].shape[:2]
                    restored = np.clip(
                        np.transpose(output[batch_index], (1, 2, 0)) * 255, 0, 255
                    ).astype(np.uint8)
                    results[index] = restored[:height, :width]
        self.last_used = monotonic()
        return [result for result in results if result is not None]

    @staticmethod
    def _gradient_guided_restoration(
        original: np.ndarray, restored: np.ndarray, mask: np.ndarray,
    ) -> tuple[np.ndarray, bool]:
        """Correct LaMa only when the local background is a measurable smooth gradient.

        A robust RGB plane is fitted to a ring around the text mask. It is
        accepted only with a very small residual, so artwork, screentones and
        impact rays continue to use the neural reconstruction unchanged.
        """
        hard = mask > 0
        if not np.any(hard) or min(mask.shape) < 12:
            return restored, False
        inner = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
        outer = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))) > 0
        ring = outer & ~inner
        yy, xx = np.nonzero(ring)
        if len(xx) < 96:
            return restored, False
        if len(xx) > 8000:
            indices = np.linspace(0, len(xx) - 1, 8000, dtype=np.int32)
            xx, yy = xx[indices], yy[indices]
        height, width = mask.shape
        design = np.column_stack((
            np.ones(len(xx), dtype=np.float32),
            xx.astype(np.float32) / max(1, width - 1),
            yy.astype(np.float32) / max(1, height - 1),
        ))
        samples = original[yy, xx].astype(np.float32)
        keep = np.ones(len(xx), dtype=bool)
        coefficients = None
        for _ in range(3):
            if int(np.count_nonzero(keep)) < 64:
                return restored, False
            coefficients, *_ = np.linalg.lstsq(design[keep], samples[keep], rcond=None)
            residual = np.linalg.norm(samples - design @ coefficients, axis=1)
            cutoff = max(2.0, float(np.percentile(residual[keep], 72)) * 1.8)
            keep = residual <= cutoff
        assert coefficients is not None
        accepted_residual = np.linalg.norm(samples[keep] - design[keep] @ coefficients, axis=1)
        if len(accepted_residual) < 64 or float(np.percentile(accepted_residual, 90)) > 5.5:
            return restored, False
        my, mx = np.nonzero(hard)
        masked_design = np.column_stack((
            np.ones(len(mx), dtype=np.float32),
            mx.astype(np.float32) / max(1, width - 1),
            my.astype(np.float32) / max(1, height - 1),
        ))
        predicted = np.clip(masked_design @ coefficients, 0, 255).astype(np.uint8)
        guided = restored.copy()
        guided[my, mx] = predicted
        return guided, True

    @staticmethod
    def _composite_lama(original: np.ndarray, restored: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Use the LaMa result exactly inside its mask and nowhere else.

        LaMa already receives the original image together with the binary
        inpainting mask. Colour replacement, background flattening and
        post-model feathering can deform a balloon and are intentionally not
        part of this compositor.
        """
        hard_mask = mask > 0
        if not np.any(hard_mask):
            return original.copy()
        restored, _gradient_used = CleaningManager._gradient_guided_restoration(
            original, restored, mask,
        )
        result = original.copy()
        result[hard_mask] = restored[hard_mask]
        return result

    @staticmethod
    def _residual_mask(
        cleaned: np.ndarray,
        text_mask: np.ndarray,
        selection: np.ndarray,
    ) -> np.ndarray:
        """Find faint ink left by LaMa without expanding the original mask.

        A retry is allowed only for components surrounded by a demonstrably
        flat background. Candidate pixels must remain inside ``text_mask``;
        therefore balloon rays, outlines and artwork outside the validated
        text mask can never enter the second inference.
        """
        hard = np.where(text_mask > 0, 255, 0).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(hard, 8)
        if count <= 1:
            return np.zeros_like(hard)

        lab = cv2.cvtColor(cleaned, cv2.COLOR_RGB2LAB).astype(np.float32)
        residual = np.zeros_like(hard)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < 8:
                continue
            component = np.where(labels == label, 255, 0).astype(np.uint8)
            ring = (
                (cv2.dilate(component, kernel, iterations=1) > 0)
                & (hard == 0)
                & (selection > 0)
            )
            samples = lab[ring]
            if samples.shape[0] < 32:
                continue

            centre = np.median(samples, axis=0)
            distances = np.linalg.norm(samples - centre, axis=1)
            limit = float(np.percentile(distances, 60)) + 0.5
            flat_samples = samples[distances <= limit]
            if flat_samples.shape[0] < 24:
                continue
            background = np.median(flat_samples, axis=0)
            spread = np.linalg.norm(flat_samples - background, axis=1)
            if float(np.percentile(spread, 90)) > 4.0:
                continue

            difference = np.linalg.norm(lab - background, axis=2)
            residual[(difference > 3.0) & (labels == label)] = 255

        if not np.any(residual):
            return residual
        residual = cv2.dilate(
            residual,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        # This intersection is the safety invariant for the retry pass.
        return cv2.bitwise_and(residual, hard)

    def _refine_lama_residuals(
        self,
        cleaned: np.ndarray,
        text_mask: np.ndarray,
        selection: np.ndarray,
        max_passes: int = 1,
    ) -> tuple[np.ndarray, int]:
        """Retry LaMa on a contextual crop while measurable residue remains.

        Impact balloons make the first reconstruction harder, but rerunning
        LaMa over the whole selected balloon is both slow and risky. Each pass
        therefore uses only the residual bounding box plus model context. The
        retry mask remains a subset of the validated text mask, and processing
        stops as soon as residue no longer decreases.
        """
        current = np.ascontiguousarray(cleaned.copy())
        allowed = np.where(text_mask > 0, 255, 0).astype(np.uint8)
        previous_count: int | None = None
        completed_passes = 0
        image_height, image_width = allowed.shape

        for _ in range(max(0, int(max_passes))):
            residual = self._residual_mask(current, allowed, selection)
            residual_count = int(np.count_nonzero(residual))
            if residual_count < 4:
                break
            if previous_count is not None and residual_count >= previous_count:
                break

            x, y, width, height = cv2.boundingRect(residual)
            context = max(self.INPAINT_CONTEXT, 32)
            x1, y1 = max(0, x - context), max(0, y - context)
            x2 = min(image_width, x + width + context)
            y2 = min(image_height, y + height + context)
            local_image = np.ascontiguousarray(current[y1:y2, x1:x2])
            local_mask = np.ascontiguousarray(residual[y1:y2, x1:x2])
            restored = self._lama(local_image, local_mask)
            refined = self._composite_lama(local_image, restored, local_mask)
            if np.array_equal(refined[local_mask > 0], local_image[local_mask > 0]):
                break
            current[y1:y2, x1:x2] = refined
            previous_count = residual_count
            completed_passes += 1

        return current, completed_passes

    def _refine_lama_residuals_many(
        self,
        jobs: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
        max_passes: int = 1,
    ) -> list[tuple[np.ndarray, int]]:
        """Retry residual text for many independent crops in shared batches."""
        if not jobs:
            return []
        current = [np.ascontiguousarray(cleaned.copy()) for cleaned, _, _ in jobs]
        allowed = [np.where(mask > 0, 255, 0).astype(np.uint8) for _, mask, _ in jobs]
        selections = [selection for _, _, selection in jobs]
        previous_counts: list[int | None] = [None] * len(jobs)
        completed = [0] * len(jobs)

        for _ in range(max(0, int(max_passes))):
            retry_jobs: list[tuple[np.ndarray, np.ndarray]] = []
            retry_metadata: list[tuple[int, int, int, int, int, int]] = []
            for index, image in enumerate(current):
                residual = self._residual_mask(image, allowed[index], selections[index])
                residual_count = int(np.count_nonzero(residual))
                if residual_count < 4:
                    continue
                previous = previous_counts[index]
                if previous is not None and residual_count >= previous:
                    continue
                x, y, width, height = cv2.boundingRect(residual)
                image_height, image_width = residual.shape
                context = max(self.INPAINT_CONTEXT, 32)
                x1, y1 = max(0, x - context), max(0, y - context)
                x2 = min(image_width, x + width + context)
                y2 = min(image_height, y + height + context)
                retry_jobs.append((
                    np.ascontiguousarray(image[y1:y2, x1:x2]),
                    np.ascontiguousarray(residual[y1:y2, x1:x2]),
                ))
                retry_metadata.append((index, x1, y1, x2, y2, residual_count))

            if not retry_jobs:
                break
            restored_jobs = self._lama_many(retry_jobs)
            for metadata, (local_image, local_mask), restored in zip(
                retry_metadata, retry_jobs, restored_jobs,
            ):
                index, x1, y1, x2, y2, residual_count = metadata
                refined = self._composite_lama(local_image, restored, local_mask)
                if np.array_equal(refined[local_mask > 0], local_image[local_mask > 0]):
                    continue
                current[index][y1:y2, x1:x2] = refined
                previous_counts[index] = residual_count
                completed[index] += 1

        return list(zip(current, completed))

    def _inpaint_validated_crop(
        self,
        rgb: np.ndarray,
        mask: np.ndarray,
    ) -> tuple[np.ndarray, tuple[int, int, int, int], int]:
        """Run LaMa only around validated text, not over its whole search box."""
        x, y, width, height = cv2.boundingRect(mask)
        if width <= 0 or height <= 0:
            return rgb.copy(), (0, 0, 0, 0), 0
        image_height, image_width = mask.shape
        context = max(32, int(self.INPAINT_CONTEXT))
        x1, y1 = max(0, x - context), max(0, y - context)
        x2 = min(image_width, x + width + context)
        y2 = min(image_height, y + height + context)
        local_image = np.ascontiguousarray(rgb[y1:y2, x1:x2])
        local_mask = np.ascontiguousarray(mask[y1:y2, x1:x2])
        restored = self._lama(local_image, local_mask)
        cleaned = self._composite_lama(local_image, restored, local_mask)
        local_selection = np.full(local_mask.shape, 255, dtype=np.uint8)
        cleaned, passes = self._refine_lama_residuals(
            cleaned, local_mask, local_selection, max_passes=1
        )
        return cleaned, (x1, y1, x2, y2), passes

    @staticmethod
    def _complete_glyph_mask(rgb: np.ndarray, neural_mask: np.ndarray, selection: np.ndarray) -> np.ndarray:
        """Build a mask that fully covers each text run, per-run, not per-balloon.

        Classifies flat-vs-textured background per text run against a local
        ring of background around that run (with a whole-selection fallback),
        so one shadow, gradient, or sliver of neighbouring artwork can't
        starve the rest of the balloon of colour-based completion.
        """
        selected_rgb = rgb[selection > 0]
        if selected_rgb.size < 60:
            return neural_mask
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)

        def estimate_background(pixels: np.ndarray) -> np.ndarray:
            return np.percentile(pixels, 80, axis=0)

        selection_bg = estimate_background(lab[selection > 0])
        sx, sy, sw, sh = cv2.boundingRect(selection)

        # Recover disconnected strokes around every neural seed. East Asian
        # glyphs often contain several islands and the detector may activate
        # on only one of them. Work from ink measured against the local
        # background, remove components entering from the selection boundary
        # first, and then retain only ink spatially supported by a seed. This
        # is intentionally not a fill of ``selection``.
        distance_from_background = np.linalg.norm(lab - selection_bg, axis=2)
        candidate_ink = np.where(distance_from_background > 9.0, 255, 0).astype(np.uint8)
        candidate_ink = cv2.bitwise_and(candidate_ink, selection)
        candidate_ink = cv2.morphologyEx(
            candidate_ink,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2)),
        )
        candidate_ink = CleaningManager._protect_selection_boundary(candidate_ink, selection)
        support_x = max(12, min(48, int(round(sw * 0.07))))
        support_y = max(14, min(52, int(round(sh * 0.22))))
        seed_support = cv2.dilate(
            neural_mask,
            cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (support_x * 2 + 1, support_y * 2 + 1)
            ),
            iterations=1,
        )
        anchored_ink = cv2.bitwise_and(candidate_ink, seed_support)
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(25, int(sw * 0.07)), 3)
        )
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (3, max(25, int(sh * 0.07)))
        )
        joined = cv2.morphologyEx(neural_mask, cv2.MORPH_CLOSE, horizontal_kernel)
        joined = cv2.bitwise_or(
            joined, cv2.morphologyEx(neural_mask, cv2.MORPH_CLOSE, vertical_kernel)
        )
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
        # Completion may add the portions of a character that the neural
        # heatmap missed, but must never discard pixels it did detect.
        completed = cv2.bitwise_or(neural_mask, anchored_ink)
        for label in range(1, component_count):
            x, y, width, height, area = (int(value) for value in stats[label])
            if width < max(12, int(sw * 0.025)) or height < 6 or area < 12:
                continue

            is_vertical_run = height > width * 1.3
            run_size = width if is_vertical_run else height
            ring_pad = max(10, int(run_size * 1.2))
            rx1, ry1 = max(sx, x - ring_pad), max(sy, y - ring_pad)
            rx2, ry2 = min(sx + sw, x + width + ring_pad), min(sy + sh, y + height + ring_pad)

            window_selection = np.zeros_like(neural_mask, dtype=bool)
            window_selection[ry1:ry2, rx1:rx2] = True
            ring_pixels = window_selection & (joined == 0)
            if np.count_nonzero(ring_pixels) < 20:
                ring_pixels = window_selection
            local_bg = estimate_background(lab[ring_pixels])
            local_variance = float(np.percentile(
                np.linalg.norm(lab[ring_pixels] - local_bg, axis=1), 70
            ))
            flat_local = local_variance < 22.0
            if not flat_local:
                global_variance = float(np.percentile(
                    np.linalg.norm(lab[ring_pixels] - selection_bg, axis=1), 70
                ))
                if global_variance < 26.0:
                    local_bg = selection_bg
                    flat_local = True

            if flat_local:
                if is_vertical_run:
                    pad_x, pad_y = max(6, int(width * 0.35)), max(16, int(width * 1.00))
                else:
                    pad_x, pad_y = max(16, int(height * 1.00)), max(6, int(height * 0.25))
                x1, y1 = max(sx, x - pad_x), max(sy, y - pad_y)
                x2, y2 = min(sx + sw, x + width + pad_x), min(sy + sh, y + height + pad_y)
                # Reuse the already boundary-protected ink map. Recomputing
                # dark pixels here could reintroduce a balloon outline or an
                # impact ray and connect it to an otherwise valid text line.
                local_ink = candidate_ink[y1:y2, x1:x2]
                completed[y1:y2, x1:x2] = cv2.bitwise_or(completed[y1:y2, x1:x2], local_ink)
            else:
                component = np.zeros_like(neural_mask)
                component[y:y + height, x:x + width] = joined[y:y + height, x:x + width]
                component = cv2.dilate(
                    component,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                    iterations=2,
                )
                completed = cv2.bitwise_or(completed, component)
        if not np.any(completed):
            return neural_mask
        completed = cv2.dilate(
            completed, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=2
        )
        return cv2.bitwise_and(completed, selection)

    @classmethod
    def _build_text_mask(
        cls,
        rgb: np.ndarray,
        detected: np.ndarray,
        selection: np.ndarray,
    ) -> np.ndarray:
        """Turn a detection heatmap into complete glyphs inside a search box.

        ``selection`` is only a search boundary; it is deliberately never
        used as an inpainting mask. The neural seed locates text runs, local
        colour completion recovers missing strokes, and boundary protection
        removes balloon outlines or impact rays that enter through the box.
        """
        seed = cv2.bitwise_and(detected, selection)
        seed = cls._protect_selection_boundary(seed, selection, preserve_thick_bands=True)
        seed = cls._filter_text_seed_components(seed, selection)
        seed = cls._suppress_peripheral_strokes(seed, selection)
        # The neural model is a strong locator, but older OCR masks can miss a
        # complete line or coloured lettering. On a demonstrably flat balloon
        # background, recover text-shaped colour/contrast runs and merge them
        # with the model seed. Boundary-connected rays are removed first, so
        # pointed/shout balloons remain protected.
        border_guard = cls._selection_border_guard(rgb, selection)
        protected_border = cv2.dilate(
            border_guard, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
            iterations=1,
        )
        if np.any(protected_border):
            seed = cv2.bitwise_and(seed, cv2.bitwise_not(protected_border))
        # The neural output is a locator, not the final paint mask. Rebuild
        # its glyphs from the real image colours, following the same principle
        # used by mature manga pipelines: Otsu candidates per channel are
        # accepted component by component only when they agree with the
        # detector. This removes rectangular/blurred neural halos and retains
        # antialiased black or coloured strokes.
        refined = cls._refine_neural_mask_by_colour(rgb, seed, selection)
        locator = refined if np.any(refined) else seed
        recovered = cls._recover_flat_background_text(
            rgb, locator, selection, border_guard
        )
        seed = cv2.bitwise_or(locator, recovered)
        if not np.any(seed):
            return seed
        completed = cls._complete_glyph_mask(rgb, seed, selection)
        completed = cls._protect_selection_boundary(
            completed, selection, preserve_thick_bands=True
        )
        completed = cls._suppress_peripheral_strokes(completed, selection)
        return cv2.bitwise_and(completed, cv2.bitwise_not(protected_border))

    @staticmethod
    def _refine_neural_mask_by_colour(
        rgb: np.ndarray,
        neural_mask: np.ndarray,
        selection: np.ndarray,
    ) -> np.ndarray:
        """Convert a soft/expanded detector mask into real glyph components.

        The detector establishes *where* text is expected. Thresholds from
        RGB and luminance establish *which pixels* belong to the lettering.
        A candidate component is kept only when adding it reduces the XOR
        error against the detector mask, so a white balloon, its outline, and
        distant impact rays cannot become the inpainting mask.
        """
        empty = np.zeros(selection.shape, dtype=np.uint8)
        if rgb.shape[:2] != selection.shape:
            return empty
        reference = cv2.bitwise_and(
            np.where(neural_mask > 0, 255, 0).astype(np.uint8), selection
        )
        if not np.any(reference):
            return empty

        selected = selection > 0
        selected_count = int(np.count_nonzero(selected))
        if selected_count < 16:
            return reference

        blurred = cv2.GaussianBlur(rgb, (3, 3), 0)
        gray = cv2.cvtColor(blurred, cv2.COLOR_RGB2GRAY)
        channels = [blurred[:, :, index] for index in range(3)] + [gray]
        candidates: list[np.ndarray] = []

        # For every channel choose the threshold polarity that resembles the
        # detector most closely. This handles dark text, light text, and
        # saturated red/pink lettering without assuming a fixed foreground.
        reference_bool = reference > 0
        for channel in channels:
            _, binary = cv2.threshold(
                channel, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
            )
            binary = cv2.bitwise_and(binary, selection)
            inverse = cv2.bitwise_and(cv2.bitwise_not(binary), selection)
            binary_error = int(np.count_nonzero((binary > 0) ^ reference_bool))
            inverse_error = int(np.count_nonzero((inverse > 0) ^ reference_bool))
            candidates.append(binary if binary_error <= inverse_error else inverse)

        # Add a few narrow colour bands sampled under the neural locator.
        # They recover antialiasing and coloured glyph interiors which a
        # global Otsu split can merge into the balloon background.
        sample_mask = cv2.erode(
            reference, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
            iterations=1,
        )
        if not np.any(sample_mask):
            sample_mask = reference
        values = gray[sample_mask > 0]
        if values.size:
            quantized = (values.astype(np.uint16) // 16).astype(np.int32)
            histogram = np.bincount(quantized, minlength=16)
            for colour_bin in np.argsort(histogram)[-4:]:
                if histogram[colour_bin] < 3:
                    continue
                centre = int(colour_bin) * 16 + 8
                lower, upper = max(0, centre - 22), min(255, centre + 22)
                candidates.append(cv2.bitwise_and(
                    cv2.inRange(gray, lower, upper), selection
                ))

        merged = np.zeros_like(reference)
        dilated_reference = cv2.dilate(
            reference, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            iterations=1,
        )
        for candidate in candidates:
            component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
                candidate, 8
            )
            for label in range(1, component_count):
                area = int(stats[label, cv2.CC_STAT_AREA])
                if area < 2 or area > selected_count * 0.45:
                    continue
                component = labels == label
                overlap = int(np.count_nonzero(component & reference_bool))
                nearby = int(np.count_nonzero(component & (dilated_reference > 0)))
                # Equivalent to accepting a component when it lowers XOR,
                # with a small antialiasing allowance around the locator.
                if overlap * 2 > area or (
                    overlap >= 2 and nearby / max(1, area) >= 0.72
                ):
                    merged[component] = 255

        overlap = int(np.count_nonzero((merged > 0) & reference_bool))
        reference_pixels = int(np.count_nonzero(reference))
        if overlap < max(3, int(reference_pixels * 0.12)):
            return reference

        # Fill enclosed glyph holes only when the filled version still agrees
        # better with the detector; then grow just enough to cover antialiasing.
        contours, _ = cv2.findContours(
            merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        filled = np.zeros_like(merged)
        if contours:
            cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
            merged_error = int(np.count_nonzero((merged > 0) ^ reference_bool))
            filled_error = int(np.count_nonzero((filled > 0) ^ reference_bool))
            if filled_error < merged_error:
                merged = filled
        merged = cv2.dilate(
            merged, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
            iterations=1,
        )
        return cv2.bitwise_and(merged, selection)

    @staticmethod
    def _flat_balloon_fill_colour(
        rgb: np.ndarray,
        text_mask: np.ndarray,
        selection: np.ndarray,
    ) -> tuple[int, int, int] | None:
        """Return a safe direct-fill colour for a containing flat balloon.

        Edges are searched after removing the text mask. The smallest contour
        containing the detected glyphs is treated as the balloon interior.
        Only statistically flat interiors qualify; gradients, translucency,
        and artwork continue through LaMa.
        """
        if rgb.shape[:2] != text_mask.shape or not np.any(text_mask):
            return None
        blurred = cv2.GaussianBlur(rgb, (3, 3), 0)
        gray = cv2.cvtColor(blurred, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 70, 140)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        text_guard = cv2.dilate(
            np.where(text_mask > 0, 255, 0).astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1,
        )
        edges[text_guard > 0] = 0
        edges[0, :] = edges[-1, :] = 255
        edges[:, 0] = edges[:, -1] = 255
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        glyphs = text_mask > 0
        best_mask: np.ndarray | None = None
        best_area = float("inf")
        for contour in contours:
            area = float(abs(cv2.contourArea(contour)))
            if area < 64 or area >= best_area:
                continue
            x, y, width, height = cv2.boundingRect(contour)
            # The artificial crop frame closes open edges for contour search,
            # but it is not itself evidence of a speech balloon. A completely
            # flat page/background therefore remains on the regular LaMa path.
            if (
                x <= 1 and y <= 1
                and x + width >= text_mask.shape[1] - 1
                and y + height >= text_mask.shape[0] - 1
            ):
                continue
            contour_mask = np.zeros(text_mask.shape, dtype=np.uint8)
            cv2.drawContours(contour_mask, [contour], -1, 255, cv2.FILLED)
            missing = int(np.count_nonzero(glyphs & (contour_mask == 0)))
            if missing > max(2, int(np.count_nonzero(glyphs) * 0.01)):
                continue
            best_mask, best_area = contour_mask, area
        if best_mask is None:
            return None

        sample = (best_mask > 0) & (text_guard == 0) & (selection > 0)
        pixels = rgb[sample].astype(np.float32)
        if pixels.shape[0] < 64:
            return None
        median = np.median(pixels, axis=0)
        deviations = np.abs(pixels - median)
        channel_std = pixels.std(axis=0)
        # Keep this deliberately conservative. A wrong direct fill is more
        # visible than one extra LaMa call.
        if float(channel_std.max()) > 7.0:
            return None
        if float(np.percentile(deviations, 90)) > 9.0:
            return None
        return tuple(int(round(value)) for value in median)

    @staticmethod
    def _solid_balloon_text_mask(
        rgb: np.ndarray, selection: np.ndarray,
    ) -> tuple[np.ndarray, tuple[int, int, int] | None]:
        """Detect text as holes inside a connected, nearly solid light balloon.

        This mirrors the supplied Photoshop workflow: select the white colour
        range, keep a plausible connected vignette, turn its outer contour
        into an interior, and fill the non-background islands inside it.  It
        does not depend on the OCR heatmap, so black and coloured CJK strokes
        are retained even when ``ocr.onnx`` returns no activation.

        A fill colour is returned only for demonstrably flat light balloons.
        Transparent, gradient and illustrated balloons return an empty mask
        and continue through the neural OCR + LaMa path.
        """
        empty = np.zeros(selection.shape, dtype=np.uint8)
        if rgb.shape[:2] != selection.shape or not np.any(selection):
            return empty, None
        selected = selection > 0
        pixels = rgb[selected].astype(np.int16)
        if pixels.shape[0] < 96:
            return empty, None

        channel_min = rgb.min(axis=2)
        channel_span = rgb.max(axis=2).astype(np.int16) - channel_min.astype(np.int16)
        # Photoshop's script uses white with tolerance 6. A slightly wider
        # range keeps JPEG/antialiased white while still rejecting artwork.
        # Detect the balloon on the whole context crop. ``selection`` is the
        # user's text box, not the balloon contour; treating it as a contour
        # drops glyphs that sit close to a box edge. The caller deliberately
        # supplies context around every box for this reason.
        light = np.where(
            (channel_min >= 232) & (channel_span <= 18), 255, 0
        ).astype(np.uint8)
        light = cv2.morphologyEx(
            light, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1,
        )
        sx, sy, sw, sh = cv2.boundingRect(selection)
        contract = max(5, min(20, int(round(min(sw, sh) * 0.08))))
        contract_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (contract * 2 + 1, contract * 2 + 1)
        )
        contracted = cv2.erode(light, contract_kernel, iterations=1)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(contracted, 8)
        if count <= 1:
            return empty, None
        selection_area = max(1, int(np.count_nonzero(selected)))
        # Only paths overlapping the text box can belong to its balloon.
        # Keep several pieces because dense CJK may split white into strips.
        candidates = []
        # A large box around the complete balloon is the normal workflow.
        # With dense multiline CJK, the contraction splits the white interior
        # into a ring of side lobes and narrow strips around the dialogue.
        # Keep every contracted piece enclosed by the search selection. Page
        # background beyond the impact crown reaches the selection boundary,
        # so it is excluded before the pieces are joined into one contour.
        central_x1 = sx + max(contract, int(round(sw * 0.08)))
        central_y1 = sy + max(contract, int(round(sh * 0.08)))
        central_x2 = sx + sw - max(contract, int(round(sw * 0.08)))
        central_y2 = sy + sh - max(contract, int(round(sh * 0.08)))
        minimum_piece = max(24, int(selection_area * 0.001))
        boundary_depth = max(3, min(12, contract // 2))
        inner_selection = cv2.erode(
            selection,
            cv2.getStructuringElement(
                cv2.MORPH_RECT, (boundary_depth * 2 + 1, boundary_depth * 2 + 1)
            ),
            iterations=1,
        )
        selection_boundary = cv2.bitwise_and(
            selection, cv2.bitwise_not(inner_selection)
        )
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            component = labels == label
            overlap = int(np.count_nonzero(component & selected))
            centre_x = x + width * 0.5
            centre_y = y + height * 0.5
            centrally_anchored = (
                central_x1 <= centre_x <= central_x2
                and central_y1 <= centre_y <= central_y2
            )
            touches_selection_edge = np.any(component & (selection_boundary > 0))
            if overlap >= minimum_piece and (
                not touches_selection_edge or centrally_anchored
            ):
                candidates.append(label)
        if not candidates:
            # Very small/tight selections may leave no centroid in the inner
            # band. Fall back to the largest overlapping component only;
            # never combine every large exterior component into one hull.
            overlapping = []
            for label in range(1, count):
                x, y, width, height, area = (int(value) for value in stats[label])
                ix1, iy1 = max(sx, x), max(sy, y)
                ix2, iy2 = min(sx + sw, x + width), min(sy + sh, y + height)
                overlap = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                if overlap >= 12 and area >= minimum_piece:
                    overlapping.append((area, label))
            if not overlapping:
                return empty, None
            candidates = [max(overlapping)[1]]
        # Dense CJK lines may split the contracted white balloon into several
        # horizontal components. Photoshop turns all surviving paths back
        # into one selection. Build their common hull before growing; choosing
        # a single component would retain only one strip between dialogue
        # lines. Narrow ray gaps are absent because this happens after the
        # contraction.
        contracted_candidates = np.where(
            np.isin(labels, np.asarray(candidates, dtype=labels.dtype)), 255, 0
        ).astype(np.uint8)
        core_contours, _ = cv2.findContours(
            contracted_candidates, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not core_contours:
            return empty, None
        points = np.concatenate(core_contours, axis=0)
        if points.shape[0] < 3:
            return empty, None
        filled_core = np.zeros_like(contracted_candidates)
        cv2.drawContours(
            filled_core, [cv2.convexHull(points)], -1, 255, thickness=cv2.FILLED
        )
        # Grow once by the same radius used to contract, matching the JSX
        # contract/grow cycle. The hull already closes glyph holes.
        interior = cv2.dilate(filled_core, contract_kernel, iterations=1)
        component = cv2.bitwise_and(light, interior)
        component_fraction = np.count_nonzero(component) / selection_area
        component_pixels = rgb[component > 0].astype(np.float32)
        fill = np.median(component_pixels, axis=0)
        spread = np.linalg.norm(component_pixels - fill, axis=1)
        if float(np.percentile(spread, 90)) > 16.0 or float(fill.min()) < 232.0:
            return empty, None

        if np.count_nonzero(interior) < selection_area * 0.08:
            return empty, None

        difference = np.linalg.norm(
            rgb.astype(np.float32) - fill.reshape(1, 1, 3), axis=2
        )
        text = np.where(
            (interior > 0) & selected & (difference > 6.0), 255, 0
        ).astype(np.uint8)
        if component_fraction > 0.93:
            # A nearly full white rectangle can be either a tight crop inside
            # a balloon or plain page background. The Photoshop script rejects
            # page-wide candidates by dimensions; for a local box, require
            # actual foreground through its central horizontal band. Side
            # frames/rays alone therefore cannot activate the solid route.
            central = text[
                sy + max(1, int(sh * 0.04)):sy + sh - max(1, int(sh * 0.04)),
                sx + int(sw * 0.20):sx + int(sw * 0.80),
            ]
            if np.count_nonzero(central) < max(12, int(np.count_nonzero(text) * 0.05)):
                return empty, None
        # Classify components against the reconstructed balloon silhouette,
        # never against the rectangular user selection. Dialogue components
        # reach the inner balloon. Impact rays remain attached to, or confined
        # within, its contour band. Retaining a complete anchored component
        # also preserves punctuation and glyph fragments close to an edge.
        silhouette_distance = cv2.distanceTransform(interior, cv2.DIST_L2, 5)
        _, _, interior_width, interior_height = cv2.boundingRect(interior)
        silhouette_scale = max(1, min(interior_width, interior_height))
        contour_depth = max(2.5, min(7.0, silhouette_scale * 0.016))
        anchor_depth = max(6.0, min(18.0, silhouette_scale * 0.045))
        contour_band = (interior > 0) & (silhouette_distance <= contour_depth)

        components, text_labels, text_stats, _ = cv2.connectedComponentsWithStats(
            text, 8
        )
        filtered = np.zeros_like(text)
        interior_area = max(1, int(np.count_nonzero(interior)))
        deferred_components: list[int] = []
        for item in range(1, components):
            x, y, width, height, area = (
                int(value) for value in text_stats[item]
            )
            if area < 2 or area > interior_area * 0.20:
                continue
            component = text_labels == item
            maximum_depth = float(silhouette_distance[component].max(initial=0.0))
            touches_contour = bool(np.any(component & contour_band))
            density = area / max(1, width * height)
            aspect = max(width, height) / max(1, min(width, height))
            long_contour_stroke = (
                touches_contour
                and (aspect >= 5.0 or density < 0.28)
                and max(width, height) >= max(12, int(silhouette_scale * 0.08))
            )
            horizontal_frame = (
                width >= interior_width * 0.34
                and height <= max(6, int(interior_height * 0.035))
            )
            vertical_frame = (
                height >= interior_height * 0.34
                and width <= max(6, int(interior_width * 0.018))
            )
            if (
                maximum_depth >= anchor_depth
                and not long_contour_stroke
                and not horizontal_frame
                and not vertical_frame
            ):
                filtered[component] = 255
            elif maximum_depth >= contour_depth and area <= interior_area * 0.025:
                deferred_components.append(item)
        if not np.any(filtered):
            return empty, None

        # Build complete text-line envelopes from the safely anchored glyphs.
        # CJK characters commonly consist of several disconnected strokes;
        # evaluating each stroke in isolation leaves the pieces nearest the
        # balloon edge behind. The envelope joins those pieces without ever
        # expanding to the rectangular selection or the impact crown.
        line_envelope = np.zeros_like(filtered)
        active_rows = np.flatnonzero(np.any(filtered > 0, axis=1))
        if active_rows.size:
            row_groups: list[tuple[int, int]] = []
            start = previous = int(active_rows[0])
            for row in active_rows[1:]:
                row = int(row)
                if row - previous > 3:
                    row_groups.append((start, previous))
                    start = row
                previous = row
            row_groups.append((start, previous))
            for line_y1, line_y2 in row_groups:
                rows = filtered[line_y1:line_y2 + 1]
                columns = np.flatnonzero(np.any(rows > 0, axis=0))
                if columns.size == 0:
                    continue
                line_height = max(1, line_y2 - line_y1 + 1)
                pad_y = max(6, min(14, int(round(line_height * 0.28))))
                pad_x = max(10, min(28, int(round(line_height * 0.55))))
                ex1 = max(sx, int(columns[0]) - pad_x)
                ex2 = min(sx + sw, int(columns[-1]) + pad_x + 1)
                ey1 = max(sy, line_y1 - pad_y)
                ey2 = min(sy + sh, line_y2 + pad_y + 1)
                line_envelope[ey1:ey2, ex1:ex2] = 255

        line_candidates = cv2.bitwise_and(text, line_envelope)
        line_count, line_labels, line_stats, _ = cv2.connectedComponentsWithStats(
            line_candidates, 8
        )
        for item in range(1, line_count):
            x, y, width, height, area = (
                int(value) for value in line_stats[item]
            )
            if area < 2 or area > interior_area * 0.20:
                continue
            component = line_labels == item
            maximum_depth = float(silhouette_distance[component].max(initial=0.0))
            touches_contour = bool(np.any(component & contour_band))
            density = area / max(1, width * height)
            aspect = max(width, height) / max(1, min(width, height))
            long_contour_stroke = (
                touches_contour
                and (aspect >= 5.0 or density < 0.28)
                and max(width, height) >= max(12, int(silhouette_scale * 0.08))
            )
            horizontal_frame = (
                width >= interior_width * 0.34
                and height <= max(6, int(interior_height * 0.035))
            )
            vertical_frame = (
                height >= interior_height * 0.34
                and width <= max(6, int(interior_width * 0.018))
            )
            if (
                maximum_depth >= contour_depth
                and not long_contour_stroke
                and not horizontal_frame
                and not vertical_frame
            ):
                filtered[component] = 255

        # Join all interior glyph components into text runs. Small detached
        # pieces (dots, commas, accents and CJK stroke fragments) are admitted
        # only when aligned with an anchored run and clear of the silhouette.
        horizontal_reach = max(20, min(90, int(round(sw * 0.11))))
        vertical_reach = max(8, min(28, int(round(sh * 0.070))))
        text_run_support = cv2.dilate(
            filtered,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (horizontal_reach * 2 + 1, vertical_reach * 2 + 1),
            ),
            iterations=1,
        )
        for item in deferred_components:
            component = text_labels == item
            x, y, width, height, area = (
                int(value) for value in text_stats[item]
            )
            aspect = max(width, height) / max(1, min(width, height))
            maximum_depth = float(silhouette_distance[component].max(initial=0.0))
            touches_contour = bool(np.any(component & contour_band))
            horizontal_frame = (
                width >= interior_width * 0.34
                and height <= max(6, int(interior_height * 0.035))
            )
            vertical_frame = (
                height >= interior_height * 0.34
                and width <= max(6, int(interior_width * 0.018))
            )
            compact_fragment = (
                area <= max(280, int(interior_area * 0.006))
                and (aspect <= 5.5 or max(width, height) <= 16)
            )
            # A detached CJK stroke can be narrow and highly elongated. It is
            # still text when it lies away from the real silhouette and is
            # aligned with an already confirmed run. Impact rays necessarily
            # touch the contour band and cannot enter through this exception.
            interior_text_fragment = (
                not touches_contour
                and area <= interior_area * 0.025
                and not horizontal_frame
                and not vertical_frame
            )
            if (
                (compact_fragment or interior_text_fragment)
                and maximum_depth >= contour_depth
                and np.any(component & (text_run_support > 0))
            ):
                filtered[component] = 255

        filtered = cv2.dilate(
            filtered, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            iterations=1,
        )
        # The last guard is the actual balloon contour. A one-pixel inset
        # prevents antialias expansion from painting over pointed silhouettes.
        contour_guard = np.where(
            silhouette_distance >= 1.5, 255, 0
        ).astype(np.uint8)
        filtered = cv2.bitwise_and(filtered, contour_guard)
        filtered = cv2.bitwise_and(filtered, selection)
        masked = int(np.count_nonzero(filtered))
        # Dense CJK dialogue can legitimately occupy a quarter of the balloon;
        # Photoshop's Grow/Expand step raises the editable mask close to half
        # of the interior. The contour invariant, rather than a low density
        # cap, is what keeps impact rays outside.
        if masked < 8 or masked > interior_area * 0.60:
            return empty, None
        return filtered, tuple(int(round(value)) for value in fill)

    @staticmethod
    def _protect_solid_balloon_silhouette(
        mask: np.ndarray, interior: np.ndarray,
    ) -> np.ndarray:
        """Remove impact strokes by their relation to the balloon contour.

        ``interior`` is the reconstructed light balloon, so its distance
        transform describes the real silhouette independently of how large or
        displaced the user's box is. Components confined to the contour band
        are outlines/rays. Components extending into the inner balloon are
        retained, including punctuation near the end of a dialogue line.
        """
        if mask.shape != interior.shape or not np.any(mask) or not np.any(interior):
            return mask
        distance = cv2.distanceTransform(
            np.where(interior > 0, 255, 0).astype(np.uint8), cv2.DIST_L2, 5
        )
        _, _, width, height = cv2.boundingRect(interior)
        scale = max(1, min(width, height))
        contour_depth = max(3.0, min(9.0, scale * 0.018))
        anchor_depth = max(8.0, min(24.0, scale * 0.055))
        contour_band = (interior > 0) & (distance <= contour_depth)
        inner_anchor = distance >= anchor_depth

        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        protected = mask.copy()
        for label in range(1, count):
            component = labels == label
            if not np.any(component & contour_band):
                continue
            x, y, component_width, component_height, area = (
                int(value) for value in stats[label]
            )
            anchored = np.any(component & inner_anchor)
            density = area / max(1, component_width * component_height)
            aspect = max(component_width, component_height) / max(
                1, min(component_width, component_height)
            )
            ray_like = (
                aspect >= 3.0 or density < 0.34
                or min(component_width, component_height) <= 4
            )
            # Anything restricted to the silhouette band is not dialogue.
            # A component that reaches the inner balloon is preserved unless
            # its shape is a long/sparse stroke characteristic of an impact
            # ray. This is evaluated before neighbouring rays can merge.
            if not anchored or ray_like:
                protected[component] = 0
        return protected

    @staticmethod
    def _solid_text_mask_from_detector(
        rgb: np.ndarray,
        detected: np.ndarray,
        selection: np.ndarray,
        fill_color: tuple[int, int, int] | list[int],
    ) -> np.ndarray:
        """Build complete text bands while preserving the real silhouette.

        The neural result locates text lines; it is not used as a glyph mask.
        Each line becomes a compact solid band, so disconnected CJK strokes,
        coloured text and antialiasing are removed together. Dark components
        connected to the context exterior form a silhouette guard and are
        subtracted from the bands. Consequently the user's rectangle remains
        a search area and can safely enclose an entire pointed balloon.
        """
        empty = np.zeros(selection.shape, dtype=np.uint8)
        if (
            rgb.shape[:2] != selection.shape
            or detected.shape != selection.shape
            or not np.any(selection)
        ):
            return empty
        line_seed = cv2.bitwise_and(
            np.where(detected > 0, 255, 0).astype(np.uint8), selection
        )
        line_seed = CleaningManager._protect_selection_boundary(
            line_seed, selection, preserve_thick_bands=True
        )
        line_seed = CleaningManager._filter_text_seed_components(
            line_seed, selection
        )
        line_seed = CleaningManager._suppress_peripheral_strokes(
            line_seed, selection
        )
        border_guard = CleaningManager._selection_border_guard(rgb, selection)
        line_seed = cv2.bitwise_and(
            line_seed,
            cv2.bitwise_not(cv2.dilate(
                border_guard,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                iterations=1,
            )),
        )
        if not np.any(line_seed):
            return empty
        # Neural line masks may contain tiny internal holes precisely over
        # dark glyph cores. Fill every external line contour before padding;
        # otherwise those holes survive as recognizable text fragments.
        line_contours, _ = cv2.findContours(
            line_seed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        completed_seed = np.zeros_like(line_seed)
        if line_contours:
            cv2.drawContours(
                completed_seed, line_contours, -1, 255, thickness=cv2.FILLED
            )
            line_seed = cv2.bitwise_and(completed_seed, selection)
        sx, sy, sw, sh = cv2.boundingRect(selection)
        line_count, _, line_stats, _ = cv2.connectedComponentsWithStats(
            line_seed, 8
        )
        detected_heights = line_stats[1:, cv2.CC_STAT_HEIGHT]
        typical_line_height = (
            float(np.median(detected_heights))
            if line_count > 1 and detected_heights.size else 20.0
        )
        line_pad_x = max(7, min(16, int(round(typical_line_height * 0.30))))
        line_pad_y = max(8, min(18, int(round(typical_line_height * 0.45))))
        line_envelope = cv2.dilate(
            line_seed,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (line_pad_x * 2 + 1, line_pad_y * 2 + 1),
            ),
            iterations=1,
        )
        line_envelope = cv2.bitwise_and(line_envelope, selection)

        fill = np.asarray(fill_color[:3], dtype=np.float32).reshape(1, 1, 3)
        difference = np.linalg.norm(rgb.astype(np.float32) - fill, axis=2)

        # Reconstruct the verified flat interior from background-coloured
        # pixels. Closing removes glyph/ray holes; filling the chosen external
        # contour gives a silhouette clip independent of the rectangular
        # user box. This is the key protection for pointed balloons.
        background_like = np.where(
            (difference <= 18.0) & (selection > 0), 255, 0
        ).astype(np.uint8)
        close_radius = max(5, min(13, int(round(typical_line_height * 0.22))))
        if close_radius % 2 == 0:
            close_radius += 1
        background_like = cv2.morphologyEx(
            background_like, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (close_radius, close_radius)
            ),
            iterations=1,
        )
        interior_count, interior_labels, interior_stats, _ = (
            cv2.connectedComponentsWithStats(background_like, 8)
        )
        support = cv2.dilate(
            line_seed,
            cv2.getStructuringElement(cv2.MORPH_RECT, (17, 9)),
            iterations=1,
        )
        best_label = 0
        best_score = 0
        for label in range(1, interior_count):
            area = int(interior_stats[label, cv2.CC_STAT_AREA])
            if area < max(48, int(np.count_nonzero(selection) * 0.02)):
                continue
            overlap = int(np.count_nonzero((interior_labels == label) & (support > 0)))
            score = overlap * 8 + min(area, int(np.count_nonzero(selection)))
            if overlap > 0 and score > best_score:
                best_label, best_score = label, score
        safe_interior = np.zeros_like(selection)
        if best_label:
            component = np.where(interior_labels == best_label, 255, 0).astype(np.uint8)
            component_contours, _ = cv2.findContours(
                component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if component_contours:
                largest = max(component_contours, key=cv2.contourArea)
                cv2.drawContours(
                    safe_interior, [largest], -1, 255, cv2.FILLED
                )
                safe_interior = cv2.erode(
                    safe_interior,
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
                    iterations=1,
                )

        # Anything unlike the verified fill and connected to the context
        # border belongs to artwork, the balloon outline or its impact crown.
        # This guard follows those shapes even when they do not touch the
        # user's rectangular selection.
        non_background = np.where(difference > 14.0, 255, 0).astype(np.uint8)
        component_count, component_labels, _, _ = cv2.connectedComponentsWithStats(
            non_background, 8
        )
        border_labels = np.unique(np.concatenate((
            component_labels[0, :], component_labels[-1, :],
            component_labels[:, 0], component_labels[:, -1],
        )))
        exterior = np.where(
            np.isin(component_labels, border_labels[border_labels != 0]), 255, 0
        ).astype(np.uint8)
        if np.any(exterior):
            exterior = cv2.dilate(
                exterior, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                iterations=1,
            )
        # The detector confirms a text line, not individual glyph pixels.
        # Replacing its compact band with the verified flat fill clears every
        # disconnected CJK stroke. Silhouette protection remains active for
        # punctuation recovered outside this confirmed band.
        # A rectangular line envelope can overlap pointed-balloon rays at its
        # ends. Those rays are connected to the crop exterior, while dialogue
        # is enclosed by the verified fill, so subtract the exterior guard
        # before the direct colour fill.
        filtered = cv2.bitwise_and(line_envelope, cv2.bitwise_not(exterior))
        if np.any(safe_interior):
            filtered = cv2.bitwise_and(filtered, safe_interior)

        # Ellipses and detached punctuation may sit beyond the model's line
        # rectangle. Search only in a horizontally limited continuation of a
        # confirmed line and accept compact components; remote rays cannot
        # enter this support and exterior-connected ones remain protected.
        punctuation_reach = max(100, min(220, int(round(sw * 0.38))))
        punctuation_y = max(10, min(24, int(round(sh * 0.055))))
        punctuation_support = cv2.dilate(
            line_seed,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (punctuation_reach * 2 + 1, punctuation_y * 2 + 1),
            ),
            iterations=1,
        )
        punctuation_support = cv2.bitwise_and(punctuation_support, selection)
        punctuation_candidates = np.where(
            (punctuation_support > 0) & (line_envelope == 0)
            & (exterior == 0) & (difference > 5.0), 255, 0
        ).astype(np.uint8)

        punctuation = np.zeros_like(filtered)
        near_line = cv2.dilate(
            line_envelope,
            cv2.getStructuringElement(cv2.MORPH_RECT, (121, 17)),
            iterations=1,
        )
        punctuation_count, punctuation_labels, punctuation_stats, _ = (
            cv2.connectedComponentsWithStats(punctuation_candidates, 8)
        )
        for label in range(1, punctuation_count):
            x, y, width, height, area = (
                int(value) for value in punctuation_stats[label]
            )
            aspect = max(width, height) / max(1, min(width, height))
            component = punctuation_labels == label
            round_fragment = (
                2 <= area <= 240 and width <= 22 and height <= 24
                and aspect <= 2.2
            )
            adjacent_sign = (
                2 <= area <= max(600, int(typical_line_height ** 2 * 0.55))
                and width <= max(24, int(typical_line_height * 0.65))
                and height <= max(48, int(typical_line_height * 1.45))
                and aspect <= 6.0
                and np.any(component & (near_line > 0))
            )
            if round_fragment or adjacent_sign:
                punctuation[component] = 255
        if np.any(punctuation):
            punctuation = cv2.dilate(
                punctuation,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
                iterations=1,
            )
            punctuation = cv2.bitwise_and(punctuation, punctuation_support)
            punctuation = cv2.bitwise_and(punctuation, cv2.bitwise_not(exterior))
            filtered = cv2.bitwise_or(filtered, punctuation)
        return cv2.bitwise_and(filtered, selection)

    @staticmethod
    def _protect_solid_balloon_boundary(
        mask: np.ndarray, selection: np.ndarray,
    ) -> np.ndarray:
        """Remove sparse impact-ray crowns without deleting edge-clipped text.

        Grow/Expand can join many adjacent rays into one component. Such a
        crown touches the search-box edge but occupies only a small fraction
        of its bounding rectangle. A clipped text line is much denser, so it
        remains editable even when the user draws a tight box.
        """
        sx, sy, sw, sh = cv2.boundingRect(selection)
        if sw <= 0 or sh <= 0 or not np.any(mask):
            return mask
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        protected = mask.copy()
        tolerance = max(2, min(7, round(min(sw, sh) * 0.015)))
        thin_limit = max(7, round(min(sw, sh) * 0.035))
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            touches_edge = (
                x <= sx + tolerance or y <= sy + tolerance
                or x + width >= sx + sw - tolerance
                or y + height >= sy + sh - tolerance
            )
            long_component = width >= sw * 0.16 or height >= sh * 0.16
            density = area / max(1, width * height)
            sparse_or_thin = density < 0.42 or min(width, height) <= thin_limit
            if touches_edge and long_component and sparse_or_thin:
                protected[labels == label] = 0
        return protected

    @classmethod
    def _recover_flat_background_text(
        cls,
        rgb: np.ndarray,
        neural_seed: np.ndarray,
        selection: np.ndarray,
        border_guard: np.ndarray | None = None,
    ) -> np.ndarray:
        """Recover missed black or coloured text lines on a flat balloon.

        This is intentionally conservative: it activates only when a dominant
        local background colour exists, keeps glyph-like horizontal/vertical
        runs, and rejects every component connected to the box boundary. It
        therefore complements the OCR model without turning the whole balloon
        or its impact rays into an inpainting mask.
        """
        sx, sy, sw, sh = cv2.boundingRect(selection)
        recovered = np.zeros(selection.shape, dtype=np.uint8)
        if sw <= 0 or sh <= 0 or rgb.shape[:2] != selection.shape:
            return recovered
        selected = selection > 0
        pixels = rgb[selected]
        if pixels.shape[0] < 96:
            return recovered

        # Find the dominant colour bin rather than assuming white. This also
        # supports solid coloured balloons and coloured/gradient lettering.
        quantized = pixels.astype(np.uint16) >> 4
        codes = (quantized[:, 0] << 8) | (quantized[:, 1] << 4) | quantized[:, 2]
        histogram = np.bincount(codes, minlength=4096)
        dominant_code = int(histogram.argmax())
        dominant = codes == dominant_code
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        dominance = float(np.count_nonzero(dominant)) / max(1, pixels.shape[0])
        candidate = np.zeros(selection.shape, dtype=np.uint8)
        if dominance >= 0.20 and np.count_nonzero(dominant) >= 48:
            background_rgb = np.median(pixels[dominant], axis=0).astype(np.uint8)
            background_lab = cv2.cvtColor(
                background_rgb.reshape(1, 1, 3), cv2.COLOR_RGB2LAB
            ).astype(np.float32)[0, 0]
            distances = np.linalg.norm(lab - background_lab, axis=2)
            selected_distances = distances[selected]
            # Compression/antialiasing may perturb a flat fill by a few Lab
            # units. Text colours remain farther away, including reds.
            noise = float(np.percentile(selected_distances, 65))
            threshold = max(9.0, min(28.0, noise + 5.0))
            candidate[(distances > threshold) & selected] = 255

        # A YOLO/search box can contain a large piece of artwork as well as a
        # small white speech balloon. In that case no colour is globally
        # dominant and the old code returned no mask at all, even for clear
        # black text. Recover ink against a *local* light background instead.
        # The following line/ray guards still run below, so this does not turn
        # the balloon outline or impact spikes into a LaMa mask.
        lightness = lab[:, :, 0]
        neighbourhood = max(17, min(37, (min(sw, sh) // 10) | 1))
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (neighbourhood, neighbourhood)
        )
        local_background = cv2.morphologyEx(
            lightness.astype(np.uint8), cv2.MORPH_CLOSE, kernel
        ).astype(np.float32)
        light_support = cv2.boxFilter(
            (lightness >= 205.0).astype(np.float32), -1,
            (neighbourhood, neighbourhood), normalize=True,
            borderType=cv2.BORDER_REPLICATE,
        )
        locally_contrasted = (
            selected
            & (local_background >= 215.0)
            & ((local_background - lightness) >= 16.0)
            & (light_support >= 0.38)
        )
        candidate[locally_contrasted] = 255

        # Remove protected rays before glyph completion; subtracting them only
        # at the end would let dilation grow a fringe beyond the guard.
        if border_guard is None:
            border_guard = cls._selection_border_guard(rgb, selection)
        candidate = cv2.bitwise_and(candidate, cv2.bitwise_not(border_guard))
        candidate = cls._protect_selection_boundary(candidate, selection)
        if not np.any(candidate):
            return recovered

        # Remove isolated compression specks while preserving thin coloured
        # strokes and punctuation.
        count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
        filtered = np.zeros_like(candidate)
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            if area < 3:
                continue
            aspect = max(width, height) / max(1, min(width, height))
            if aspect > 16.0 and min(width, height) <= 2:
                continue
            filtered[labels == label] = 255
        if not np.any(filtered):
            return recovered

        seed_bounds = cv2.boundingRect(neural_seed) if np.any(neural_seed) else (0, 0, 0, 0)
        seed_x, seed_y, seed_width, seed_height = seed_bounds

        def runs(active: np.ndarray) -> list[tuple[int, int]]:
            values = np.flatnonzero(active)
            if not values.size:
                return []
            result: list[tuple[int, int]] = []
            start = previous = int(values[0])
            for value in values[1:]:
                value = int(value)
                if value > previous + 1:
                    result.append((start, previous + 1))
                    start = value
                previous = value
            result.append((start, previous + 1))
            return result

        def accept_horizontal(y1: int, y2: int) -> None:
            band = filtered[y1:y2, sx:sx + sw]
            ys, xs = np.nonzero(band)
            if not xs.size:
                return
            span = int(xs.max() - xs.min() + 1)
            ink = int(xs.size)
            band_height = y2 - y1
            components, _, component_stats, _ = cv2.connectedComponentsWithStats(band, 8)
            glyphs = sum(
                1 for label in range(1, components)
                if int(component_stats[label, cv2.CC_STAT_AREA]) >= 3
                and int(component_stats[label, cv2.CC_STAT_HEIGHT]) >= 2
            )
            seeded = np.any(neural_seed[y1:y2, sx:sx + sw])
            supported = True
            if seed_width > 0 and not seeded:
                support_pad = max(12, int(sw * 0.06))
                support_x1 = max(sx, seed_x - support_pad)
                support_x2 = min(sx + sw, seed_x + seed_width + support_pad)
                central_ink = int(np.count_nonzero(filtered[y1:y2, support_x1:support_x2]))
                supported = central_ink >= max(12, int(ink * 0.22))
            if (
                band_height <= max(90, int(sh * 0.32))
                and span >= max(24, int(sw * 0.07))
                and ink >= max(18, int(span * 0.10))
                and (glyphs >= 2 or seeded)
                and supported
            ):
                recovered[y1:y2, sx:sx + sw] = cv2.bitwise_or(
                    recovered[y1:y2, sx:sx + sw], band
                )

        row_counts = np.count_nonzero(filtered[:, sx:sx + sw], axis=1)
        row_active = (row_counts >= max(3, int(sw * 0.006))).astype(np.uint8)
        row_active = cv2.dilate(
            row_active.reshape(-1, 1), np.ones((5, 1), np.uint8), iterations=1
        ).ravel() > 0
        for y1, y2 in runs(row_active):
            accept_horizontal(y1, y2)

        # Vertical text uses the same logic with transposed axes.
        # If the model already established a horizontal layout, do not let
        # repeated side rays be reinterpreted as vertical writing.
        if seed_width > 0 and seed_width >= seed_height:
            recovered = cls._protect_selection_boundary(recovered, selection)
            recovered = cls._suppress_peripheral_strokes(recovered, selection)
            return cv2.bitwise_and(recovered, selection)
        column_counts = np.count_nonzero(filtered[sy:sy + sh, :], axis=0)
        column_active = (column_counts >= max(3, int(sh * 0.006))).astype(np.uint8)
        column_active = cv2.dilate(
            column_active.reshape(1, -1), np.ones((1, 5), np.uint8), iterations=1
        ).ravel() > 0
        for x1, x2 in runs(column_active):
            band = filtered[sy:sy + sh, x1:x2]
            ys, xs = np.nonzero(band)
            if not ys.size:
                continue
            span = int(ys.max() - ys.min() + 1)
            ink = int(ys.size)
            band_width = x2 - x1
            seeded = np.any(neural_seed[sy:sy + sh, x1:x2])
            supported = True
            if seed_height > 0 and not seeded:
                support_pad = max(12, int(sh * 0.06))
                support_y1 = max(sy, seed_y - support_pad)
                support_y2 = min(sy + sh, seed_y + seed_height + support_pad)
                central_ink = int(np.count_nonzero(filtered[support_y1:support_y2, x1:x2]))
                supported = central_ink >= max(12, int(ink * 0.22))
            if (
                band_width <= max(90, int(sw * 0.32))
                and span >= max(24, int(sh * 0.07))
                and ink >= max(18, int(span * 0.10))
                and (seeded or band_width >= 4)
                and supported
            ):
                recovered[sy:sy + sh, x1:x2] = cv2.bitwise_or(
                    recovered[sy:sy + sh, x1:x2], band
                )

        recovered = cls._protect_selection_boundary(recovered, selection)
        recovered = cls._suppress_peripheral_strokes(recovered, selection)
        return cv2.bitwise_and(recovered, selection)

    @staticmethod
    def _filter_text_seed_components(mask: np.ndarray, selection: np.ndarray) -> np.ndarray:
        """Remove isolated detector blobs that are not part of a text run.

        Small punctuation is retained when it aligns with a detected
        horizontal or vertical run. If the box contains only one character,
        there is no run to compare against and the original mask is kept.
        """
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        sx, sy, sw, sh = cv2.boundingRect(selection)
        if count <= 2:
            if count == 1:
                return mask
            x, y, width, height, area = (int(value) for value in stats[1])
            selection_area = max(1, sw * sh)
            isolated = area / selection_area < 0.008
            outer_x = max(8, int(sw * 0.12))
            outer_y = max(8, int(sh * 0.12))
            near_outer_edge = (
                x + width <= sx + outer_x
                or x >= sx + sw - outer_x
                or y + height <= sy + outer_y
                or y >= sy + sh - outer_y
            )
            # A lone, tiny activation in the outer band of a large box is
            # overwhelmingly a balloon ray/outline, not a text run. A tight
            # one-character box remains valid because its area ratio is high.
            return np.zeros_like(mask) if isolated and near_outer_edge else mask
        runs: list[int] = []
        for label in range(1, count):
            _, _, width, height, area = (int(value) for value in stats[label])
            if area >= 12 and (
                width >= max(22, int(sw * 0.06))
                or height >= max(22, int(sh * 0.16))
            ):
                runs.append(label)
        if not runs:
            return mask

        filtered = np.zeros_like(mask)
        for label in range(1, count):
            if label in runs:
                filtered[labels == label] = 255
                continue
            cx, cy = centroids[label]
            aligned = False
            for run in runs:
                x, y, width, height, _ = (int(value) for value in stats[run])
                horizontal = width >= height
                if horizontal:
                    aligned = y - max(8, height // 2) <= cy <= y + height + max(8, height // 2)
                else:
                    aligned = x - max(8, width // 2) <= cx <= x + width + max(8, width // 2)
                if aligned:
                    break
            if aligned:
                filtered[labels == label] = 255
        return filtered if np.any(filtered) else mask

    @staticmethod
    def _suppress_peripheral_strokes(mask: np.ndarray, selection: np.ndarray) -> np.ndarray:
        """Remove impact-ray tips outside the actual text band.

        A detector can activate on disconnected tips that sit well inside a
        generously drawn box, so border-connected filtering cannot catch
        them. Real multiline text produces one or more dominant components;
        those components define a padded text envelope. Only small/thin
        components in the outer band and outside that envelope are removed.
        Tight boxes and single-character detections deliberately fall back to
        the original mask.
        """
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        sx, sy, sw, sh = cv2.boundingRect(selection)
        if count <= 3 or sw <= 0 or sh <= 0:
            return mask

        areas = stats[1:, cv2.CC_STAT_AREA].astype(np.int32)
        maximum_area = int(areas.max(initial=0))
        if maximum_area < 18:
            return mask
        dominant_limit = max(14, int(round(maximum_area * 0.12)))
        dominant: list[int] = []
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            aspect = max(width, height) / max(1, min(width, height))
            if area >= dominant_limit and (aspect <= 6.0 or area >= maximum_area * 0.45):
                dominant.append(label)
        if not dominant:
            return mask

        left = min(int(stats[label, cv2.CC_STAT_LEFT]) for label in dominant)
        top = min(int(stats[label, cv2.CC_STAT_TOP]) for label in dominant)
        right = max(
            int(stats[label, cv2.CC_STAT_LEFT] + stats[label, cv2.CC_STAT_WIDTH])
            for label in dominant
        )
        bottom = max(
            int(stats[label, cv2.CC_STAT_TOP] + stats[label, cv2.CC_STAT_HEIGHT])
            for label in dominant
        )
        pad_x = max(12, min(48, int(round(sw * 0.08))))
        pad_y = max(12, min(42, int(round(sh * 0.08))))
        envelope = (
            max(sx, left - pad_x), max(sy, top - pad_y),
            min(sx + sw, right + pad_x), min(sy + sh, bottom + pad_y),
        )
        outer_x = max(10, int(round(sw * 0.16)))
        outer_y = max(10, int(round(sh * 0.16)))
        result = mask.copy()
        dominant_set = set(dominant)
        for label in range(1, count):
            if label in dominant_set:
                continue
            x, y, width, height, area = (int(value) for value in stats[label])
            cx, cy = x + width / 2.0, y + height / 2.0
            outside_envelope = not (
                envelope[0] <= cx <= envelope[2] and envelope[1] <= cy <= envelope[3]
            )
            peripheral = (
                cx <= sx + outer_x or cx >= sx + sw - outer_x
                or cy <= sy + outer_y or cy >= sy + sh - outer_y
            )
            aspect = max(width, height) / max(1, min(width, height))
            thin = aspect >= 3.5 or min(width, height) <= 3
            small = area < max(dominant_limit, int(maximum_area * 0.22))
            if outside_envelope and peripheral and (small or thin):
                result[labels == label] = 0
        return result

    @staticmethod
    def _cluster_regions(boxes: list[tuple[int, int, int, int]]) -> list[list[int]]:
        """Group region indices whose (context-padded) boxes overlap or touch.

        Manga pages routinely pack several speech balloons within the 150px
        detection context of one another. Without this, each one triggers
        its own OCR forward pass over largely the same pixels — the single
        biggest CPU cost in this module on a laptop. Clustering lets one
        pass cover all of them. This is only safe because _text_mask's
        dilation kernel is a fixed size rather than a percentage of the
        crop's width — a width-relative kernel would make the mask depend
        on which crop (individual or clustered) produced it.
        """
        n = len(boxes)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        def overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2

        for i in range(n):
            for j in range(i + 1, n):
                if overlaps(boxes[i], boxes[j]):
                    union(i, j)

        clusters: dict[int, list[int]] = {}
        for i in range(n):
            clusters.setdefault(find(i), []).append(i)
        return list(clusters.values())

    def _clean_legacy(
        self,
        image_path: Path,
        regions: list[dict],
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> dict:
        if not regions:
            raise ValueError("No hay regiones detectadas para limpiar.")

        with Image.open(image_path) as opened:
            page = np.asarray(opened.convert("RGB"))
        page_height, page_width = page.shape[:2]

        target_boxes: list[tuple[int, int, int, int]] = []
        context_boxes: list[tuple[int, int, int, int]] = []
        for region in regions:
            x1 = max(0, int(region["x"]))
            y1 = max(0, int(region["y"]))
            x2 = min(page_width, x1 + max(1, int(region["width"])))
            y2 = min(page_height, y1 + max(1, int(region["height"])))
            target_boxes.append((x1, y1, x2, y2))
            context_boxes.append((
                max(0, x1 - self.DETECTION_CONTEXT),
                max(0, y1 - self.DETECTION_CONTEXT),
                min(page_width, x2 + self.DETECTION_CONTEXT),
                min(page_height, y2 + self.DETECTION_CONTEXT),
            ))

        # Reuse OCR inference where 150px context boxes overlap. The neural
        # mask dilation is fixed-size, so slicing the shared result is
        # equivalent away from the outer crop edge. Very large chained groups
        # are split to keep memory bounded on long webtoon strips.
        detected_masks: list[np.ndarray | None] = [None] * len(context_boxes)
        detection_groups: list[list[int]] = []
        for cluster in self._cluster_regions(context_boxes):
            pending: list[int] = []
            for item in cluster:
                candidate = [*pending, item]
                ux1 = min(context_boxes[index][0] for index in candidate)
                uy1 = min(context_boxes[index][1] for index in candidate)
                ux2 = max(context_boxes[index][2] for index in candidate)
                uy2 = max(context_boxes[index][3] for index in candidate)
                if pending and ((ux2 - ux1) > 2048 or (uy2 - uy1) > 2048):
                    detection_groups.append(pending)
                    pending = [item]
                else:
                    pending = candidate
            if pending:
                detection_groups.append(pending)

        for group in detection_groups:
            gx1 = min(context_boxes[index][0] for index in group)
            gy1 = min(context_boxes[index][1] for index in group)
            gx2 = max(context_boxes[index][2] for index in group)
            gy2 = max(context_boxes[index][3] for index in group)
            shared = self._text_mask(page[gy1:gy2, gx1:gx2])
            for item in group:
                cx1, cy1, cx2, cy2 = context_boxes[item]
                detected_masks[item] = np.ascontiguousarray(
                    shared[cy1 - gy1:cy2 - gy1, cx1 - gx1:cx2 - gx1]
                )

        patches: list[dict] = []
        targets: list[dict] = []
        mask_entries: list[tuple[int, int, np.ndarray]] = []
        inpaint_boxes: list[tuple[int, int, int, int]] = []
        valid_targets: list[tuple[int, int, int, int]] = []

        for index, (x1, y1, x2, y2) in enumerate(target_boxes, start=1):
            if cancelled():
                break
            if x2 <= x1 or y2 <= y1:
                continue
            targets.append({"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1})

            cx1, cy1, cx2, cy2 = context_boxes[index - 1]
            detection_crop = page[cy1:cy2, cx1:cx2]
            detected = detected_masks[index - 1]
            if detected is None:
                progress(int(index * 45 / len(regions)))
                continue

            selection = np.zeros_like(detected)
            rx1, ry1 = x1 - cx1, y1 - cy1
            rx2, ry2 = x2 - cx1, y2 - cy1
            selection[ry1:ry2, rx1:rx2] = 255
            final_mask = self._build_text_mask(detection_crop, detected, selection)
            if not np.any(final_mask):
                progress(int(index * 45 / len(regions)))
                continue
            mx, my, mw, mh = cv2.boundingRect(final_mask)
            compact_mask = np.ascontiguousarray(final_mask[my:my + mh, mx:mx + mw])
            gx, gy = cx1 + mx, cy1 + my
            mask_entries.append((gx, gy, compact_mask))
            inpaint_boxes.append((
                max(0, gx - self.INPAINT_CONTEXT),
                max(0, gy - self.INPAINT_CONTEXT),
                min(page_width, gx + mw + self.INPAINT_CONTEXT),
                min(page_height, gy + mh + self.INPAINT_CONTEXT),
            ))
            valid_targets.append((x1, y1, x2, y2))
            progress(int(index * 45 / len(regions)))

        # Group masks whose 64px LaMa contexts overlap. Several nearby text
        # boxes then share a single model inference instead of paying the
        # fixed ONNX cost once per box.
        clusters = self._cluster_regions(inpaint_boxes)
        # Keep the exact model mask with every result.  The canvas must never
        # infer that an entire rectangular crop was modified: doing so is what
        # produced white strips.  Using the input mask (rather than a colour
        # difference) also preserves valid white-on-white restorations.
        processed: list[tuple[int, int, np.ndarray, np.ndarray]] = []
        inpaint_jobs: list[
            tuple[int, int, tuple[int, int, int, int], np.ndarray, np.ndarray]
        ] = []
        masked_pixels = 0
        for cluster_number, members in enumerate(clusters, start=1):
            if cancelled():
                break
            bx1 = min(inpaint_boxes[item][0] for item in members)
            by1 = min(inpaint_boxes[item][1] for item in members)
            bx2 = max(inpaint_boxes[item][2] for item in members)
            by2 = max(inpaint_boxes[item][3] for item in members)
            cluster_image = np.ascontiguousarray(page[by1:by2, bx1:bx2])
            cluster_mask = np.zeros((by2 - by1, bx2 - bx1), dtype=np.uint8)
            for item in members:
                mx, my, compact_mask = mask_entries[item]
                mh, mw = compact_mask.shape
                target = cluster_mask[my - by1:my - by1 + mh, mx - bx1:mx - bx1 + mw]
                cv2.bitwise_or(target, compact_mask, dst=target)
            masked_pixels += int(np.count_nonzero(cluster_mask))
            x, y, width, height = cv2.boundingRect(cluster_mask)
            context = max(32, int(self.INPAINT_CONTEXT))
            lx1, ly1 = max(0, x - context), max(0, y - context)
            lx2 = min(cluster_mask.shape[1], x + width + context)
            ly2 = min(cluster_mask.shape[0], y + height + context)
            local_bounds = (lx1, ly1, lx2, ly2)
            local_image = np.ascontiguousarray(cluster_image[ly1:ly2, lx1:lx2])
            local_mask = np.ascontiguousarray(cluster_mask[ly1:ly2, lx1:lx2])
            inpaint_jobs.append((bx1, by1, local_bounds, local_image, local_mask))
            progress(45 + int(cluster_number * 8 / max(1, len(clusters))))

        restored_jobs = self._lama_many([
            (local_image, local_mask)
            for _, _, _, local_image, local_mask in inpaint_jobs
        ])
        for job_number, (job, restored) in enumerate(zip(inpaint_jobs, restored_jobs), start=1):
            if cancelled():
                break
            bx1, by1, local_bounds, local_image, local_mask = job
            cleaned = self._composite_lama(local_image, restored, local_mask)
            local_selection = np.full(local_mask.shape, 255, dtype=np.uint8)
            cleaned, _ = self._refine_lama_residuals(
                cleaned, local_mask, local_selection, max_passes=1
            )
            lx1, ly1, lx2, ly2 = local_bounds
            # ``local_mask`` is the exact cluster slice and remains valid even
            # after the cluster-building loop has moved to another mask.
            exact_mask = local_mask
            processed.append((bx1 + lx1, by1 + ly1, cleaned, exact_mask))
            progress(53 + int(job_number * 42 / max(1, len(inpaint_jobs))))

        # Keep patches aligned to user boxes for persistence/undo, composing
        # only the processed crop intersections instead of copying a full
        # 60k-pixel page into memory.
        for x1, y1, x2, y2 in valid_targets:
            pixels = np.ascontiguousarray(page[y1:y2, x1:x2].copy())
            patch_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
            for px, py, cleaned, exact_mask in processed:
                ph, pw = cleaned.shape[:2]
                ix1, iy1 = max(x1, px), max(y1, py)
                ix2, iy2 = min(x2, px + pw), min(y2, py + ph)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                pixels[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1] = cleaned[
                    iy1 - py:iy2 - py, ix1 - px:ix2 - px
                ]
                mask_slice = exact_mask[iy1 - py:iy2 - py, ix1 - px:ix2 - px]
                target_slice = patch_mask[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1]
                np.maximum(target_slice, mask_slice, out=target_slice)
            if np.any(patch_mask):
                patches.append({
                    "x": x1,
                    "y": y1,
                    "pixels": pixels,
                    "mask": patch_mask,
                    "kind": "automatic",
                })
        progress(100)

        return {
            "patches": patches,
            "targets": targets,
            "provider": self.provider_name,
            "runtime": self.runtime_label,
            "masked_pixels": masked_pixels,
        }

    @staticmethod
    def _copy_mask_entry(entry: dict | None) -> dict | None:
        if entry is None:
            return None
        return {
            **entry,
            "target": dict(entry.get("target", {})),
            "mask": np.ascontiguousarray(np.asarray(entry["mask"]).copy()),
        }

    @staticmethod
    def _mask_entry_bytes(entry: dict | None) -> int:
        return 0 if entry is None else int(np.asarray(entry.get("mask", [])).nbytes)

    @staticmethod
    def _copy_mask_plan(plan: dict) -> dict:
        return {
            **plan,
            "targets": [dict(target) for target in plan.get("targets", [])],
            "entries": [
                CleaningManager._copy_mask_entry(entry)
                for entry in plan.get("entries", [])
            ],
        }

    def _mask_source_key(
        self, image_path: Path, source_states: dict[str, dict] | None = None,
    ) -> tuple:
        stat = image_path.stat()
        return (
            str(image_path.resolve()).casefold(), stat.st_mtime_ns, stat.st_size,
            self.MASK_PIPELINE_VERSION, int(self.aggressiveness), state_signature(source_states),
        )

    def _mask_cache_key(
        self, image_path: Path, regions: list[dict], source_states: dict[str, dict] | None = None,
    ) -> tuple:
        boxes = tuple(
            (int(region["x"]), int(region["y"]), int(region["width"]), int(region["height"]))
            for region in regions
        )
        return (*self._mask_source_key(image_path, source_states), boxes)

    def prepare_masks(
        self,
        image_path: Path,
        regions: list[dict],
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
        source_states: dict[str, dict] | None = None,
    ) -> dict:
        """Detect editable text masks without executing LaMa."""
        if not regions:
            raise ValueError("No hay regiones detectadas para preparar la máscara.")
        cache_key = self._mask_cache_key(image_path, regions, source_states)
        with self._mask_cache_lock:
            cached = self._mask_cache.get(cache_key)
            if cached is not None:
                self._mask_cache_hits += 1
                self._mask_cache.move_to_end(cache_key)
                result = self._copy_mask_plan(cached)
                result["cached"] = True
                progress(100)
                return result
            self._mask_cache_misses += 1

        page = self._load_source_page(image_path, source_states)
        page_height, page_width = page.shape[:2]
        target_boxes: list[tuple[int, int, int, int]] = []
        context_boxes: list[tuple[int, int, int, int]] = []
        for region in regions:
            x1 = max(0, int(region["x"])); y1 = max(0, int(region["y"]))
            x2 = min(page_width, x1 + max(1, int(region["width"])))
            y2 = min(page_height, y1 + max(1, int(region["height"])))
            target_boxes.append((x1, y1, x2, y2))
            context_boxes.append((
                max(0, x1 - self.DETECTION_CONTEXT), max(0, y1 - self.DETECTION_CONTEXT),
                min(page_width, x2 + self.DETECTION_CONTEXT),
                min(page_height, y2 + self.DETECTION_CONTEXT),
            ))

        source_key = self._mask_source_key(image_path, source_states)
        entry_cache_keys = [(*source_key, box) for box in target_boxes]
        cached_entries: dict[int, dict | None] = {}
        fresh_indices: list[int] = []
        with self._mask_cache_lock:
            for index, entry_key in enumerate(entry_cache_keys):
                cached_record = self._mask_entry_cache.get(entry_key)
                if cached_record is None:
                    self._mask_entry_cache_misses += 1
                    fresh_indices.append(index)
                    continue
                self._mask_entry_cache_hits += 1
                self._mask_entry_cache.move_to_end(entry_key)
                cached_entries[index] = self._copy_mask_entry(cached_record.get("entry"))

        detected_masks: list[np.ndarray | None] = [None] * len(context_boxes)
        solid_masks: list[tuple[np.ndarray, tuple[int, int, int]] | None] = [
            None
        ] * len(context_boxes)
        solid_needs_detector = [False] * len(context_boxes)
        # Resolve flat balloons first. Their contour-derived mask already
        # captures black/coloured CJK and protects impact rays, so sending
        # those boxes through ocr.onnx again only adds latency and can remove
        # strokes the neural locator did not activate on.
        unresolved: list[int] = []
        def classify_solid(index: int):
            x1, y1, x2, y2 = target_boxes[index]
            cx1, cy1, cx2, cy2 = context_boxes[index]
            crop = page[cy1:cy2, cx1:cx2]
            selection = np.zeros(crop.shape[:2], dtype=np.uint8)
            selection[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1] = 255
            solid_mask, fill = self._solid_balloon_text_mask(crop, selection)
            return index, solid_mask, fill

        workers = {"low": 2, "balanced": min(os.cpu_count() or 4, 8), "high": min(os.cpu_count() or 8, 12)}.get(self.resource_profile, 4)
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(fresh_indices)))) as executor:
            classified = executor.map(classify_solid, fresh_indices)
            for completed, (index, solid_mask, fill) in enumerate(classified, start=1):
                if cancelled():
                    return {"entries": [], "targets": [], "cached": False}
                if fill is not None and np.any(solid_mask):
                    solid_masks[index] = (solid_mask, fill)
                    x1, y1, x2, y2 = target_boxes[index]
                    cx1, cy1, _, _ = context_boxes[index]
                    local_selection = np.zeros_like(solid_mask)
                    local_selection[
                        y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1
                    ] = 255
                    selection_pixels = max(1, int(np.count_nonzero(local_selection)))
                    mask_fraction = np.count_nonzero(solid_mask) / selection_pixels
                    boundary = cv2.bitwise_and(
                        local_selection,
                        cv2.bitwise_not(cv2.erode(
                            local_selection,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)),
                            iterations=1,
                        )),
                    )
                    boundary_pixels = int(np.count_nonzero(
                        (solid_mask > 0) & (boundary > 0)
                    ))
                    # A text mask should be sparse and remain inside the real
                    # balloon. Large/boundary-touching masks indicate that a
                    # white page wedge was mistaken for dialogue; validate
                    # those cases with the neural line locator.
                    needs_detector = (
                        mask_fraction > 0.22
                        or boundary_pixels > max(20, int((x2 - x1) * 0.04))
                    )
                    solid_needs_detector[index] = needs_detector
                    if needs_detector:
                        unresolved.append(index)
                else:
                    unresolved.append(index)
                progress(int(completed * 20 / max(1, len(fresh_indices))))

        detection_groups: list[list[int]] = []
        unresolved_boxes = [context_boxes[index] for index in unresolved]
        for unresolved_cluster in self._cluster_regions(unresolved_boxes):
            cluster = [unresolved[item] for item in unresolved_cluster]
            pending: list[int] = []
            for item in cluster:
                candidate = [*pending, item]
                ux1 = min(context_boxes[index][0] for index in candidate)
                uy1 = min(context_boxes[index][1] for index in candidate)
                ux2 = max(context_boxes[index][2] for index in candidate)
                uy2 = max(context_boxes[index][3] for index in candidate)
                if pending and ((ux2 - ux1) > 2048 or (uy2 - uy1) > 2048):
                    detection_groups.append(pending); pending = [item]
                else:
                    pending = candidate
            if pending:
                detection_groups.append(pending)

        detection_crops: list[np.ndarray] = []
        detection_bounds: list[tuple[int, int, int, int]] = []
        for group in detection_groups:
            gx1 = min(context_boxes[index][0] for index in group)
            gy1 = min(context_boxes[index][1] for index in group)
            gx2 = max(context_boxes[index][2] for index in group)
            gy2 = max(context_boxes[index][3] for index in group)
            detection_bounds.append((gx1, gy1, gx2, gy2))
            detection_crops.append(np.ascontiguousarray(page[gy1:gy2, gx1:gx2]))

        shared_masks = self._text_mask_many(detection_crops)
        for group_number, (group, bounds, shared) in enumerate(
            zip(detection_groups, detection_bounds, shared_masks), start=1,
        ):
            if cancelled():
                return {"entries": [], "targets": [], "cached": False}
            gx1, gy1, _, _ = bounds
            for item in group:
                cx1, cy1, cx2, cy2 = context_boxes[item]
                detected_masks[item] = np.ascontiguousarray(
                    shared[cy1 - gy1:cy2 - gy1, cx1 - gx1:cx2 - gx1]
                )
            progress(20 + int(group_number * 45 / max(1, len(detection_groups))))

        def build_fresh_entry(index: int) -> tuple[int, dict | None]:
            x1, y1, x2, y2 = target_boxes[index]
            if x2 <= x1 or y2 <= y1:
                return index, None
            target = {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}
            cx1, cy1, _, _ = context_boxes[index]
            selection = np.zeros(
                (context_boxes[index][3] - cy1, context_boxes[index][2] - cx1),
                dtype=np.uint8,
            )
            selection[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1] = 255
            solid = solid_masks[index]
            if solid is not None:
                fallback_mask, fill_color = solid
                # _solid_balloon_text_mask validates the actual light
                # silhouette and keeps only components anchored inside it.
                # It is safer and more complete than intersecting again with
                # a neural line seed that may omit coloured glyphs.
                final_mask = cv2.bitwise_and(fallback_mask, selection)
                if solid_needs_detector[index]:
                    detected = detected_masks[index]
                    context_rgb = page[
                        cy1:cy1 + selection.shape[0],
                        cx1:cx1 + selection.shape[1],
                    ]
                    guided = (
                        self._solid_text_mask_from_detector(
                            context_rgb, detected, selection, fill_color
                        )
                        if detected is not None else np.zeros_like(selection)
                    )
                    if not np.any(guided) and detected is not None:
                        guided = self._build_text_mask(
                            context_rgb, detected, selection
                        )
                    # For a suspicious geometric mask, doing nothing is safer
                    # than repainting an entire page/balloon wedge.
                    final_mask = guided
                if not np.any(final_mask):
                    return index, None
                method = "solid_fill"
            else:
                detected = detected_masks[index]
                if detected is None:
                    return index, None
                context_rgb = page[
                    cy1:cy1 + detected.shape[0], cx1:cx1 + detected.shape[1]
                ]
                final_mask = self._build_text_mask(
                    context_rgb, detected, selection,
                )
                fill_color = None
                method = "lama"
                # Flat coloured balloons need no generative reconstruction.
                # As in BallonsTranslator, infer the containing contour and
                # fill only the validated text pixels with its median colour.
                # Gradient/transparent balloons fail this strict test and
                # remain assigned to LaMa.
                if np.any(final_mask):
                    inferred_fill = self._flat_balloon_fill_colour(
                        context_rgb, final_mask, selection
                    )
                    if inferred_fill is not None:
                        fill_color = inferred_fill
                        method = "solid_fill"
            if not np.any(final_mask):
                return index, None
            mx, my, width, height = cv2.boundingRect(final_mask)
            built_entry = {
                "x": cx1 + mx, "y": cy1 + my,
                "mask": np.ascontiguousarray(final_mask[my:my + height, mx:mx + width]),
                "target": target,
                "method": method,
                "fill_color": list(fill_color) if fill_color is not None else None,
            }
            return index, built_entry

        # Colour recovery, contour guards and component validation are CPU
        # operations. Build independent boxes concurrently after the single
        # neural batch instead of serialising dozens of OpenCV pipelines.
        built_entries_by_index: dict[int, dict] = {}
        if fresh_indices:
            with ThreadPoolExecutor(
                max_workers=min(workers, max(1, len(fresh_indices)))
            ) as executor:
                for completed, (index, built_entry) in enumerate(
                    executor.map(build_fresh_entry, fresh_indices), start=1,
                ):
                    if cancelled():
                        return {"entries": [], "targets": [], "cached": False}
                    if built_entry is not None:
                        built_entries_by_index[index] = built_entry
                    progress(65 + int(completed * 30 / max(1, len(fresh_indices))))

        targets: list[dict] = []
        entries: list[dict] = []
        for index, (x1, y1, x2, y2) in enumerate(target_boxes):
            if x2 <= x1 or y2 <= y1:
                continue
            targets.append({"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1})
            if index in cached_entries:
                cached_entry = cached_entries[index]
                if cached_entry is not None:
                    entries.append(cached_entry)
            else:
                built_entry = built_entries_by_index.get(index)
                if built_entry is not None:
                    entries.append(built_entry)
            progress(65 + int((index + 1) * 35 / max(1, len(target_boxes))))

        plan = {
            "image_path": str(image_path), "entries": entries, "targets": targets,
            "cached": False, "provider": self.provider_name, "runtime": self.runtime_label,
            "masked_pixels": sum(int(np.count_nonzero(entry["mask"])) for entry in entries),
            "solid_entries": sum(entry.get("method") == "solid_fill" for entry in entries),
        }
        with self._mask_cache_lock:
            for index in fresh_indices:
                entry_key = entry_cache_keys[index]
                stored_entry = self._copy_mask_entry(built_entries_by_index.get(index))
                previous_entry = self._mask_entry_cache.pop(entry_key, None)
                if previous_entry is not None:
                    self._mask_entry_cache_bytes -= self._mask_entry_bytes(previous_entry.get("entry"))
                self._mask_entry_cache[entry_key] = {"entry": stored_entry}
                self._mask_entry_cache_bytes += self._mask_entry_bytes(stored_entry)
                self._mask_entry_cache.move_to_end(entry_key)
            while (
                len(self._mask_entry_cache) > self._mask_entry_cache_limit
                or (
                    self._mask_entry_cache_bytes > self._mask_entry_cache_max_bytes
                    and len(self._mask_entry_cache) > 1
                )
            ):
                _, removed_entry = self._mask_entry_cache.popitem(last=False)
                self._mask_entry_cache_bytes -= self._mask_entry_bytes(removed_entry.get("entry"))
            stored_plan = self._copy_mask_plan(plan)
            previous = self._mask_cache.pop(cache_key, None)
            if previous is not None:
                self._mask_cache_bytes -= self._mask_plan_bytes(previous)
            self._mask_cache[cache_key] = stored_plan
            self._mask_cache_bytes += self._mask_plan_bytes(stored_plan)
            self._mask_cache.move_to_end(cache_key)
            while (
                len(self._mask_cache) > self._mask_cache_limit
                or (self._mask_cache_bytes > self._mask_cache_max_bytes and len(self._mask_cache) > 1)
            ):
                _, removed = self._mask_cache.popitem(last=False)
                self._mask_cache_bytes -= self._mask_plan_bytes(removed)
        progress(100)
        return self._copy_mask_plan(plan)

    @staticmethod
    def _mask_plan_bytes(plan: dict) -> int:
        return sum(int(np.asarray(entry.get("mask", [])).nbytes) for entry in plan.get("entries", []))

    def cache_stats(self) -> dict[str, int]:
        with self._mask_cache_lock:
            return {
                "items": len(self._mask_cache), "bytes": self._mask_cache_bytes,
                "hits": self._mask_cache_hits, "misses": self._mask_cache_misses,
                "entry_items": len(self._mask_entry_cache),
                "entry_bytes": self._mask_entry_cache_bytes,
                "entry_hits": self._mask_entry_cache_hits,
                "entry_misses": self._mask_entry_cache_misses,
                "source_bytes": int(self._source_cache.nbytes) if self._source_cache is not None else 0,
            }

    def clean_prepared(
        self,
        image_path: Path,
        plan: dict,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
        source_states: dict[str, dict] | None = None,
    ) -> dict:
        """Execute LaMa using a reviewed/edited mask plan."""
        page = self._load_source_page(image_path, source_states)
        page_height, page_width = page.shape[:2]
        mask_entries: list[dict] = []
        valid_targets: list[tuple[int, int, int, int, str]] = []
        inpaint_boxes: list[tuple[int, int, int, int]] = []
        lama_entries: list[dict] = []
        processed: list[tuple[int, int, np.ndarray, np.ndarray]] = []
        for entry in plan.get("entries", []):
            mask = np.where(np.asarray(entry["mask"]) > 8, 255, 0).astype(np.uint8)
            if not np.any(mask):
                continue
            x, y = int(entry["x"]), int(entry["y"])
            height, width = mask.shape
            x2, y2 = min(page_width, x + width), min(page_height, y + height)
            mask = np.ascontiguousarray(mask[:y2 - y, :x2 - x])
            if x2 <= x or y2 <= y or not np.any(mask):
                continue
            method = str(entry.get("method", "lama"))
            fill_value = entry.get("fill_color")
            record = {"x": x, "y": y, "mask": mask, "method": method}
            mask_entries.append(record)
            target = entry.get("target", {})
            tx1, ty1 = max(0, int(target.get("x", x))), max(0, int(target.get("y", y)))
            tx2 = min(page_width, tx1 + max(1, int(target.get("width", width))))
            ty2 = min(page_height, ty1 + max(1, int(target.get("height", height))))
            valid_targets.append((tx1, ty1, tx2, ty2, method))
            if method == "solid_fill" and isinstance(fill_value, (list, tuple)) and len(fill_value) >= 3:
                pixels = np.ascontiguousarray(page[y:y2, x:x2].copy())
                colour = np.asarray(fill_value[:3], dtype=np.uint8)
                pixels[mask > 0] = colour
                processed.append((x, y, pixels, mask))
            else:
                lama_entries.append(record)
                inpaint_boxes.append((
                    max(0, x - self.INPAINT_CONTEXT), max(0, y - self.INPAINT_CONTEXT),
                    min(page_width, x2 + self.INPAINT_CONTEXT),
                    min(page_height, y2 + self.INPAINT_CONTEXT),
                ))

        clusters = self._cluster_regions(inpaint_boxes)
        jobs: list[tuple[int, int, tuple[int, int, int, int], np.ndarray, np.ndarray]] = []
        for number, members in enumerate(clusters, start=1):
            bx1 = min(inpaint_boxes[item][0] for item in members)
            by1 = min(inpaint_boxes[item][1] for item in members)
            bx2 = max(inpaint_boxes[item][2] for item in members)
            by2 = max(inpaint_boxes[item][3] for item in members)
            cluster_image = np.ascontiguousarray(page[by1:by2, bx1:bx2])
            cluster_mask = np.zeros((by2 - by1, bx2 - bx1), dtype=np.uint8)
            for item in members:
                source_entry = lama_entries[item]
                mx, my, mask = source_entry["x"], source_entry["y"], source_entry["mask"]
                mh, mw = mask.shape
                target_slice = cluster_mask[my - by1:my - by1 + mh, mx - bx1:mx - bx1 + mw]
                cv2.bitwise_or(target_slice, mask, dst=target_slice)
            x, y, width, height = cv2.boundingRect(cluster_mask)
            context = max(32, int(self.INPAINT_CONTEXT))
            lx1, ly1 = max(0, x - context), max(0, y - context)
            lx2 = min(cluster_mask.shape[1], x + width + context)
            ly2 = min(cluster_mask.shape[0], y + height + context)
            jobs.append((
                bx1, by1, (lx1, ly1, lx2, ly2),
                np.ascontiguousarray(cluster_image[ly1:ly2, lx1:lx2]),
                np.ascontiguousarray(cluster_mask[ly1:ly2, lx1:lx2]),
            ))
            progress(int(number * 10 / max(1, len(clusters))))

        restored_jobs = self._lama_many([(image, mask) for _, _, _, image, mask in jobs])
        initial_cleaned = [
            self._composite_lama(job[3], restored, job[4])
            for job, restored in zip(jobs, restored_jobs)
        ]
        refined_jobs = self._refine_lama_residuals_many([
            (cleaned, job[4], np.full(job[4].shape, 255, dtype=np.uint8))
            for job, cleaned in zip(jobs, initial_cleaned)
        ], max_passes=1)
        residual_passes = 0
        for number, (job, refined) in enumerate(zip(jobs, refined_jobs), start=1):
            if cancelled():
                break
            bx1, by1, bounds, local_image, local_mask = job
            cleaned, passes = refined
            residual_passes += passes
            lx1, ly1, _, _ = bounds
            processed.append((bx1 + lx1, by1 + ly1, cleaned, local_mask))
            progress(10 + int(number * 80 / max(1, len(jobs))))

        patches: list[dict] = []
        for x1, y1, x2, y2, target_method in valid_targets:
            pixels = np.ascontiguousarray(page[y1:y2, x1:x2].copy())
            patch_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
            for px, py, cleaned, exact_mask in processed:
                height, width = cleaned.shape[:2]
                ix1, iy1 = max(x1, px), max(y1, py)
                ix2, iy2 = min(x2, px + width), min(y2, py + height)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                pixels[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1] = cleaned[iy1 - py:iy2 - py, ix1 - px:ix2 - px]
                source_mask = exact_mask[iy1 - py:iy2 - py, ix1 - px:ix2 - px]
                destination_mask = patch_mask[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1]
                np.maximum(destination_mask, source_mask, out=destination_mask)
            if np.any(patch_mask):
                residual_mask = (
                    np.zeros_like(patch_mask)
                    if target_method == "solid_fill"
                    else self._residual_mask(
                        pixels, patch_mask, np.full(patch_mask.shape, 255, dtype=np.uint8),
                    )
                )
                residual_pixels = int(np.count_nonzero(residual_mask))
                patches.append({
                    "x": x1, "y": y1, "pixels": pixels, "mask": patch_mask,
                    "kind": "automatic",
                    "target": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
                    "residual_mask": np.ascontiguousarray(residual_mask),
                    "residual_pixels": residual_pixels,
                    "qc_status": "attention" if residual_pixels >= 4 else "pending",
                })
        progress(100)
        solid_count = sum(
            entry["method"] == "solid_fill" for entry in mask_entries
        )
        runtime = (
            "Relleno sólido · rango de color"
            if solid_count and not lama_entries else self.runtime_label
        )
        return {
            "patches": patches, "targets": [dict(target) for target in plan.get("targets", [])],
            "review_entries": [
                {
                    "x": int(entry["x"]), "y": int(entry["y"]),
                    "mask": np.ascontiguousarray(entry["mask"].copy()),
                    "target": dict(entry.get("target", {})),
                    "method": str(entry.get("method", "lama")),
                    "fill_color": entry.get("fill_color"),
                }
                for entry in plan.get("entries", [])
            ],
            "provider": self.provider_name, "runtime": runtime,
            "masked_pixels": sum(
                int(np.count_nonzero(entry["mask"])) for entry in mask_entries
            ),
            "residual_passes": residual_passes,
            "solid_entries": solid_count,
        }

    def clean(
        self,
        image_path: Path,
        regions: list[dict],
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
        source_states: dict[str, dict] | None = None,
    ) -> dict:
        plan = self.prepare_masks(
            image_path, regions, lambda value: progress(int(value * 0.35)), cancelled, source_states
        )
        if cancelled():
            return {"patches": [], "targets": []}
        return self.clean_prepared(
            image_path, plan,
            lambda value: progress(35 + int(value * 0.65)), cancelled, source_states,
        )
