"""Bounded undo/redo timeline for editor document snapshots."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field


def _clone(value):
    """Copy containers while sharing immutable NumPy patch buffers."""
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone(item) for item in value)
    if value.__class__.__module__.startswith("numpy"):
        return value
    return copy.deepcopy(value)


def _fingerprint(snapshot: dict) -> str:
    """Compare document state without serialising the large pixel buffers."""
    clean = {}
    for page_key, result in snapshot.get("clean_results", {}).items():
        clean[page_key] = [
            {
                "id": patch.get("id"),
                "x": int(patch.get("x", 0)),
                "y": int(patch.get("y", 0)),
                "shape": list(getattr(patch.get("pixels"), "shape", ())),
            }
            for patch in result.get("patches", [])
        ]
    comparable = {
        "active_index": snapshot.get("active_index", 0),
        "page_regions": snapshot.get("page_regions", {}),
        "page_texts": snapshot.get("page_texts", {}),
        "page_styles": snapshot.get("page_styles", {}),
        "style_presets": snapshot.get("style_presets", {}),
        "clean_results": clean,
    }
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class HistoryManager:
    """A post-action timeline; restoring an entry never creates another one."""

    limit: int = 80
    _entries: list[dict] = field(default_factory=list)
    _fingerprints: list[str] = field(default_factory=list)
    _index: int = -1

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return 0 <= self._index < len(self._entries) - 1

    def reset(self, snapshot: dict) -> None:
        self._entries = [_clone(snapshot)]
        self._fingerprints = [_fingerprint(snapshot)]
        self._index = 0

    def record(self, snapshot: dict) -> bool:
        fingerprint = _fingerprint(snapshot)
        if self._index >= 0 and self._fingerprints[self._index] == fingerprint:
            return False
        del self._entries[self._index + 1 :]
        del self._fingerprints[self._index + 1 :]
        self._entries.append(_clone(snapshot))
        self._fingerprints.append(fingerprint)
        if len(self._entries) > self.limit:
            overflow = len(self._entries) - self.limit
            del self._entries[:overflow]
            del self._fingerprints[:overflow]
        self._index = len(self._entries) - 1
        return True

    def replace_current(self, snapshot: dict) -> None:
        """Update non-undoable state such as the currently viewed page."""
        if self._index < 0:
            self.reset(snapshot)
            return
        self._entries[self._index] = _clone(snapshot)
        self._fingerprints[self._index] = _fingerprint(snapshot)

    def undo(self) -> dict | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return _clone(self._entries[self._index])

    def redo(self) -> dict | None:
        if not self.can_redo:
            return None
        self._index += 1
        return _clone(self._entries[self._index])
