"""Run the app OCR pipeline from a Photoshop-exported image.

Photoshop calls this script with a temporary PNG and receives a small JSON
payload. The script reuses the same settings and encrypted credentials as the
main desktop app, so the user does not need to configure API keys twice.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.credential_store import CredentialStore
from core.detection_manager import DetectionManager
from core.ocr_manager import OCRManager
from core.performance_manager import resource_policy
from core.settings_manager import SettingsManager

_CJK_RE = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\u31F0-\u31FF"
    r"\u1100-\u11FF\u3130-\u318F\uA960-\uA97F\uAC00-\uD7AF\uD7B0-\uD7FF]"
)


def _app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "ManhuaSuiteEditor"


def _settings() -> SettingsManager:
    app_settings = _app_data() / "settings.json"
    legacy_settings = ROOT / "api_configs.json"
    return SettingsManager(app_settings if app_settings.exists() else legacy_settings)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _ocr_configuration(provider: str | None, model: str | None) -> tuple[str, str, str]:
    settings = _settings()
    ocr_settings = settings.data.get("ocr", {})
    selected_provider = (provider or ocr_settings.get("platform") or "Alibaba Cloud").strip()
    selected_model = (model or ocr_settings.get("model") or "qwen-vl-ocr").strip()
    credentials = CredentialStore(_app_data() / "credentials.dat")
    api_key = credentials.get("Alibaba Cloud" if selected_provider == "Qwen API" else selected_provider)
    if not api_key:
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    return selected_provider, selected_model, api_key


def _performance_policy():
    performance = _settings().data.get("performance", {})
    return resource_policy(
        performance.get("resource_profile", "auto"),
        int(performance.get("image_cache_mb", 384)),
    )


def run_single_ocr(image_path: Path, provider: str | None, model: str | None) -> dict:
    selected_provider, selected_model, api_key = _ocr_configuration(provider, model)
    with Image.open(image_path) as image:
        width, height = image.size

    manager = OCRManager()
    manager.set_resource_policy(_performance_policy())
    progress_value = {"value": 0}

    def progress(value: int) -> None:
        progress_value["value"] = int(value)

    started = time.perf_counter()
    regions = [{
        "id": 1,
        "x": 0,
        "y": 0,
        "width": int(width),
        "height": int(height),
    }]
    recognized = manager.run_regions(
        image_path,
        regions,
        selected_provider,
        selected_model,
        api_key,
        progress,
        lambda: False,
    )
    text = recognized[0].get("text", "") if recognized else ""
    return {
        "ok": True,
        "text": str(text or "").strip(),
        "provider": selected_provider,
        "model": selected_model,
        "width": width,
        "height": height,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "progress": progress_value["value"],
        "regions": [{
            "id": 1,
            "x": 0,
            "y": 0,
            "width": width,
            "height": height,
            "text": str(text or "").strip(),
        }],
    }


def detect_regions(image_path: Path) -> dict:
    started = time.perf_counter()
    detector = DetectionManager(ROOT / "Models")
    policy = _performance_policy()
    detector.set_resource_policy(policy)
    detector.set_device_mode(_settings().data.get("performance", {}).get("device_mode", "auto"))

    progress_value = {"value": 0}

    def progress(value: int) -> None:
        progress_value["value"] = int(value)

    regions = detector.detect(image_path, progress, lambda: False)
    with Image.open(image_path) as image:
        width, height = image.size
    return {
        "ok": True,
        "width": width,
        "height": height,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "progress": progress_value["value"],
        "regions": regions,
    }


def _prefer_cjk_when_present(regions: list[dict]) -> list[dict]:
    cjk_regions = [
        region for region in regions
        if _CJK_RE.search(str(region.get("text", "")))
    ]
    return cjk_regions if cjk_regions else regions


def run_detected_ocr(
    image_path: Path,
    provider: str | None,
    model: str | None,
    source_script: str,
) -> dict:
    started = time.perf_counter()
    detection = detect_regions(image_path)
    regions = list(detection.get("regions", []))
    selected_provider, selected_model, api_key = _ocr_configuration(provider, model)
    if not regions:
        detection.update({
            "provider": selected_provider,
            "model": selected_model,
            "text": "",
        })
        return detection

    manager = OCRManager()
    manager.set_resource_policy(_performance_policy())
    progress_value = {"value": 0}

    def progress(value: int) -> None:
        progress_value["value"] = int(value)

    recognized = manager.run_regions(
        image_path,
        regions,
        selected_provider,
        selected_model,
        api_key,
        progress,
        lambda: False,
    )
    recognized = [region for region in recognized if str(region.get("text", "")).strip()]
    if source_script == "cjk":
        recognized = _prefer_cjk_when_present(recognized)
    recognized.sort(key=lambda item: (int(item.get("y", 0)), int(item.get("x", 0))))
    detection.update({
        "provider": selected_provider,
        "model": selected_model,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "progress": progress_value["value"],
        "regions": recognized,
        "text": "\n\n".join(
            f"{index}. {str(region.get('text', '')).strip()}"
            for index, region in enumerate(recognized, 1)
        ),
    })
    return detection


def run_ocr(
    image_path: Path,
    provider: str | None,
    model: str | None,
    mode: str,
    source_script: str,
) -> dict:
    if mode == "detect":
        return detect_regions(image_path)
    if mode == "detect-ocr":
        return run_detected_ocr(image_path, provider, model, source_script)
    if mode == "single":
        return run_single_ocr(image_path, provider, model)
    raise ValueError(f"Modo OCR no soportado: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Photoshop OCR bridge for KuroPanel Studio")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--mode",
        choices=("single", "detect", "detect-ocr"),
        default="single",
        help="single: OCR del recorte completo; detect: solo cajas; detect-ocr: YOLO + OCR por caja",
    )
    parser.add_argument(
        "--script",
        choices=("all", "cjk"),
        default="cjk",
        help="cjk descarta texto latino si el OCR detecta texto chino/japones/coreano.",
    )
    args = parser.parse_args()

    try:
        if not args.image.exists():
            raise FileNotFoundError(f"No existe la imagen temporal: {args.image}")
        payload = run_ocr(
            args.image,
            args.provider or None,
            args.model or None,
            args.mode,
            args.script,
        )
        _write_json(args.output, payload)
        return 0
    except Exception as error:  # noqa: BLE001 - must report errors to Photoshop.
        _write_json(args.output, {
            "ok": False,
            "error": str(error),
            "type": error.__class__.__name__,
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
