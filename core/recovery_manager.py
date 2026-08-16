"""Atomic autosaves and incremental project backups."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


class RecoveryManager:
    def __init__(self, root: Path, backup_limit: int = 10) -> None:
        self.root = root
        self.backup_limit = max(1, backup_limit)
        self.autosave_path = root / "recovery.mseproj"
        self.backup_root = root / "Backups"

    def has_recovery(self) -> bool:
        return self.autosave_path.is_file() and self.autosave_path.stat().st_size > 0

    def clear_recovery(self) -> None:
        if self.autosave_path.exists():
            self.autosave_path.unlink()

    def backup(self, project_file: Path) -> Path | None:
        if not project_file.is_file():
            return None
        self.backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.backup_root / f"{project_file.stem}-{stamp}.mseproj"
        shutil.copy2(project_file, destination)
        backups = sorted(self.backup_root.glob(f"{project_file.stem}-*.mseproj"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in backups[self.backup_limit:]:
            stale.unlink()
        return destination
