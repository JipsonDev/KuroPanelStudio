"""Version embedded in a source checkout or a frozen Windows build."""
from __future__ import annotations

import json
from pathlib import Path


def installed_version() -> str:
    version_file = Path(__file__).resolve().parents[1] / "assets" / "build_version.json"
    try:
        return str(json.loads(version_file.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "0.2.1"


APP_VERSION = installed_version()
