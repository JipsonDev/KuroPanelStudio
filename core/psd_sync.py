"""Helpers for safely reloading PSD/PSB files written by Photoshop."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from time import sleep

from PIL import Image

from core.psd_manager import inspect_psd


def file_signature(path: Path) -> tuple[int, int] | None:
    """Return a cheap change signature, or ``None`` while a file is absent."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def inspect_stable_psd(
    path: Path, attempts: int = 10, interval: float = 0.18,
) -> dict:
    """Wait until Photoshop has finished replacing/writing a PSD, then inspect it.

    Photoshop can emit several directory notifications during one save and
    may temporarily lock or replace the source file. This runs in an I/O
    worker, never in the Qt thread.
    """
    previous: tuple[int, int] | None = None
    stable_reads = 0
    last_error: Exception | None = None
    for _ in range(max(2, int(attempts))):
        signature = file_signature(path)
        if signature is not None and signature == previous and signature[1] > 0:
            stable_reads += 1
        else:
            stable_reads = 0
        previous = signature
        if stable_reads >= 1:
            try:
                width, height, layers = inspect_psd(path)
                return {
                    "path": path, "signature": signature,
                    "width": width, "height": height, "layers": layers,
                    "size_label": f"{signature[1] / 1024 / 1024:.1f} MB",
                }
            except (OSError, PermissionError, ValueError) as error:
                last_error = error
        sleep(max(0.02, float(interval)))
    if last_error is not None:
        raise RuntimeError(f"Photoshop todavía está escribiendo {path.name}: {last_error}")
    raise RuntimeError(f"No se pudo leer una versión estable de {path.name}.")


def inspect_stable_raster(
    path: Path, attempts: int = 10, interval: float = 0.18,
) -> dict:
    """Wait for Photoshop to finish replacing a PNG/JPEG, then read metadata.

    Accessing ``Image.size`` only parses the image header, so even a 60k-pixel
    webtoon strip can be checked without decoding the complete page twice.
    """
    previous: tuple[int, int] | None = None
    stable_reads = 0
    last_error: Exception | None = None
    for _ in range(max(2, int(attempts))):
        signature = file_signature(path)
        if signature is not None and signature == previous and signature[1] > 0:
            stable_reads += 1
        else:
            stable_reads = 0
        previous = signature
        if stable_reads >= 1:
            try:
                with Image.open(path) as image:
                    width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError("dimensiones inválidas")
                return {
                    "path": path, "signature": signature,
                    "width": int(width), "height": int(height), "layers": None,
                    "size_label": f"{signature[1] / 1024 / 1024:.1f} MB",
                }
            except (OSError, PermissionError, ValueError) as error:
                last_error = error
        sleep(max(0.02, float(interval)))
    if last_error is not None:
        raise RuntimeError(f"Photoshop todavía está escribiendo {path.name}: {last_error}")
    raise RuntimeError(f"No se pudo leer una versión estable de {path.name}.")


def inspect_stable_source(
    path: Path, attempts: int = 10, interval: float = 0.18,
) -> dict:
    """Inspect either a layered Photoshop file or a regular raster image."""
    if path.suffix.casefold() in {".psd", ".psb"}:
        return inspect_stable_psd(path, attempts, interval)
    return inspect_stable_raster(path, attempts, interval)


def reconcile_layer_states(
    old_layers: list[dict], new_layers: list[dict], states: dict[str, dict] | None,
) -> dict[str, dict]:
    """Preserve visibility/opacity when Photoshop changes layer identifiers.

    Positional IDs remain the fast path. When layers are inserted/reordered,
    match by name, kind and hierarchy depth so user overrides follow the
    corresponding Photoshop layer instead of moving to another row.
    """
    states = states or {}
    result: dict[str, dict] = {}
    old_by_id = {str(layer.get("id", "")): layer for layer in old_layers}
    available: dict[tuple[str, str, int, bool], deque[str]] = defaultdict(deque)
    for layer in old_layers:
        identity = str(layer.get("id", ""))
        fingerprint = (
            str(layer.get("name", "")), str(layer.get("kind", "")),
            int(layer.get("depth", 0)), bool(layer.get("group", False)),
        )
        if identity in states:
            available[fingerprint].append(identity)

    used: set[str] = set()
    for layer in new_layers:
        identity = str(layer.get("id", ""))
        old = old_by_id.get(identity)
        if old is not None and (
            str(old.get("name", "")), str(old.get("kind", "")), int(old.get("depth", 0))
        ) == (
            str(layer.get("name", "")), str(layer.get("kind", "")), int(layer.get("depth", 0))
        ) and identity in states:
            result[identity] = dict(states[identity])
            used.add(identity)
            continue
        fingerprint = (
            str(layer.get("name", "")), str(layer.get("kind", "")),
            int(layer.get("depth", 0)), bool(layer.get("group", False)),
        )
        while available[fingerprint] and available[fingerprint][0] in used:
            available[fingerprint].popleft()
        if available[fingerprint]:
            old_identity = available[fingerprint].popleft()
            result[identity] = dict(states[old_identity])
            used.add(old_identity)
    return result
