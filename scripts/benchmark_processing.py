"""Offline CPU benchmark. Optional baseline must be a trusted local Git ref.

Run: python scripts/benchmark_processing.py --baseline-ref origin/main --output benchmark.json
No OCR provider is contacted: transport latency is simulated locally.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.detection_manager import DetectionManager, TextRegion
from core.ocr_manager import OCRManager


def load_baseline(ref, name):
    source = subprocess.check_output(
        ["git", "show", f"{ref}:core/{name}.py"], cwd=ROOT, encoding="utf-8",
    )
    module = types.ModuleType(f"benchmark_baseline_{name}")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{ref}:core/{name}.py", "exec"), module.__dict__)
    return module


def median_seconds(operation, repeats):
    timings = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        timings.append(time.perf_counter() - started)
    return statistics.median(timings), result


def make_page(path):
    image = Image.new("RGB", (800, 3700), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except OSError:
        font = ImageFont.load_default(size=32)
    regions = []
    for index, y in enumerate(range(80, 3500, 280)):
        x = 80 if index % 2 == 0 else 400
        text = "Where are we?\nLet us find out!"
        bounds = draw.multiline_textbbox((x, y), text, font=font, spacing=8)
        color = [(10, 10, 10), (130, 25, 35), (40, 60, 155)][index % 3]
        draw.multiline_text((x, y), text, font=font, spacing=8, fill=color)
        regions.append(dict(id=str(index), x=bounds[0], y=bounds[1],
                            width=bounds[2] - bounds[0], height=bounds[3] - bounds[1]))
    image.save(path)
    image.close()
    return regions


def covered_dialogues(expected, detected):
    """Count synthetic labels whose centers fall inside a returned text region."""
    return sum(any(d["x"] <= r["x"] + r["width"] / 2 <= d["x"] + d["width"]
                   and d["y"] <= r["y"] + r["height"] / 2 <= d["y"] + d["height"]
                   for d in detected) for r in expected)


def benchmark(detector_cls, region_cls, ocr_cls, source, regions, repeats):
    rng = np.random.default_rng(71)
    candidates = [region_cls(int(x), int(y), 150, 60, float(c))
                  for x, y, c in zip(rng.integers(0, 800, 1200),
                                     rng.integers(0, 60000, 1200), rng.random(1200))]
    suppression, deduped = median_seconds(lambda: detector_cls._deduplicate(candidates), repeats)
    detector = detector_cls(ROOT / "Models")
    detector.set_device_mode("cpu")
    detector.cpu_threads = 2
    detector.warm_up()

    def detect():
        detector.clear_cache()
        return detector.detect(source, lambda _: None, lambda: False)

    detection, boxes = median_seconds(detect, repeats)
    # A cache hit must not decode the source or run a network.
    detection_cached, _ = median_seconds(lambda: detector.detect(source, lambda _: None, lambda: False), repeats)
    result = dict(deduplicate_seconds=suppression, retained_candidates=len(deduped),
                  detection_seconds=detection, detection_cached_seconds=detection_cached,
                  detected_boxes=len(boxes), synthetic_dialogues_covered=covered_dialogues(regions, boxes),
                  synthetic_dialogues_total=len(regions))
    # Avoid importing an optional torch distribution solely for benchmark teardown.
    detector._model = None

    ocr = ocr_cls()
    first_request = []
    started = 0.0

    def post(*_args, **_kwargs):
        if not first_request:
            first_request.append(time.perf_counter() - started)
        time.sleep(.01)
        return types.SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "sample"}}]})

    ocr._session = types.SimpleNamespace(post=post)
    many_regions = [dict(r, id=f"{iteration}-{r['id']}") for iteration in range(12) for r in regions]
    first_timings = []
    fresh_timings = []
    for _ in range(repeats):
        first_request.clear()
        started = time.perf_counter()
        ocr.run_regions(source, many_regions, "Alibaba Cloud", "offline", "test-key", lambda _: None, lambda: False, force_refresh=True)
        fresh_timings.append(time.perf_counter() - started)
        first_timings.append(first_request[0])
    cached, _ = median_seconds(lambda: ocr.run_regions(
        source, many_regions, "Alibaba Cloud", "offline", "test-key", lambda _: None, lambda: False,
    ), repeats)
    result.update(ocr_regions=len(many_regions), ocr_simulated_seconds=statistics.median(fresh_timings),
                  ocr_first_request_seconds=statistics.median(first_timings), ocr_cached_seconds=cached)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", help="Trusted Git revision to compare against")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    report = dict(python=sys.version.split()[0], platform=platform.platform(), cpu_threads=2,
                  repeats=args.repeats, image_size=[800, 3700], ocr_transport="simulated 10 ms; no network")
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "synthetic_page.png"
        regions = make_page(source)
        if args.baseline_ref:
            detection = load_baseline(args.baseline_ref, "detection_manager")
            ocr = load_baseline(args.baseline_ref, "ocr_manager")
            report["baseline_ref"] = args.baseline_ref
            report["baseline"] = benchmark(detection.DetectionManager, detection.TextRegion, ocr.OCRManager, source, regions, args.repeats)
        report["current"] = benchmark(DetectionManager, TextRegion, OCRManager, source, regions, args.repeats)
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
