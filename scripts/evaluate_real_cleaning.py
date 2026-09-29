"""Review production cleaning on locally supplied, visually annotated pages.

Run as ``python -m scripts.evaluate_real_cleaning --images <chapter-folder>``.
The source images and generated crops stay outside version control. Dark-pixel
counts are a useful residue proxy for these light balloons, not OCR accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np
from PIL import Image, ImageDraw

from core.cleaning_manager import CleaningManager


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "tests/fixtures/real_cleaning_cases.json"


def _local_rect(rect: list[int], origin: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y, width, height = (int(value) for value in rect)
    return x - origin[0], y - origin[1], width, height


def _text_rectangles(region: dict) -> list[list[int]]:
    if "text_rois" in region:
        return region["text_rois"]
    return [region["text_roi"]]


def _apply_patches(source: np.ndarray, patches: list[dict]) -> np.ndarray:
    result = source.copy()
    for patch in patches:
        x, y = int(patch["x"]), int(patch["y"])
        mask = np.asarray(patch["mask"]) > 0
        pixels = np.asarray(patch["pixels"], dtype=np.uint8)
        height, width = mask.shape
        destination = result[y:y + height, x:x + width]
        destination[mask] = pixels[mask]
    return result


def _mask_image(shape: tuple[int, int], entries: list[dict]) -> np.ndarray:
    result = np.zeros(shape, np.uint8)
    for entry in entries:
        x, y = int(entry["x"]), int(entry["y"])
        mask = np.asarray(entry["mask"], dtype=np.uint8)
        height, width = mask.shape
        np.maximum(result[y:y + height, x:x + width], mask,
                   out=result[y:y + height, x:x + width])
    return result


def _review_tile(before: np.ndarray, after: np.ndarray, mask: np.ndarray,
                 rect: tuple[int, int, int, int], label: str, output: Path) -> None:
    x, y, width, height = rect
    margin = 35
    x1, y1 = max(0, x - margin), max(0, y - margin)
    x2, y2 = min(before.shape[1], x + width + margin), min(before.shape[0], y + height + margin)
    original = before[y1:y2, x1:x2]
    cleaned = after[y1:y2, x1:x2]
    overlay = original.copy()
    overlay[mask[y1:y2, x1:x2] > 0] = (
        overlay[mask[y1:y2, x1:x2] > 0].astype(np.uint16) // 2
        + np.array([125, 0, 0], dtype=np.uint16)
    ).astype(np.uint8)
    panels = [Image.fromarray(image) for image in (original, overlay, cleaned)]
    sheet = Image.new("RGB", (panels[0].width * 3, panels[0].height + 28), "#111820")
    draw = ImageDraw.Draw(sheet)
    for index, (title, panel) in enumerate(zip(("Original", "Máscara", "Limpio"), panels)):
        sheet.paste(panel, (index * panel.width, 28))
        draw.text((index * panel.width + 8, 7), f"{title} · {label}" if index == 0 else title,
                  fill="white")
    sheet.save(output)


def evaluate_case(case: dict, images: Path, output: Path, manager: CleaningManager,
                  masks_only: bool = False) -> dict:
    image = (images / case["image"]).resolve()
    if not image.is_relative_to(images.resolve()) or not image.is_file():
        raise FileNotFoundError(f"Falta la página de prueba: {case['image']}")
    destination = output / case["id"]
    destination.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = (int(value) for value in case["crop"])
    with Image.open(image) as page:
        before = np.asarray(page.crop((left, top, right, bottom)).convert("RGB"))
    crop_path = destination / "source.png"
    Image.fromarray(before).save(crop_path)
    regions = []
    for item in case["regions"]:
        x, y, width, height = _local_rect(item["selection"], (left, top))
        if not (0 <= x < before.shape[1] and 0 <= y < before.shape[0]
                and x + width <= before.shape[1] and y + height <= before.shape[0]):
            raise ValueError(f"La selección de {case['id']} sale del recorte")
        regions.append(dict(x=x, y=y, width=width, height=height))
    start = monotonic()
    plan = manager.prepare_masks(crop_path, regions, lambda _value: None, lambda: False)
    mask = _mask_image(before.shape[:2], plan.get("entries", []))
    Image.fromarray(mask).save(destination / "mask.png")
    mask_seconds = monotonic() - start
    if masks_only:
        after = before
    else:
        cleaned = manager.clean_prepared(crop_path, plan, lambda _value: None, lambda: False)
        after = _apply_patches(before, cleaned.get("patches", []))
        Image.fromarray(after).save(destination / "cleaned.png")
    selected_area = np.zeros(before.shape[:2], bool)
    annotated_text = np.zeros(before.shape[:2], bool)
    measurements = []
    for index, item in enumerate(case["regions"], start=1):
        dark_before = dark_after = dark_covered = mask_pixels = 0
        for text_rect in _text_rectangles(item):
            x, y, width, height = _local_rect(text_rect, (left, top))
            before_roi = before[y:y + height, x:x + width]
            after_roi = after[y:y + height, x:x + width]
            dark_ink = np.min(before_roi, axis=2) < 80
            text_mask = mask[y:y + height, x:x + width] > 0
            dark_before += int(np.count_nonzero(dark_ink))
            dark_after += int(np.count_nonzero(np.min(after_roi, axis=2) < 80))
            dark_covered += int(np.count_nonzero(dark_ink & text_mask))
            mask_pixels += int(np.count_nonzero(text_mask))
            annotated_text[y:y + height, x:x + width] = True
        measurements.append({
            "label": item["label"], "dark_before": dark_before,
            "dark_after": dark_after if not masks_only else None,
            "dark_mask_coverage": round(dark_covered / max(1, dark_before), 4),
            "mask_pixels": mask_pixels,
        })
        bx, by, bw, bh = _local_rect(item["selection"], (left, top))
        selected_area[by:by + bh, bx:bx + bw] = True
        _review_tile(before, after, mask, (bx, by, bw, bh), item["label"],
                     destination / f"region-{index:02}.png")
    changed = np.any(before != after, axis=2)
    outside_text = changed & ~annotated_text
    report = {
        "id": case["id"], "image": case["image"], "regions": measurements,
        "mask_seconds": round(mask_seconds, 3),
        "total_seconds": round(monotonic() - start, 3),
        "changed_outside_selections": int(np.count_nonzero(changed & ~selected_area)),
        "changed_outside_annotated_text": int(np.count_nonzero(changed & ~(selected_area | annotated_text))),
        "changed_outside_text_rois": int(np.count_nonzero(outside_text)),
        "dark_changed_outside_text_rois": int(np.count_nonzero(outside_text & (np.min(before, axis=2) < 80))),
        "masked_outside_annotated_text": int(np.count_nonzero((mask > 0) & ~(selected_area | annotated_text))),
        "mask_entries": len(plan.get("entries", [])),
        "review": str(destination),
    }
    (destination / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _overview(reports: list[dict], output: Path) -> None:
    tiles = []
    for report in reports:
        for index in range(1, len(report["regions"]) + 1):
            with Image.open(Path(report["review"]) / f"region-{index:02}.png") as source:
                tile = source.convert("RGB")
                width = min(1800, tile.width)
                if tile.width != width:
                    tile = tile.resize((width, max(1, round(tile.height * width / tile.width))), Image.Resampling.LANCZOS)
                tiles.append(tile)
    if not tiles:
        return
    margin = 12
    width = max(tile.width for tile in tiles)
    height = sum(tile.height for tile in tiles) + margin * (len(tiles) + 1)
    sheet = Image.new("RGB", (width + margin * 2, height), "#101720")
    top = margin
    for tile in tiles:
        sheet.paste(tile, (margin, top))
        top += tile.height + margin
    sheet.save(output, quality=90)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", required=True, type=Path, help="Carpeta que contiene 156/ y 179/")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=Path("graphify-out/real-cleaning/review"))
    parser.add_argument("--case", action="append", help="Ejecutar solo este caso (repetible)")
    parser.add_argument("--masks-only", action="store_true", help="Omitir LaMa para una revisión rápida")
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = [case for case in payload["cases"] if not args.case or case["id"] in args.case]
    args.output.mkdir(parents=True, exist_ok=True)
    manager = CleaningManager(Path("Models"))
    manager.set_device_mode("cpu")
    manager.set_resource_profile("balanced")
    manager.set_aggressiveness(90)
    reports = []
    for case in cases:
        report = evaluate_case(case, args.images, args.output, manager, args.masks_only)
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    (args.output / "summary.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _overview(reports, args.output / "overview.jpg")


if __name__ == "__main__":
    main()
