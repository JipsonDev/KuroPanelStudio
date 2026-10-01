"""Version embedded in a source checkout or a frozen Windows build."""
from __future__ import annotations

import json
from pathlib import Path


def build_info() -> dict:
    version_file = Path(__file__).resolve().parents[1] / "assets" / "build_version.json"
    try:
        return json.loads(version_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return {"version": "0.2.9", "update_channel": "github"}


def installed_version() -> str:
    return str(build_info().get("version") or "0.2.9")


_BUILD_INFO = build_info()
APP_VERSION = str(_BUILD_INFO.get("version") or "0.2.9")
APP_UPDATE_CHANNEL = "local" if _BUILD_INFO.get("update_channel") == "local" else "github"
