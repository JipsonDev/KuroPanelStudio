"""Optional regressions using the user's local, untracked chapter images."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from core.cleaning_manager import CleaningManager
from scripts.evaluate_real_cleaning import _local_rect, _mask_image, _text_rectangles


MANIFEST = Path(__file__).parent / "fixtures/real_cleaning_cases.json"
CASES = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
LOCAL_IMAGES = Path(os.environ.get(
    "KURO_REAL_CLEANING_IMAGES", str(Path.home() / "Downloads" / "prueba [stitched]"),
))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_real_page_glyph_coverage_and_artwork_boundary(case, tmp_path):
    image = LOCAL_IMAGES / case["image"]
    if not image.is_file():
        pytest.skip("Las páginas reales de limpieza no están en este equipo")
    if not (Path("Models/ocr.onnx").is_file() and Path("Models/lama.onnx").is_file()):
        pytest.skip("Faltan los modelos locales de limpieza")
    left, top, right, bottom = case["crop"]
    with Image.open(image) as page:
        original = np.asarray(page.crop((left, top, right, bottom)).convert("RGB"))
    source = tmp_path / "source.png"
    Image.fromarray(original).save(source)
    regions = []
    annotated = np.zeros(original.shape[:2], bool)
    for item in case["regions"]:
        x, y, width, height = _local_rect(item["selection"], (left, top))
        regions.append(dict(x=x, y=y, width=width, height=height))
        annotated[y:y + height, x:x + width] = True
        for text_rect in _text_rectangles(item):
            x, y, width, height = _local_rect(text_rect, (left, top))
            annotated[y:y + height, x:x + width] = True
    cleaner = CleaningManager(Path("Models"))
    cleaner.set_device_mode("cpu")
    cleaner.set_aggressiveness(90)
    plan = cleaner.prepare_masks(source, regions, lambda _value: None, lambda: False)
    mask = _mask_image(original.shape[:2], plan["entries"]) > 0
    assert len(plan["entries"]) == len(case["regions"])
    assert not np.any(mask & ~annotated), "La máscara toca dibujo fuera de las anotaciones"
    for item in case["regions"]:
        dark_count = covered = 0
        for text_rect in _text_rectangles(item):
            x, y, width, height = _local_rect(text_rect, (left, top))
            dark = np.min(original[y:y + height, x:x + width], axis=2) < 80
            dark_count += int(np.count_nonzero(dark))
            covered += int(np.count_nonzero(dark & mask[y:y + height, x:x + width]))
        assert dark_count > 100
        assert covered / dark_count >= .98, item["label"]
        if case["id"] == "179-03-globo-radial":
            assert covered == dark_count, "Reaparecieron las motas del primer carácter"
