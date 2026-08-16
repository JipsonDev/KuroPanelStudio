"""Tiled, lazy YOLO inference for very long manhua pages."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from collections.abc import Callable
import os
import threading
import gc
from time import monotonic

import numpy as np
from PIL import Image

from core.model_paths import ModelPaths
from core.performance_manager import (
    adaptive_gpu_batch, cuda_memory_mb, normalize_device_mode,
    preload_onnx_cuda, register_cuda_dll_directories,
)
from core.psd_manager import load_source_image, state_signature


class _OnnxTextDetector:
    """Batched YOLO12 runtime using the same weights as the reference editor.

    The source model is ``yolo12s_animetext.pt``.  Its ONNX export keeps the
    executable small and, unlike the old wrapper, accepts several page strips
    in one GPU call.  Input arrays here originate in PIL and are already RGB;
    reversing them produced BGR tensors and missed coloured text.
    """

    def __init__(self, path: Path, device_mode: str, threads: int) -> None:
        import cv2
        import onnxruntime as ort

        capi = Path(ort.__file__).resolve().parent / "capi"
        register_cuda_dll_directories(capi)
        # Registering a directory is not enough on Windows: ORT may create the
        # session before delayed cuBLAS/cuDNN dependencies are resolved and
        # silently fall back to CPU. Preload them before choosing providers.
        preload_onnx_cuda(ort, capi)
        available = set(ort.get_available_providers())
        use_cuda = device_mode != "cpu" and "CUDAExecutionProvider" in available
        use_dml = device_mode != "cpu" and "DmlExecutionProvider" in available
        if device_mode == "gpu" and not (use_cuda or use_dml):
            raise RuntimeError("El modo GPU está activo, pero ni CUDA ni DirectML están disponibles para YOLO ONNX.")
        cuda_opt = (
            "CUDAExecutionProvider",
            {
                "device_id": 0,
                "arena_extend_strategy": "kSameAsRequested",
                "cudnn_conv_algo_search": "HEURISTIC",
                "do_copy_in_default_stream": True,
            },
        )
        dml_opt = ("DmlExecutionProvider", {"device_id": 0, "enable_dynamic_graph_fusion": "1"})
        providers = (
            [cuda_opt, "CPUExecutionProvider"]
            if use_cuda
            else [dml_opt, "CPUExecutionProvider"]
            if use_dml
            else ["CPUExecutionProvider"]
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        # Ultralytics' default inference size used by the reference editor.
        self.size = 640
        self.cv2 = cv2
        self.uses_cuda = self.session.get_providers()[0] in {"CUDAExecutionProvider", "DmlExecutionProvider"}

    def predict_boxes(self, images: list[np.ndarray], confidence: float, iou: float) -> list[list[tuple[np.ndarray, float]]]:
        if not images:
            return []
        tensors: list[np.ndarray] = []
        transforms: list[tuple[int, int, float, int, int]] = []
        for image in images:
            height, width = image.shape[:2]
            scale = min(self.size / max(1, width), self.size / max(1, height))
            resized_w, resized_h = max(1, round(width * scale)), max(1, round(height * scale))
            resized = self.cv2.resize(image, (resized_w, resized_h), interpolation=self.cv2.INTER_LINEAR)
            left = (self.size - resized_w) // 2
            top = (self.size - resized_h) // 2
            padded = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
            padded[top:top + resized_h, left:left + resized_w] = resized
            tensors.append(np.transpose(padded, (2, 0, 1)))
            transforms.append((height, width, scale, left, top))

        tensor = np.ascontiguousarray(np.stack(tensors), dtype=np.float32)
        tensor *= 1.0 / 255.0
        raw_batch = np.asarray(self.session.run(None, {self.input_name: tensor})[0])
        outputs: list[list[tuple[np.ndarray, float]]] = []
        for prediction, (height, width, scale, left, top) in zip(raw_batch, transforms):
            if prediction.shape[0] <= 8:
                prediction = prediction.T
            scores = prediction[:, 4]
            keep = scores >= float(confidence)
            prediction, scores = prediction[keep], scores[keep]
            if not len(prediction):
                outputs.append([])
                continue
            xywh = prediction[:, :4]
            boxes = np.column_stack((
                xywh[:, 0] - xywh[:, 2] / 2,
                xywh[:, 1] - xywh[:, 3] / 2,
                xywh[:, 0] + xywh[:, 2] / 2,
                xywh[:, 1] + xywh[:, 3] / 2,
            ))
            selected = self._nms(boxes, scores, iou)
            detected = []
            for index in selected:
                box = boxes[index].copy()
                box[[0, 2]] = (box[[0, 2]] - left) / scale
                box[[1, 3]] = (box[[1, 3]] - top) / scale
                box[[0, 2]] = np.clip(box[[0, 2]], 0, width)
                box[[1, 3]] = np.clip(box[[1, 3]], 0, height)
                detected.append((box, float(scores[index])))
            outputs.append(detected)
        return outputs

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
        order = scores.argsort()[::-1]
        selected: list[int] = []
        areas = np.maximum(0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0, boxes[:, 3] - boxes[:, 1])
        while order.size:
            current = int(order[0]); selected.append(current)
            if order.size == 1:
                break
            remaining = order[1:]
            xx1 = np.maximum(boxes[current, 0], boxes[remaining, 0])
            yy1 = np.maximum(boxes[current, 1], boxes[remaining, 1])
            xx2 = np.minimum(boxes[current, 2], boxes[remaining, 2])
            yy2 = np.minimum(boxes[current, 3], boxes[remaining, 3])
            intersection = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            union = areas[current] + areas[remaining] - intersection
            order = remaining[np.where(intersection / np.maximum(union, 1e-6) <= threshold)[0]]
        return selected


@dataclass
class TextRegion:
    x: int
    y: int
    width: int
    height: int
    confidence: float
    label: str = "text"

    def to_dict(self) -> dict:
        return asdict(self)


class DetectionManager:
    """Loads YOLO only when it is first used and processes pages in strips."""

    def __init__(self, models_root: Path, confidence: float = 0.25, tile_height: int = 2000, tile_overlap: int = 300) -> None:
        self.paths = ModelPaths(models_root)
        self.confidence = confidence
        self.tile_height = tile_height
        self.tile_overlap = tile_overlap
        self._model = None
        self._model_lock = threading.RLock()
        self._device = "cpu"
        self.runtime_label = "CPU"
        self._warmed = False
        self.device_mode = "auto"
        self._loaded_mode = ""
        self.last_used = monotonic()
        self.resource_profile = "balanced"
        self.cpu_threads = max(1, min(3, (os.cpu_count() or 2) // 2))
        self._result_cache: dict[tuple, tuple[dict, ...]] = {}
        self._cache_limit = 32

    def set_resource_policy(self, policy) -> None:
        self.resource_profile = str(getattr(policy, "name", "balanced"))
        self.cpu_threads = max(1, int(getattr(policy, "onnx_threads", self.cpu_threads)))
        # Keep the geometry used by the trained model stable on every machine.
        # Resource profiles vary batch size/threads, not detector semantics.
        self.tile_height, self.tile_overlap = 2000, 300

    def set_device_mode(self, mode: str) -> None:
        normalized = normalize_device_mode(mode)
        if normalized != self.device_mode:
            self.device_mode = normalized
            self._warmed = False

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> bool:
        """Release YOLO weights and CUDA allocations while retaining results."""
        with self._model_lock:
            had_model = self._model is not None
            self._model = None
            self._warmed = False
            self._loaded_mode = ""
        if had_model:
            gc.collect()
            try:
                import torch
                if hasattr(torch, "cuda") and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except (ImportError, RuntimeError, AttributeError):
                pass
        self.runtime_label = "Modelo descargado"
        return had_model

    def clear_cache(self) -> None:
        """Forget detections while retaining the already loaded network."""
        self._result_cache.clear()

    def _load_model(self):
        if self._model is not None and self._loaded_mode != self.device_mode:
            self.unload()
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    if self.paths.yolo_onnx.is_file():
                        self._model = _OnnxTextDetector(
                            self.paths.yolo_onnx, self.device_mode, self.cpu_threads,
                        )
                        self._device = "cuda" if self._model.uses_cuda else "cpu"
                        self.runtime_label = "GPU · ONNX CUDA" if self._model.uses_cuda else f"CPU · ONNX · {self.cpu_threads} núcleos"
                        self._loaded_mode = self.device_mode
                        self.last_used = monotonic()
                        return self._model
                    import torch
                    from ultralytics import YOLO
                    cuda_available = bool(torch.cuda.is_available())
                    if self.device_mode == "gpu" and not cuda_available:
                        raise RuntimeError("El modo GPU está activo, pero CUDA no está disponible para YOLO.")
                    if self.device_mode != "cpu" and cuda_available:
                        self._device = "0"
                        self.runtime_label = f"GPU · {torch.cuda.get_device_name(0)}"
                    else:
                        physical_cores = max(1, (os.cpu_count() or 2) // 2)
                        try:
                            import psutil
                            physical_cores = psutil.cpu_count(logical=False) or physical_cores
                        except ImportError:
                            pass
                        physical_cores = max(1, min(physical_cores, self.cpu_threads))
                        torch.set_num_threads(physical_cores)
                        self._device = "cpu"
                        self.runtime_label = f"CPU · {physical_cores} núcleos"
                    self._model = YOLO(str(self.paths.validate("yolo")))
                    self._loaded_mode = self.device_mode
                    self.last_used = monotonic()
        return self._model

    def warm_up(
        self,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        progress = progress or (lambda _value: None)
        cancelled = cancelled or (lambda: False)
        if self._warmed:
            progress(100)
            return self.runtime_label
        progress(10)
        model = self._load_model()
        progress(55)
        if cancelled():
            return self.runtime_label
        # Ultralytics/PyTorch initialize CUDA kernels on the first prediction,
        # not while reading weights. Pay that cost during background warm-up.
        if isinstance(model, _OnnxTextDetector):
            model.predict_boxes([np.zeros((640, 640, 3), dtype=np.uint8)], self.confidence, 0.70)
        else:
            model.predict(
                np.zeros((640, 640, 3), dtype=np.uint8),
                conf=self.confidence,
                verbose=False,
                device=self._device,
                half=self._device != "cpu",
            )
        self._warmed = True
        self.last_used = monotonic()
        progress(100)
        return self.runtime_label

    def detect(
        self, image_path: Path, progress: Callable[[int], None], cancelled: Callable[[], bool],
        source_states: dict[str, dict] | None = None,
    ) -> list[dict]:
        if not image_path.exists():
            raise FileNotFoundError("La imagen activa ya no existe.")
        stat = image_path.stat()
        cache_key = (
            str(image_path.resolve()).casefold(), int(stat.st_mtime_ns), int(stat.st_size),
            state_signature(source_states), round(float(self.confidence), 4),
            int(self.tile_height), int(self.tile_overlap),
        )
        cached = self._result_cache.get(cache_key)
        if cached is not None:
            self.last_used = monotonic()
            progress(100)
            return [dict(region) for region in cached]
        model = self._load_model()
        regions: list[TextRegion] = []
        with load_source_image(image_path, source_states) as image:
            width, height = image.size
            stride = max(1, self.tile_height - self.tile_overlap)
            starts = list(range(0, height, stride))
            if starts and starts[-1] + self.tile_height < height:
                starts.append(max(0, height - self.tile_height))
            starts = list(dict.fromkeys(starts))
            memory = cuda_memory_mb() if self._device != "cpu" else None
            if isinstance(model, _OnnxTextDetector):
                if memory:
                    batch_limit = adaptive_gpu_batch(memory[0])[0]
                else:
                    batch_limit = {"low": 1, "balanced": 2, "high": 4}.get(self.resource_profile, 2)
            else:
                batch_limit = adaptive_gpu_batch(memory[0] if memory else None)[0] if memory else 1
            for offset in range(0, len(starts), batch_limit):
                if cancelled():
                    return []
                batch_starts = starts[offset:offset + batch_limit]
                strips = [
                    np.asarray(image.crop((0, top, width, min(height, top + self.tile_height))))
                    for top in batch_starts
                ]
                if isinstance(model, _OnnxTextDetector):
                    # Ultralytics first performs per-tile NMS at its default
                    # IoU 0.70.  Cross-tile suppression is applied below.
                    predictions = model.predict_boxes(strips, self.confidence, 0.70)
                    for top, boxes in zip(batch_starts, predictions):
                        for box, score in boxes:
                            x1, y1, x2, y2 = (int(value) for value in box)
                            regions.append(TextRegion(x1, y1 + top, x2 - x1, y2 - y1, float(score)))
                else:
                    predictions = model.predict(
                        strips if len(strips) > 1 else strips[0],
                        conf=self.confidence,
                        iou=0.35,
                        verbose=False,
                        device=self._device,
                        half=self._device != "cpu",
                    )
                    for top, result in zip(batch_starts, predictions):
                        if result.boxes is not None:
                            for box, score in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy()):
                                x1, y1, x2, y2 = (int(value) for value in box)
                                regions.append(TextRegion(x1, y1 + top, x2 - x1, y2 - y1, float(score)))
                progress(int(min(len(starts), offset + len(batch_starts)) * 100 / len(starts)))
        self.last_used = monotonic()
        # Match the reference workflow: discard accidental page-sized boxes,
        # suppress duplicates from overlapping strips, then join text lines.
        page_area = max(1, width * height)
        regions = [item for item in regions if (item.width * item.height) / page_area <= 0.15]
        regions = self._deduplicate(regions)
        regions = self._merge_same_balloon_lines(regions)
        result = tuple(region.to_dict() for region in regions)
        self._result_cache[cache_key] = result
        while len(self._result_cache) > self._cache_limit:
            self._result_cache.pop(next(iter(self._result_cache)))
        return [dict(region) for region in result]

    @staticmethod
    def _deduplicate(regions: list[TextRegion]) -> list[TextRegion]:
        """Reference NMS: IoU 0.35 or 75% containment of the weaker box."""
        accepted: list[TextRegion] = []
        for candidate in sorted(regions, key=lambda item: item.confidence, reverse=True):
            candidate_area = candidate.width * candidate.height
            duplicate = False
            for current in accepted:
                overlap_x = max(0, min(candidate.x + candidate.width, current.x + current.width) - max(candidate.x, current.x))
                overlap_y = max(0, min(candidate.y + candidate.height, current.y + current.height) - max(candidate.y, current.y))
                intersection = overlap_x * overlap_y
                union = candidate_area + current.width * current.height - intersection
                containment = intersection / max(1, candidate_area)
                if (union and intersection / union > 0.35) or containment > 0.75:
                    duplicate = True
                    break
            if not duplicate:
                accepted.append(candidate)
        return sorted(accepted, key=lambda item: (item.y, item.x))

    @staticmethod
    def _merge_same_balloon_lines(regions: list[TextRegion]) -> list[TextRegion]:
        """Join adjacent detected lines only when they clearly form one balloon.

        YOLO sometimes returns one box per line inside a bubble. The limits are
        deliberately conservative so nearby but independent bubbles stay apart.
        """
        regions = list(regions)
        changed = True
        while changed:
            changed = False
            regions.sort(key=lambda item: (item.y, item.x))
            for first_index, first in enumerate(regions):
                for second_index in range(first_index + 1, len(regions)):
                    second = regions[second_index]
                    if second.y > first.y + first.height + 40:
                        break
                    if not DetectionManager._are_adjacent_lines(first, second):
                        continue
                    x1 = min(first.x, second.x)
                    y1 = min(first.y, second.y)
                    x2 = max(first.x + first.width, second.x + second.width)
                    y2 = max(first.y + first.height, second.y + second.height)
                    merged = TextRegion(x1, y1, x2 - x1, y2 - y1, max(first.confidence, second.confidence), "text")
                    regions.pop(second_index)
                    regions.pop(first_index)
                    regions.append(merged)
                    changed = True
                    break
                if changed:
                    break
        return sorted(regions, key=lambda item: (item.y, item.x))

    @staticmethod
    def _are_adjacent_lines(first: TextRegion, second: TextRegion) -> bool:
        horizontal_overlap = max(0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x))
        overlap_ratio = horizontal_overlap / max(1, min(first.width, second.width))
        vertical_gap = max(0, max(first.y, second.y) - min(first.y + first.height, second.y + second.height))
        return overlap_ratio >= 0.25 and vertical_gap <= 5
