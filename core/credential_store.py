"""Per-user encrypted credential storage backed by Windows DPAPI."""
from __future__ import annotations

import base64
import ctypes
import json
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, object]:
        buffer = ctypes.create_string_buffer(data)
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    @staticmethod
    def _protect(value: str) -> str:
        raw, keepalive = CredentialStore._blob(value.encode("utf-8"))
        output = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(raw), "ManhuaSuiteEditor", None, None, None, 0,
            ctypes.byref(output),
        ):
            raise ctypes.WinError()
        try:
            encrypted = ctypes.string_at(output.pbData, output.cbData)
            return base64.b64encode(encrypted).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
            del keepalive

    @staticmethod
    def _unprotect(value: str) -> str:
        raw, keepalive = CredentialStore._blob(base64.b64decode(value))
        output = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(raw), None, None, None, None, 0, ctypes.byref(output)
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)
            del keepalive

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, name: str) -> str:
        encrypted = self._load().get(name, "")
        if not encrypted:
            return ""
        try:
            return self._unprotect(encrypted)
        except (ValueError, OSError):
            return ""

    def set(self, name: str, value: str) -> None:
        values = self._load()
        if value:
            values[name] = self._protect(value)
        else:
            values.pop(name, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(values, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def configured(self, name: str) -> bool:
        return bool(self.get(name))
