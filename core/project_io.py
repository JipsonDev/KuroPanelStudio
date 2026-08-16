"""Read and write the lossless, portable .mseproj project bundle."""
from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

import cv2
import numpy as np

from core.project_manager import Page
from core.watermark_manager import normalized_watermark, watermark_bytes


PROJECT_VERSION = 4


def page_key(page: Page) -> str:
    return str(page.path) if page.path else page.name


def _relative_path(path: Path, root: Path | None) -> str | None:
    if root is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def save_project_bundle(
    destination: Path,
    pages: list[Page],
    active_index: int,
    page_regions: dict[str, list[dict]],
    clean_results: dict[str, dict],
    page_texts: dict[str, str],
    page_styles: dict[str, dict],
    style_presets: dict[str, dict] | None = None,
    project_profile: str = "",
    project_profile_type: str = "",
    effect_presets: dict[str, dict] | None = None,
    watermark_settings: dict | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_root = pages[0].path.parent if pages and pages[0].path else None
    watermark = normalized_watermark(watermark_settings)
    watermark_payload = watermark_bytes(watermark)
    watermark_manifest = {key: value for key, value in watermark.items() if key != "png_bytes"}
    # The asset is embedded; reopening never depends on the original PNG path.
    watermark_manifest["source_path"] = ""
    if watermark_payload:
        watermark_manifest["asset"] = "assets/watermark.png"
    manifest = {
        "version": PROJECT_VERSION,
        "source_root": str(source_root) if source_root else "",
        "active_index": active_index,
        "style_presets": style_presets or {},
        "project_profile": project_profile,
        "project_profile_type": project_profile_type,
        "effect_presets": effect_presets or {},
        "watermark": watermark_manifest,
        "pages": [],
    }
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            if watermark_payload:
                bundle.writestr("assets/watermark.png", watermark_payload)
            for page_index, page in enumerate(pages):
                key = page_key(page)
                patches_meta = []
                for patch_index, patch in enumerate(clean_results.get(key, {}).get("patches", [])):
                    patch_id = str(patch.get("id") or uuid.uuid4().hex)
                    patch["id"] = patch_id
                    pixels = np.ascontiguousarray(patch["pixels"])
                    mask = patch.get("mask")
                    if mask is not None:
                        rgba = np.dstack((pixels, np.ascontiguousarray(mask))).astype(np.uint8)
                        encoded_pixels = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
                    else:
                        encoded_pixels = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
                    success, encoded = cv2.imencode(".png", encoded_pixels)
                    if not success:
                        raise RuntimeError(f"No se pudo codificar el parche {patch_index + 1} de {page.name}.")
                    asset = f"patches/{page_index:04d}/{patch_id}.png"
                    bundle.writestr(asset, encoded.tobytes())
                    residual_asset = ""
                    residual_mask = patch.get("residual_mask")
                    if residual_mask is not None:
                        residual_mask = np.ascontiguousarray(residual_mask, dtype=np.uint8)
                        if residual_mask.ndim == 2 and np.any(residual_mask):
                            success, residual_encoded = cv2.imencode(".png", residual_mask)
                            if success:
                                residual_asset = f"patches/{page_index:04d}/{patch_id}-residual.png"
                                bundle.writestr(residual_asset, residual_encoded.tobytes())
                    patches_meta.append({
                        "id": patch_id,
                        "x": int(patch["x"]),
                        "y": int(patch["y"]),
                        "asset": asset,
                        "kind": str(patch.get("kind", "legacy")),
                        "qc_status": str(patch.get("qc_status", "")),
                        "residual_pixels": int(patch.get("residual_pixels", 0)),
                        "residual_asset": residual_asset,
                        "target": dict(patch.get("target", {})),
                        "consolidated": int(patch.get("consolidated", 0)),
                    })
                manifest["pages"].append({
                    "name": page.name,
                    "path": str(page.path) if page.path else "",
                    "relative_path": _relative_path(page.path, source_root) if page.path else None,
                    "width": page.width,
                    "height": page.height,
                    "size_label": page.size_label,
                    "source_layers": page.source_layers,
                    "regions": page_regions.get(key, []),
                    "text": page_texts.get(key, ""),
                    "styles": page_styles.get(key, {}),
                    "patches": patches_meta,
                })
            bundle.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_project_bundle(source: Path) -> dict:
    with zipfile.ZipFile(source, "r") as bundle:
        manifest = json.loads(bundle.read("project.json").decode("utf-8"))
        if int(manifest.get("version", 0)) > PROJECT_VERSION:
            raise RuntimeError("El proyecto fue creado con una versión más reciente del editor.")
        saved_root = Path(manifest.get("source_root", "")) if manifest.get("source_root") else None
        pages: list[Page] = []
        page_regions: dict[str, list[dict]] = {}
        clean_results: dict[str, dict] = {}
        page_texts: dict[str, str] = {}
        page_styles: dict[str, dict] = {}
        missing: list[str] = []
        watermark_data = dict(manifest.get("watermark", {}))
        watermark_asset = str(watermark_data.pop("asset", ""))
        if watermark_asset and watermark_asset in bundle.namelist():
            watermark_data["png_bytes"] = bundle.read(watermark_asset)
        for page_data in manifest.get("pages", []):
            candidates: list[Path] = []
            if page_data.get("relative_path"):
                if saved_root:
                    candidates.append(saved_root / page_data["relative_path"])
                candidates.append(source.parent / page_data["relative_path"])
            if page_data.get("path"):
                candidates.append(Path(page_data["path"]))
            image_path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if image_path is None:
                missing.append(page_data.get("name", "imagen"))
                continue
            page = Page(
                page_data.get("name", image_path.name), image_path,
                int(page_data.get("width", 0)), int(page_data.get("height", 0)),
                page_data.get("size_label", "—"), list(page_data.get("source_layers", [])),
            )
            pages.append(page)
            key = page_key(page)
            page_regions[key] = list(page_data.get("regions", []))
            page_texts[key] = str(page_data.get("text", ""))
            page_styles[key] = dict(page_data.get("styles", {}))
            patches = []
            for patch_data in page_data.get("patches", []):
                encoded = np.frombuffer(bundle.read(patch_data["asset"]), dtype=np.uint8)
                decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
                if decoded is None:
                    raise RuntimeError(f"El parche {patch_data['asset']} está dañado.")
                patch = {
                    "id": patch_data.get("id") or uuid.uuid4().hex,
                    "x": int(patch_data["x"]), "y": int(patch_data["y"]),
                    "pixels": np.ascontiguousarray(cv2.cvtColor(decoded[:, :, :3], cv2.COLOR_BGR2RGB)),
                    "kind": str(patch_data.get("kind", "legacy")),
                    "qc_status": str(patch_data.get("qc_status", "")),
                    "residual_pixels": int(patch_data.get("residual_pixels", 0)),
                    "target": dict(patch_data.get("target", {})),
                    "consolidated": int(patch_data.get("consolidated", 0)),
                }
                if decoded.ndim == 3 and decoded.shape[2] == 4:
                    patch["mask"] = np.ascontiguousarray(decoded[:, :, 3])
                residual_asset = str(patch_data.get("residual_asset", ""))
                if residual_asset and residual_asset in bundle.namelist():
                    residual_encoded = np.frombuffer(bundle.read(residual_asset), dtype=np.uint8)
                    residual = cv2.imdecode(residual_encoded, cv2.IMREAD_GRAYSCALE)
                    if residual is not None:
                        patch["residual_mask"] = np.ascontiguousarray(residual)
                patches.append(patch)
            if patches:
                clean_results[key] = {"patches": patches, "targets": [], "runtime": "Proyecto restaurado"}
    return {
        "pages": pages,
        "active_index": min(max(0, int(manifest.get("active_index", 0))), max(0, len(pages) - 1)),
        "page_regions": page_regions,
        "clean_results": clean_results,
        "page_texts": page_texts,
        "page_styles": page_styles,
        "style_presets": dict(manifest.get("style_presets", {})),
        "project_profile": str(manifest.get("project_profile", "")),
        "project_profile_type": str(manifest.get("project_profile_type", "")),
        "effect_presets": dict(manifest.get("effect_presets", {})),
        "watermark_settings": normalized_watermark(watermark_data),
        "missing": missing,
    }
