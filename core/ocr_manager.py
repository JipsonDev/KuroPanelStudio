"""API OCR over regions supplied by the local YOLO detector."""
from __future__ import annotations

import base64
import importlib
import io
import json
import os
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from core.psd_manager import is_psd, load_source_image, state_signature


class _LazyRequests:
    """Preserve the module API while deferring requests' startup cost."""

    def __getattr__(self, name: str):
        return getattr(importlib.import_module("requests"), name)


requests = _LazyRequests()


ALIBABA_COMPATIBLE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
OCR_PROMPT = (
    "Transcribe todo el texto visible, incluido el vertical, coloreado y la puntuación. "
    "Devuelve solo el texto exacto, sin comentarios ni markdown. Une líneas visuales; "
    "conserva párrafos reales y espacios coreanos, sin espacios añadidos en chino/japonés. "
    "Si no hay texto legible, devuelve una cadena vacía."
)
OCR_BATCH_PROMPT = (
    "Recibirás {count} recortes como {count} imágenes independientes y ordenadas. "
    "La primera imagen es el recorte 1, la segunda es el recorte 2 y así sucesivamente. "
    "Transcribe cada imagen por separado: nunca copies ni desplaces el texto de una imagen a otro ID. "
    "Devuelve ÚNICAMENTE un JSON con este formato: {{\"boxes\":[{{\"id\":1,\"text\":\"...\"}}]}} "
    "con IDs del 1 al {count}. Une líneas de maquetación en párrafos reales. "
    "Si un recorte no tiene texto legible, devuelve \"\". Sin comentarios, markdown ni etiquetas internas."
)
OCR_PIPELINE_VERSION = 9

_HAN_KANA = r"\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\u31F0-\u31FF"
_HANGUL = r"\u1100-\u11FF\u3130-\u318F\uA960-\uA97F\uAC00-\uD7AF\uD7B0-\uD7FF"
_HAN_KANA_SPACE = re.compile(rf"(?<=[{_HAN_KANA}])[ \t]+(?=[{_HAN_KANA}])")
_HAN_KANA_SEARCH = re.compile(rf"[{_HAN_KANA}]")
_HANGUL_SEARCH = re.compile(rf"[{_HANGUL}]")
_INTERNAL_BOX_MARKER = re.compile(
    r"(?i)(?:<<<\s*)?\b(?:MSE[\s_-]*)?BOX[\s_-]*\d{1,6}\b(?:\s*>>>)?"
)
_INTERNAL_TOKEN_MARKER = re.compile(
    r"(?i)\b(?:BEGIN|END|START|STOP)\b"
)


class OCRConfigurationError(RuntimeError):
    pass


class OCRNetworkError(RuntimeError):
    """A temporary provider/network failure that can safely be retried."""


class OCRBatchParseError(ValueError):
    pass


class OCRManager:
    """Recognizes the contents of existing regions; it never creates boxes."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self._cache: OrderedDict[tuple, str] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_limit = 1024
        self._cache_hits = 0
        self._retry_lock = threading.Lock()
        self._session = None
        self._session_lock = threading.Lock()
        self._parallelism = 3
        self._latencies: deque[float] = deque(maxlen=8)
        self._latency_lock = threading.Lock()
        self._resource_profile = "balanced"
        # A timed-out POST may already have been billed. Re-read explicitly.
        self._max_attempts = 1
        self._connect_timeout = 6
        self._read_timeout = 38
        self.endpoint = ALIBABA_COMPATIBLE_URL
        self._disk_cache = None
        if cache_path is not None:
            from core.ocr_cache import OCRCache
            import sqlite3
            try:
                self._disk_cache = OCRCache(cache_path)
            except (OSError, sqlite3.Error):
                pass
        self._usage_lock = threading.Lock()
        self._usage = dict(requests=0, reported_responses=0, input_tokens=0, output_tokens=0)

    def usage_stats(self) -> dict[str, int]:
        with self._usage_lock:
            return dict(self._usage)

    def _record_usage(self, response) -> None:
        try:
            usage = response.json().get("usage")
            if not isinstance(usage, dict):
                return
            if not all(isinstance(usage.get(key), int) for key in ("prompt_tokens", "completion_tokens")):
                return
            with self._usage_lock:
                self._usage["reported_responses"] += 1
                self._usage["input_tokens"] += max(0, usage["prompt_tokens"])
                self._usage["output_tokens"] += max(0, usage["completion_tokens"])
        except (ValueError, AttributeError, TypeError):
            pass

    def _store_reading(self, key: tuple, text: str) -> None:
        with self._cache_lock:
            self._cache[key] = text
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)
        if self._disk_cache is not None:
            self._disk_cache.put(key, text)

    @staticmethod
    def needs_recognition(region: dict) -> bool:
        return bool(region.get("ocr_stale")) or not (
            region.get("ocr_checked") or region.get("ocr_completed") or str(region.get("text", "")).strip()
        )

    @staticmethod
    def normalize_endpoint(value: str = "") -> str:
        """Accept the regional base URL or complete URL from Model Studio."""
        value = str(value or ALIBABA_COMPATIBLE_URL).strip().rstrip("/")
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or not host.endswith(".aliyuncs.com")
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.port not in (None, 443) or "{" in value or "}" in value):
            raise OCRConfigurationError(
                "Usa la URL HTTPS de Alibaba Cloud para tu región y espacio de trabajo, "
                "sin claves ni parámetros en la URL."
            )
        path = parts.path.rstrip("/")
        if path in ("", "/compatible-mode/v1"):
            path = "/compatible-mode/v1/chat/completions"
        if path != "/compatible-mode/v1/chat/completions":
            raise OCRConfigurationError("La URL OCR debe terminar en /compatible-mode/v1 o /chat/completions.")
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    def set_endpoint(self, value: str = "") -> None:
        self.endpoint = self.normalize_endpoint(value)

    def _get_session(self):
        if self._session is None:
            with self._session_lock:
                if self._session is None:
                    s = requests.Session()
                    try:
                        from requests.adapters import HTTPAdapter
                        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
                        s.mount("https://", adapter)
                        s.mount("http://", adapter)
                    except Exception:
                        pass
                    self._session = s
        return self._session

    def set_resource_policy(self, policy) -> None:
        self._resource_profile = str(getattr(policy, "name", "balanced"))
        with self._latency_lock:
            self._parallelism = {"low": 2, "balanced": 4, "high": 6}.get(
                self._resource_profile, 4,
            )
            self._latencies.clear()
        self._cache_limit = max(64, int(getattr(policy, "ocr_cache_items", 512)))
        with self._cache_lock:
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)

    def warm_up(self) -> str:
        """Pre-import the HTTP stack without performing a billable OCR request."""
        self._get_session()
        return "API OCR preparada"

    def cache_stats(self) -> dict[str, int]:
        with self._cache_lock:
            return {"items": len(self._cache), "hits": int(getattr(self, "_cache_hits", 0))}

    def trim_cache(self, keep: int = 64) -> None:
        with self._cache_lock:
            target = max(0, int(keep))
            while len(self._cache) > target:
                self._cache.popitem(last=False)

    @property
    def parallelism(self) -> int:
        with self._latency_lock:
            return self._parallelism

    def _observe_latency(self, elapsed: float, failed: bool = False) -> None:
        with self._latency_lock:
            if failed or elapsed >= 25.0:
                self._parallelism = 2
                self._latencies.clear()
                return
            self._latencies.append(float(elapsed))
            if len(self._latencies) >= 4:
                ordered = sorted(self._latencies)
                median = ordered[len(ordered) // 2]
                maximum = {"low": 2, "balanced": 4, "high": 6}.get(self._resource_profile, 4)
                self._parallelism = maximum if median < 9.0 else min(3, maximum)

    def _batch_limits(self) -> tuple[int, int]:
        """Maximum independent images and source pixels per OCR request."""
        if self._resource_profile == "low":
            return 12, 3_800_000
        if self._resource_profile == "high":
            return 28, 7_000_000
        return 24, 5_500_000

    @staticmethod
    def _regions_overlap(first: dict, second: dict) -> bool:
        """Return true when two OCR crops can contain the same glyphs.

        Nested detections are particularly dangerous in a grouped visual OCR
        request: a perfectly valid answer can be attached to the neighbouring
        identifier. Those regions are sent as independent requests instead.
        """
        ax1, ay1 = int(first.get("x", 0)), int(first.get("y", 0))
        bx1, by1 = int(second.get("x", 0)), int(second.get("y", 0))
        ax2 = ax1 + max(0, int(first.get("width", 0)))
        ay2 = ay1 + max(0, int(first.get("height", 0)))
        bx2 = bx1 + max(0, int(second.get("width", 0)))
        by2 = by1 + max(0, int(second.get("height", 0)))
        return min(ax2, bx2) > max(ax1, bx1) and min(ay2, by2) > max(ay1, by1)

    def _partition_batches(self, prepared: list[tuple]) -> list[list[tuple]]:
        max_items, max_pixels = self._batch_limits()
        batches: list[list[tuple]] = []
        current: list[tuple] = []
        current_pixels = 0
        conflicted: set[int] = set()
        for left in range(len(prepared)):
            for right in range(left + 1, len(prepared)):
                if self._regions_overlap(prepared[left][1], prepared[right][1]):
                    conflicted.update((left, right))

        def flush() -> None:
            nonlocal current, current_pixels
            if current:
                batches.append(current)
                current = []
                current_pixels = 0

        for index, item in enumerate(prepared):
            if index in conflicted:
                flush()
                batches.append([item])
                continue
            width, height = item[4]
            item_pixels = max(1, int(width)) * max(1, int(height))
            if current and (len(current) >= max_items or current_pixels + item_pixels > max_pixels):
                flush()
            current.append(item)
            current_pixels += item_pixels
        flush()
        return batches

    @staticmethod
    def _multi_image_content(batch: list[tuple]) -> list[dict]:
        """Build an order-stable request with one API image per OCR region."""
        content: list[dict] = []
        for item in batch:
            encoded = base64.b64encode(item[3]).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded}",
                },
            })
        content.append({
            "type": "text",
            "text": OCR_BATCH_PROMPT.format(count=len(batch)),
        })
        return content

    @staticmethod
    def _contact_sheet(batch: list[tuple], quality: int) -> bytes:
        """Pack compact region crops into one token-efficient OCR image."""
        crops: list[Image.Image] = []
        max_col_width = 720
        for item in batch:
            with Image.open(io.BytesIO(item[3])) as source:
                crop = source.convert("RGB").copy()
                if crop.width > max_col_width:
                    scale = max_col_width / crop.width
                    crop = crop.resize(
                        (max_col_width, max(1, int(round(crop.height * scale)))),
                        Image.Resampling.LANCZOS,
                    )
                crops.append(crop)
        header = 32
        gutter = 10
        separator = 8
        width = max(max(image.width for image in crops) + gutter * 2, 280)
        height = sum(image.height + header + separator for image in crops)
        sheet = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        y = 0
        for slot, image in enumerate(crops, 1):
            draw.rectangle((0, y, width - 1, y + header - 1), fill=(18, 25, 31))
            draw.text((10, y + 8), f"{slot}", fill=(80, 230, 255))
            crop_top = y + header
            paste_x = gutter + max(0, (width - gutter * 2 - image.width) // 2)
            sheet.paste(image, (paste_x, crop_top))
            draw.rectangle(
                (paste_x - 2, crop_top - 2, paste_x + image.width + 1, crop_top + image.height + 1),
                outline=(0, 190, 220), width=2,
            )
            separator_top = crop_top + image.height + 2
            draw.rectangle(
                (0, separator_top, width - 1, separator_top + separator - 2),
                fill=(18, 25, 31),
            )
            y += header + image.height + separator
        stream = io.BytesIO()
        sheet.save(stream, format="JPEG", quality=min(quality, 88), subsampling=0, optimize=False)
        return stream.getvalue()

    @staticmethod
    def _enhanced_retry_payload(payload: bytes) -> bytes:
        """Make faint/coloured glyph edges explicit for one targeted retry."""
        with Image.open(io.BytesIO(payload)) as source:
            image = ImageOps.autocontrast(source.convert("RGB"), cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.22)
        image = image.filter(ImageFilter.UnsharpMask(radius=0.8, percent=105, threshold=3))
        stream = io.BytesIO()
        image.save(stream, format="JPEG", quality=97, subsampling=0, optimize=False)
        return stream.getvalue()

    @staticmethod
    def _likely_contains_foreground(payload: bytes) -> bool:
        try:
            with Image.open(io.BytesIO(payload)) as source:
                preview = source.convert("RGB")
                preview.thumbnail((320, 320))
                pixels = np.asarray(preview, dtype=np.int16)
        except (OSError, ValueError):
            return False
        if pixels.size == 0:
            return False
        background = np.percentile(pixels.reshape(-1, 3), 75, axis=0)
        distance = np.linalg.norm(pixels - background.reshape(1, 1, 3), axis=2)
        return int(np.count_nonzero(distance > 18.0)) >= max(8, int(distance.size * 0.001))

    @staticmethod
    def _normalize_batch_text(value: str, identifier: int) -> str:
        text = OCRManager.normalize_cjk_text(value)
        if not text:
            return ""
        compact = re.sub(r"[\s:：#\[\]\(\)（）._-]+", "", text).casefold()
        numeric = str(int(identifier))
        padded = f"{int(identifier):03d}"
        marker_values = {
            numeric,
            padded,
            f"box{numeric}",
            f"box{padded}",
            f"msebox{numeric}",
            f"msebox{padded}",
            f"cell{numeric}",
            f"cell{padded}",
        }
        # A label echoed by the vision model is transport noise. Only remove it
        # when it is the whole answer; real dialogue that merely contains a
        # number remains untouched.
        return "" if compact in marker_values else text

    @staticmethod
    def _batch_response_partial(response, expected: int) -> list[str | None]:
        content = OCRManager._response_content(response).strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            start, end = content.index("{"), content.rindex("}") + 1
            decoded = json.loads(content[start:end])
            entries = decoded.get("boxes", []) if isinstance(decoded, dict) else []
            mapped = {}
            duplicates: set[int] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                identifier = int(entry.get("id", 0))
                if 1 <= identifier <= expected:
                    if identifier in mapped:
                        duplicates.add(identifier)
                    mapped[identifier] = OCRManager._normalize_batch_text(
                        str(entry.get("text", "")), identifier
                    )
            for identifier in duplicates:
                mapped[identifier] = None
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise OCRBatchParseError("La respuesta OCR agrupada no conservó el formato.") from error
        return [mapped.get(index) for index in range(1, expected + 1)]

    @staticmethod
    def _batch_response(response, expected: int) -> list[str]:
        values = OCRManager._batch_response_partial(response, expected)
        if any(value is None for value in values):
            raise OCRBatchParseError("La respuesta OCR agrupada omitió una o más cajas.")
        return [str(value) for value in values]

    def validate(self, provider: str, configured_key: str = "") -> str:
        key = str(configured_key or os.environ.get("DASHSCOPE_API_KEY", "")).strip()
        if provider != "Alibaba Cloud":
            raise OCRConfigurationError(f"El adaptador de {provider} aún no está instalado.")
        if not key:
            raise OCRConfigurationError("Agrega tu API key de Alibaba Cloud en Configuración.")
        return key

    def run_regions(
        self,
        image_path: Path,
        regions: list[dict],
        provider: str,
        model: str,
        configured_key: str,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
        source_states: dict[str, dict] | None = None,
        force_refresh: bool = False,
    ) -> list[dict]:
        """OCR each YOLO region separately and preserve its canvas geometry."""
        api_key = self.validate(provider, configured_key)
        if not regions:
            raise ValueError("Primero ejecuta Autodetectar texto (YOLO).")
        requested_model = str(model).strip()
        if not requested_model:
            raise OCRConfigurationError("Escribe el nombre del modelo OCR que deseas utilizar.")
        # Alibaba aliases are valid model names. Respect exactly what the user
        # selected instead of silently replacing it with a dated release.
        qwen_model = requested_model
        endpoint = self.endpoint
        stat = image_path.stat()
        cache_prefix = (
            str(image_path.resolve()).casefold(), stat.st_mtime_ns, stat.st_size,
            provider, endpoint, qwen_model, OCR_PIPELINE_VERSION, state_signature(source_states),
            self._resource_profile,
        )
        pending: list[tuple[int, dict, tuple]] = []
        results: list[dict | None] = [None] * len(regions)
        keys = [(*cache_prefix, *(int(region[k]) for k in ("x", "y", "width", "height"))) for region in regions]
        disk_readings = self._disk_cache.get_many(keys) if self._disk_cache and not force_refresh else {}
        duplicates: dict[tuple, list[int]] = {}
        # Consult the cache before decoding a potentially very tall PNG or PSD.
        for index, region in enumerate(regions):
            if cancelled():
                return []
            cache_key = keys[index]
            cached = None
            if not force_refresh:
                with self._cache_lock:
                    cached = self._cache.get(cache_key, disk_readings.get(cache_key))
                    if cached is not None:
                        self._cache[cache_key] = cached
                        self._cache.move_to_end(cache_key)
                        while len(self._cache) > self._cache_limit:
                            self._cache.popitem(last=False)
                        self._cache_hits += 1
            if cached is not None:
                results[index] = {**region, "text": cached}
            else:
                if cache_key not in duplicates:
                    pending.append((index, region, cache_key))
                    duplicates[cache_key] = []
                duplicates[cache_key].append(index)

        completed = sum(result is not None for result in results)
        progress(int(completed * 100 / len(regions)))
        if not pending:
            return [result for result in results if result is not None]

        def recognize(payload: bytes) -> str | None:
            if cancelled():
                return None
            encoded = base64.b64encode(payload).decode("ascii")
            started = time.perf_counter()
            last_error: Exception | None = None
            for attempt in range(self._max_attempts):
                if cancelled():
                    return None
                try:
                    def request():
                        session = self._get_session()
                        with self._usage_lock:
                            self._usage["requests"] += 1
                        response = session.post(
                            endpoint,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": qwen_model,
                                "messages": [{
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": OCR_PROMPT},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/jpeg;base64,{encoded}",
                                            },
                                        },
                                    ],
                                }],
                            },
                            timeout=(self._connect_timeout, self._read_timeout),
                            allow_redirects=False,
                        )
                        self._record_usage(response)
                        try:
                            return self._response_text(response)
                        except OCRConfigurationError as error:
                            raise OCRConfigurationError(
                                f"{error}\nModelo: {qwen_model}\nServidor: {urlsplit(endpoint).hostname}"
                            ) from error

                    if attempt:
                        with self._retry_lock:
                            text = request()
                    else:
                        text = request()
                    self._observe_latency(time.perf_counter() - started)
                    return text
                except Exception as error:
                    if not self._is_transient_error(error):
                        self._observe_latency(time.perf_counter() - started, failed=True)
                        raise
                    last_error = error
                    if attempt + 1 < self._max_attempts:
                        time.sleep(0.35)
            self._observe_latency(time.perf_counter() - started, failed=True)
            detail = "10054" if self._has_winerror(last_error, 10054) else "conexión temporal"
            raise OCRNetworkError(
                f"Alibaba Cloud interrumpió la {detail}. Intentos: {self._max_attempts}. "
                "No se repite automáticamente para evitar posibles cargos duplicados. "
                "Revisa tu conexión y vuelve a ejecutar OCR; las cajas completadas quedaron en caché."
            ) from last_error

        # Qwen-VL can reorder the answers of a multi-image request even when
        # the response contains syntactically valid IDs. That silently swaps
        # dialogue between distant balloons and cannot be detected by parsing
        # the JSON. Keep one crop per request and recover speed through bounded
        # concurrency, exactly preserving future -> source-region ownership.
        def recognize_item(item: tuple) -> str:
            payload = self._enhanced_retry_payload(item[3]) if force_refresh else item[3]
            text = recognize(payload)
            if text is not None:
                # Persist inside the worker: another concurrent request may
                # fail before the coordinator consumes this successful result.
                self._store_reading(item[2], text)
            return text or ""

        # Keep only a bounded window of JPEGs/futures alive. Preparing the next
        # crop overlaps HTTP work; results always belong to their source index.
        workers = min(self.parallelism, len(pending))
        with ExitStack() as stack:
            source_page = load_source_image(image_path, source_states) if is_psd(image_path) else Image.open(image_path)
            page = stack.enter_context(source_page)
            page.load()
            executor = stack.enter_context(ThreadPoolExecutor(max_workers=workers))
            futures = {}
            next_index = 0
            try:
                while next_index < len(pending) or futures:
                    if cancelled():
                        return []
                    limit = min(workers, self.parallelism)
                    while next_index < len(pending) and len(futures) < limit:
                        if cancelled():
                            return []
                        index, region, cache_key = pending[next_index]
                        payload, size = self._prepare_crop(page, region)
                        item = (index, region, cache_key, payload, size)
                        futures[executor.submit(recognize_item, item)] = item
                        next_index += 1
                    done, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                    # Drain successes before raising an error, so completed boxes
                    # remain reusable on the user's next attempt.
                    failure = None
                    for future in done:
                        item = futures.pop(future)
                        try:
                            text = future.result()
                        except Exception as error:
                            failure = error
                            continue
                        if cancelled():
                            return []
                        cache_key = item[2]
                        for index in duplicates[cache_key]:
                            results[index] = {**regions[index], "text": text}
                        completed += len(duplicates[cache_key])
                        progress(int(completed * 100 / len(regions)))
                    if failure is not None:
                        raise failure
            finally:
                for future in futures:
                    future.cancel()
        return [result for result in results if result is not None]

    def _prepare_crop(self, page: Image.Image, region: dict) -> tuple[bytes, tuple[int, int]]:
        """Encode one crop only when a request slot is available."""
        x, y, width, height = (int(region[k]) for k in ("x", "y", "width", "height"))
        if width <= 0 or height <= 0 or x >= page.width or y >= page.height or x + width <= 0 or y + height <= 0:
            raise ValueError("La caja OCR debe tener área y estar dentro de la imagen.")
        # Tight YOLO boxes can cut punctuation and antialiased edge
        # strokes. A small bounded context improves black, coloured
        # and CJK recognition without uploading the complete balloon.
        padding = max(6, min(24, int(round(min(width, height) * 0.06))))
        left, top = max(0, x - padding), max(0, y - padding)
        right, bottom = min(page.width, x + width + padding), min(page.height, y + height + padding)
        crop = page.crop((left, top, right, bottom)).convert("RGB")
        # Fine dialogue on a 60k-pixel strip may be only a few pixels
        # high after detection. Upscale small crops before JPEG so the
        # remote vision encoder receives complete glyph strokes.
        shortest = min(crop.width, crop.height)
        if shortest < 128:
            scale = min(2.0, 128.0 / max(1, shortest))
            crop = crop.resize(
                (max(1, int(round(crop.width * scale))), max(1, int(round(crop.height * scale)))),
                Image.Resampling.LANCZOS,
            )
        # Large user boxes waste upload and remote vision tokens. Keep
        # enough pixels for glyph detail without sending multi-megapixel
        # balloon backgrounds to the API.
        max_side = 640 if self._resource_profile == "low" else (840 if self._resource_profile == "high" else 720)
        max_pixels = 240_000 if self._resource_profile == "low" else (480_000 if self._resource_profile == "high" else 320_000)
        if crop.width > max_side or crop.height > max_side or crop.width * crop.height > max_pixels:
            scale = min(max_side / crop.width, max_side / crop.height, (max_pixels / (crop.width * crop.height)) ** 0.5)
            crop = crop.resize(
                (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )
        # Thin selections can become 1–10 pixels wide after downscaling.
        # Pad instead of stretching glyphs; the provider chooses its own
        # model-specific min/max pixel defaults (which differ across Qwen).
        if min(crop.size) < 32:
            crop = ImageOps.pad(crop, (max(32, crop.width), max(32, crop.height)),
                                color="white", method=Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        crop.save(
            stream, format="JPEG", quality=86 if self._resource_profile == "low" else 90,
            subsampling=0,
        )
        return stream.getvalue(), crop.size

    @staticmethod
    def _response_content(response) -> str:
        status = getattr(response, "status_code", 200)
        if status not in (None, 200):
            detail = ""
            try:
                payload = response.json()
                api_error = payload.get("error", {}) if isinstance(payload, dict) else {}
                detail = str(
                    api_error.get("message", payload.get("message", "")) if isinstance(api_error, dict) else api_error
                ).strip()
            except (ValueError, AttributeError):
                pass
            lower = detail.casefold()
            if status == 402 or "insufficient balance" in lower:
                message = "Alibaba Cloud: Saldo insuficiente. Recarga saldo en tu cuenta de Alibaba Cloud Model Studio."
            elif status == 403 and "free quota" in lower:
                message = (
                    "Alibaba Cloud: La cuota gratuita de este modelo se ha agotado. "
                    "Revisa la cuota y la opción «usar solo cuota gratuita» en Model Studio. "
                    "La aplicación no puede ampliar esa cuota."
                )
            elif status in (401, 403) or "invalid_api_key" in lower or "incorrect api key" in lower:
                message = f"Alibaba Cloud (HTTP {status}): clave no válida o sin permisos para esta región/modelo. Revisa la clave y la URL OCR en Configuración → OCR y traducción."
            elif status == 404 or "model_not_found" in lower:
                message = (
                    f"Alibaba Cloud (HTTP {status}): modelo o ruta no disponible. "
                    "Comprueba el identificador exacto del modelo y la URL de tu región/espacio "
                    "en Configuración → OCR y traducción; la clave debe pertenecer a esa región."
                )
            elif status == 429:
                message = "Alibaba Cloud: Límite de solicitudes alcanzado (Rate Limit). Intenta de nuevo en unos segundos."
            else:
                message = f"Alibaba Cloud respondió HTTP {status}"
                if detail:
                    message += f": {detail}"
            if int(status) == 429 or int(status) >= 500:
                raise OCRNetworkError(message)
            raise OCRConfigurationError(message)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
            return str(content or "")
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
            raise OCRConfigurationError("La API OCR devolvió una respuesta sin texto reconocible.") from error

    @staticmethod
    def _response_text(response) -> str:
        return OCRManager.normalize_cjk_text(OCRManager._response_content(response))

    @staticmethod
    def normalize_cjk_text(value: str) -> str:
        """Remove visual line wrapping and residual transport labels without destroying real CJK spacing."""
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        # Remove markdown code blocks if the model wrapped the text
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        # Remove common model hallucinations and structural tokens
        text = re.sub(r"(?i)<\|.*?\|>", "", text)
        text = re.sub(r"(?i)<\s*/?\s*(?:s_box|box|begin|end|start|stop)[^>]*>", "", text)
        text = _INTERNAL_BOX_MARKER.sub("", text)
        text = re.sub(r"(?im)^[ \t]*(?:BEGIN|END|START|STOP)[ \t]*:?[ \t]*$", "", text)

        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return ""
        text = _HAN_KANA_SPACE.sub("", text)
        paragraphs: list[str] = []
        for paragraph in re.split(r"\n[ \t]*\n+", text):
            lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
            if not lines:
                continue
            joined = lines[0]
            for following in lines[1:]:
                if _HAN_KANA_SEARCH.search(joined) or _HAN_KANA_SEARCH.search(following):
                    joined += following
                elif _HANGUL_SEARCH.search(joined) or _HANGUL_SEARCH.search(following):
                    # Korean words use meaningful spaces; a visual line break
                    # normally replaces one of those spaces.
                    joined = f"{joined.rstrip()} {following.lstrip()}"
                else:
                    joined += "\n" + following
            paragraphs.append(joined)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _has_winerror(error: Exception | None, code: int) -> bool:
        visited: set[int] = set()
        current = error
        while isinstance(current, BaseException) and id(current) not in visited:
            visited.add(id(current))
            if getattr(current, "winerror", None) == code or getattr(current, "errno", None) == code:
                return True
            if str(code) in str(current):
                return True
            current = current.__cause__ or current.__context__
        return False

    @classmethod
    def _is_transient_error(cls, error: Exception) -> bool:
        if isinstance(error, (OCRNetworkError, ConnectionError, TimeoutError)):
            return True
        try:
            if isinstance(error, requests.RequestException):
                return True
        except ImportError:
            pass
        if isinstance(error, OSError) and getattr(error, "winerror", None) in {10053, 10054, 10060}:
            return True
        message = str(error).casefold()
        return any(fragment in message for fragment in (
            "10053", "10054", "10060", "connection aborted", "connection reset",
            "remote host", "timed out", "timeout", "temporarily unavailable",
            "too many requests", "http 429", "http 500", "http 502", "http 503", "http 504",
        ))
