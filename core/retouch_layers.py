"""Retouch-layer grouping and lossless patch consolidation."""
from __future__ import annotations

from collections import Counter

import numpy as np


RETOUCH_LAYER_ORDER = ("automatic", "paint", "restore")
RETOUCH_LAYER_NAMES = {
    "automatic": "Limpieza automática",
    "paint": "Pintura manual",
    "restore": "Restauración original",
}


def patch_layer(patch: dict) -> str:
    kind = str(patch.get("kind", "legacy")).strip().casefold()
    if kind == "automatic":
        return "automatic"
    if kind == "restore":
        return "restore"
    return "paint"


def layer_counts(patches: list[dict]) -> dict[str, int]:
    counts = Counter(patch_layer(patch) for patch in patches)
    return {layer: int(counts.get(layer, 0)) for layer in RETOUCH_LAYER_ORDER}


def default_layer_states() -> dict[str, dict]:
    return {
        layer: {"visible": True, "opacity": 100, "locked": False}
        for layer in RETOUCH_LAYER_ORDER
    }


def normalized_layer_states(states: dict | None) -> dict[str, dict]:
    result = default_layer_states()
    for layer, current in (states or {}).items():
        if layer not in result or not isinstance(current, dict):
            continue
        result[layer].update({
            "visible": bool(current.get("visible", True)),
            "opacity": max(0, min(100, int(current.get("opacity", 100)))),
            "locked": bool(current.get("locked", False)),
        })
    return result


def patch_history(patches: list[dict], limit: int = 12) -> list[dict]:
    entries = []
    for position, patch in enumerate(patches[-max(1, int(limit)):], start=max(0, len(patches) - limit) + 1):
        mask = np.asarray(patch.get("mask", []))
        entries.append({
            "position": position,
            "id": str(patch.get("id", "")),
            "layer": patch_layer(patch),
            "pixels": int(np.count_nonzero(mask)) if mask.ndim == 2 else 0,
            "qc_status": str(patch.get("qc_status", "")),
        })
    return entries


def _composite_patch(
    canvas_rgb: np.ndarray, canvas_alpha: np.ndarray,
    pixels: np.ndarray, mask: np.ndarray, x: int, y: int,
) -> None:
    height, width = mask.shape
    source_alpha = mask.astype(np.float32) / 255.0
    destination_alpha = canvas_alpha[y:y + height, x:x + width]
    output_alpha = source_alpha + destination_alpha * (1.0 - source_alpha)
    source_premultiplied = pixels.astype(np.float32) * source_alpha[:, :, None]
    destination_premultiplied = (
        canvas_rgb[y:y + height, x:x + width].astype(np.float32)
        * destination_alpha[:, :, None]
    )
    output_premultiplied = source_premultiplied + destination_premultiplied * (
        1.0 - source_alpha[:, :, None]
    )
    safe = np.maximum(output_alpha[:, :, None], 1e-6)
    canvas_rgb[y:y + height, x:x + width] = np.clip(
        output_premultiplied / safe, 0, 255,
    ).astype(np.uint8)
    canvas_alpha[y:y + height, x:x + width] = output_alpha


def consolidate_layer(patches: list[dict], layer: str) -> tuple[list[dict], int]:
    """Collapse all patches of one logical layer into a single RGBA patch."""
    selected = [(index, patch) for index, patch in enumerate(patches) if patch_layer(patch) == layer]
    if len(selected) < 2:
        return list(patches), 0
    bounds = []
    for _index, patch in selected:
        pixels = np.asarray(patch.get("pixels"), dtype=np.uint8)
        if pixels.ndim != 3 or pixels.shape[2] < 3:
            continue
        x, y = int(patch.get("x", 0)), int(patch.get("y", 0))
        bounds.append((x, y, x + pixels.shape[1], y + pixels.shape[0]))
    if len(bounds) < 2:
        return list(patches), 0
    x1, y1 = min(value[0] for value in bounds), min(value[1] for value in bounds)
    x2, y2 = max(value[2] for value in bounds), max(value[3] for value in bounds)
    rgb = np.zeros((y2 - y1, x2 - x1, 3), dtype=np.uint8)
    alpha = np.zeros((y2 - y1, x2 - x1), dtype=np.float32)
    for _index, patch in selected:
        pixels = np.ascontiguousarray(patch.get("pixels"), dtype=np.uint8)
        if pixels.ndim != 3 or pixels.shape[2] < 3:
            continue
        pixels = pixels[:, :, :3]
        mask = patch.get("mask")
        mask = (
            np.full(pixels.shape[:2], 255, dtype=np.uint8)
            if mask is None else np.ascontiguousarray(mask, dtype=np.uint8)
        )
        if mask.shape != pixels.shape[:2]:
            continue
        _composite_patch(
            rgb, alpha, pixels, mask,
            int(patch.get("x", 0)) - x1, int(patch.get("y", 0)) - y1,
        )
    merged = {
        "x": x1, "y": y1, "pixels": np.ascontiguousarray(rgb),
        "mask": np.clip(alpha * 255.0, 0, 255).astype(np.uint8),
        "kind": "manual" if layer == "paint" else layer,
        "consolidated": len(selected),
        "qc_status": "accepted" if all(
            str(patch.get("qc_status", "")) == "accepted" for _index, patch in selected
        ) else "",
    }
    first = selected[0][0]
    selected_ids = {id(patch) for _index, patch in selected}
    result = [patch for patch in patches if id(patch) not in selected_ids]
    result.insert(min(first, len(result)), merged)
    return result, len(selected)
