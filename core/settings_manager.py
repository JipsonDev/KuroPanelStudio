"""Persistent provider settings compatible with the reference editor layout."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


DEFAULT_SETTINGS = {
    "translate": {
        "platform": "Gemini",
        "model": "gemini-2.5-flash",
        "source_language": "ZH",
        "target_language": "ES",
        "glossary": "",
        "prompt": "",
    },
    "ocr": {"platform": "Alibaba Cloud", "model": "qwen-vl-ocr", "base_url": ""},
    # Legacy key fields are read for migration only. New secrets live in the
    # per-user DPAPI CredentialStore and are never written here.
    "global_keys": {
        "Gemini": "", "OpenAI": "", "DeepSeek": "", "DeepL": "", "Alibaba Cloud": "",
    },
    "clean": {"platform": "Local (AI)", "model": "LaMa · lama.onnx", "debug_mask": False, "local_aggressiveness": 90},
    "general": {
        "export_json_current_only": False, "autosave_minutes": 2,
        "backup_limit": 10, "psd_auto_sync": True, "ui_language": "es",
        "auto_check_updates": True,
    },
    "performance": {
        "resource_profile": "auto", "device_mode": "auto",
        "gpu_idle_minutes": 5, "image_cache_mb": 384,
    },
    "profiles": {"active_type": "", "active_project": ""},
    "watermark": {},
}


class SettingsManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()
        self.needs_save = False
        if self.data["general"].get("ui_language") not in {"es", "en"}:
            self.data["general"]["ui_language"] = "es"
            self.needs_save = True
        # Preserve explicit model versions: availability varies by region.
        if self.data["translate"].get("platform") not in {
            "Alibaba Cloud", "Gemini", "OpenAI", "DeepSeek", "DeepL",
        }:
            self.data["translate"]["platform"] = "Gemini"
            self.data["translate"]["model"] = "gemini-2.5-flash"
            self.needs_save = True

    def _load(self) -> dict:
        if not self.path.exists():
            return deepcopy(DEFAULT_SETTINGS)
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return deepcopy(DEFAULT_SETTINGS)
        settings = deepcopy(DEFAULT_SETTINGS)
        for section, values in stored.items():
            if section in settings and isinstance(values, dict):
                settings[section].update(values)
        return settings

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, ensure_ascii=False)
                handle.flush()
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def update_provider(self, section: str, platform: str, model: str) -> None:
        self.data[section]["platform"] = platform
        self.data[section]["model"] = model
        self.save()

    def update_workflow(self, values: dict) -> None:
        if "ocr_base_url" in values:
            self.data["ocr"]["base_url"] = str(values["ocr_base_url"]).strip()
        if "ocr_model" in values:
            self.data["ocr"]["model"] = str(values["ocr_model"]).strip()
        self.data["translate"].update({
            "platform": values["provider"], "model": values["model"],
            "source_language": values["source_language"], "target_language": values["target_language"],
        })
        self.data["general"]["autosave_minutes"] = int(values["autosave_minutes"])
        if "ui_language" in values:
            language = str(values["ui_language"]).lower()
            self.data["general"]["ui_language"] = language if language in {"es", "en"} else "es"
        if "auto_check_updates" in values:
            self.data["general"]["auto_check_updates"] = bool(values["auto_check_updates"])
        if "psd_auto_sync" in values:
            self.data["general"]["psd_auto_sync"] = bool(values["psd_auto_sync"])
        if "device_mode" in values:
            self.data["performance"].update({
                "resource_profile": str(values.get("resource_profile", "auto")),
                "device_mode": str(values["device_mode"]),
                "gpu_idle_minutes": int(values.get("gpu_idle_minutes", 5)),
                "image_cache_mb": int(values.get("image_cache_mb", 384)),
            })
        # Never persist plaintext credentials, including legacy values.
        self.data["global_keys"] = {
            provider: ""
            for provider in ("Gemini", "OpenAI", "DeepSeek", "DeepL", "Alibaba Cloud")
        }
        self.save()

    def update_psd_auto_sync(self, enabled: bool) -> None:
        self.data["general"]["psd_auto_sync"] = bool(enabled)
        self.save()

    def update_profile(self, active_type: str, active_project: str) -> None:
        self.data["profiles"] = {"active_type": active_type, "active_project": active_project}
        self.save()

    def update_watermark(self, values: dict) -> None:
        self.data["watermark"] = dict(values)
        self.save()
