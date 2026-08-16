"""Run the production cleaning pipeline against a complete chapter page.

The input boxes are deliberately expanded around YOLO text detections so each
selection contains the complete speech balloon, matching the editor workflow
where the selection is a search region rather than the inpainting mask.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from PIL import Image

from core.cleaning_manager import CleaningManager
from core.psd_manager import load_source_image


def _progress(label: str):
    last = {"value": -10}

    def report(value: int) -> None:
        value = int(value)
        if value >= 100 or value - last["value"] >= 10:
            print(f"{label}: {value}%", flush=True)
            last["value"] = value

    return report


def _apply_patches(page: np.ndarray, patches: list[dict]) -> np.ndarray:
    result = np.ascontiguousarray(page.copy())
    for patch in patches:
        x, y = int(patch["x"]), int(patch["y"])
        pixels = np.asarray(patch["pixels"], dtype=np.uint8)
        mask = np.asarray(patch["mask"]) > 0
        h, w = mask.shape
        destination = result[y:y + h, x:x + w]
        destination[mask] = pixels[mask]
    return result


def _contact_sheet(
    original: np.ndarray,
    cleaned: np.ndarray,
    entries: list[dict],
    output: Path,
) -> None:
    tiles: list[np.ndarray] = []
    for index, entry in enumerate(entries, start=1):
        target = entry["target"]
        x, y = int(target["x"]), int(target["y"])
        w, h = int(target["width"]), int(target["height"])
        pad = 12
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2 = min(original.shape[1], x + w + pad)
        y2 = min(original.shape[0], y + h + pad)
        before = original[y1:y2, x1:x2]
        after = cleaned[y1:y2, x1:x2]
        separator = np.full((before.shape[0], 4, 3), (0, 205, 225), np.uint8)
        crop = np.concatenate((before, separator, after), axis=1)
        tile = np.full((220, 360, 3), (14, 17, 21), np.uint8)
        scale = min(346 / max(1, crop.shape[1]), 184 / max(1, crop.shape[0]))
        nw, nh = max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))
        resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
        ox, oy = (360 - nw) // 2, 30 + (184 - nh) // 2
        tile[oy:oy + nh, ox:ox + nw] = resized[:, :, ::-1]
        method = str(entry.get("method", "lama"))
        cv2.putText(
            tile, f"#{index}  {method}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
            0.48, (225, 230, 235), 1, cv2.LINE_AA,
        )
        tiles.append(tile)

    cols = 3
    rows = math.ceil(len(tiles) / cols)
    sheet = np.full((rows * 220, cols * 360, 3), (10, 12, 15), np.uint8)
    for index, tile in enumerate(tiles):
        row, col = divmod(index, cols)
        sheet[row * 220:(row + 1) * 220, col * 360:(col + 1) * 360] = tile
    cv2.imwrite(str(output), sheet)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boxes", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=Path("Models"))
    parser.add_argument("--output", type=Path, default=Path("test_outputs"))
    args = parser.parse_args()

    payload = json.loads(args.boxes.read_text(encoding="utf-8"))
    source = Path(payload["source"])
    boxes = [
        {key: int(box[key]) for key in ("x", "y", "width", "height")}
        for box in payload["boxes"]
    ]
    args.output.mkdir(parents=True, exist_ok=True)

    manager = CleaningManager(args.models)
    manager.set_resource_profile("balanced")
    manager.set_device_mode("auto")
    manager.set_aggressiveness(90)

    started = monotonic()
    plan = manager.prepare_masks(source, boxes, _progress("mascara"), lambda: False)
    mask_seconds = monotonic() - started
    print(
        "plan:", len(plan.get("entries", [])), "entradas,",
        plan.get("solid_entries", 0), "solidas,",
        plan.get("masked_pixels", 0), "pixeles", flush=True,
    )

    clean_started = monotonic()
    result = manager.clean_prepared(source, plan, _progress("limpieza"), lambda: False)
    clean_seconds = monotonic() - clean_started

    original = np.asarray(load_source_image(source), dtype=np.uint8)
    cleaned = _apply_patches(original, result.get("patches", []))
    cleaned_path = args.output / "page01_full_balloon_clean.png"
    Image.fromarray(cleaned).save(cleaned_path, compress_level=3)

    mask_page = np.zeros(original.shape[:2], dtype=np.uint8)
    for entry in plan.get("entries", []):
        x, y = int(entry["x"]), int(entry["y"])
        mask = np.asarray(entry["mask"], dtype=np.uint8)
        h, w = mask.shape
        np.maximum(mask_page[y:y + h, x:x + w], mask, out=mask_page[y:y + h, x:x + w])
    mask_path = args.output / "page01_full_balloon_mask.png"
    Image.fromarray(mask_page).save(mask_path, compress_level=3)

    contact_path = args.output / "page01_full_balloon_before_after.png"
    _contact_sheet(original, cleaned, plan.get("entries", []), contact_path)

    methods = Counter(str(entry.get("method", "lama")) for entry in plan.get("entries", []))
    metrics = {
        "source": str(source),
        "source_size": [int(original.shape[1]), int(original.shape[0])],
        "requested_boxes": len(boxes),
        "mask_entries": len(plan.get("entries", [])),
        "methods": dict(methods),
        "masked_pixels": int(plan.get("masked_pixels", 0)),
        "mask_seconds": round(mask_seconds, 3),
        "clean_seconds": round(clean_seconds, 3),
        "total_seconds": round(monotonic() - started, 3),
        "provider": result.get("provider", plan.get("provider")),
        "runtime": result.get("runtime", plan.get("runtime")),
        "patches": len(result.get("patches", [])),
        "residual_passes": int(result.get("residual_passes", 0)),
        "outputs": {
            "cleaned": str(cleaned_path),
            "mask": str(mask_path),
            "before_after": str(contact_path),
        },
    }
    metrics_path = args.output / "page01_full_balloon_metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
