"""Find verified local installers or published GitHub Releases."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests


OWNER = "JipsonDev"
REPOSITORY = "KuroPanelStudio"
RELEASES_API = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
ASSET_NAME = re.compile(r"^KuroPanelStudio-Setup-(\d+\.\d+\.\d+)-Windows-x64\.exe$")
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
MAX_INSTALLER_BYTES = 4 * 1024**3


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    filename: str
    url: str
    size: int
    sha256: str
    release_url: str
    notes: str
    local_path: Path | None = None


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(str(value).strip())
    if not match:
        raise UpdateError("Versión de actualización no válida.")
    return tuple(int(part) for part in match.groups())


def _safe_release_url(value: str) -> bool:
    return value.startswith(f"https://github.com/{OWNER}/{REPOSITORY}/releases/download/")


def local_update_manifest_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return root / "ManhuaSuiteEditor" / "Updates" / "local-release.json"


class LocalInstallerUpdater:
    """Read a manifest written only after a local installer is fully built."""

    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = manifest_path or local_update_manifest_path()

    def check(self, current_version: str) -> UpdateInfo | None:
        if not self.manifest_path.is_file():
            return None
        try:
            if self.manifest_path.stat().st_size > 16 * 1024:
                raise UpdateError("El manifiesto de actualización local no es válido.")
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            version = str(data["version"])
            if version_tuple(version) <= version_tuple(current_version):
                return None
            filename = f"KuroPanelStudio-Setup-{version}-Windows-x64.exe"
            installer = Path(str(data["installer"]))
            expected_size = int(data["size"])
            expected_sha = str(data["sha256"]).lower()
            if (
                not installer.is_absolute() or installer.name != filename
                or str(data.get("filename")) != filename
                or ASSET_NAME.fullmatch(filename) is None
                or not 0 < expected_size <= MAX_INSTALLER_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
                or not installer.is_file() or installer.stat().st_size != expected_size
            ):
                raise UpdateError("El instalador local no existe o no coincide con el manifiesto.")
            digest = hashlib.sha256()
            with installer.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_sha:
                raise UpdateError("El SHA-256 del instalador local no coincide.")
            return UpdateInfo(
                version, filename, "", expected_size, expected_sha, "",
                "Instalador local verificado", installer,
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise UpdateError(f"No se pudo leer la actualización local: {error}") from error


def check_available_update(current_version: str, channel: str = "github") -> UpdateInfo | None:
    local = LocalInstallerUpdater().check(current_version)
    if local is not None or channel == "local":
        return local
    return ReleaseUpdater().check(current_version)


class ReleaseUpdater:
    def __init__(self, session=None) -> None:
        self.session = session or requests.Session()

    def _get(self, url: str, *, stream: bool = False):
        response = self.session.get(
            url, headers={"Accept": "application/vnd.github+json", "User-Agent": "KuroPanelStudio-Updater"},
            timeout=(10, 30), stream=stream,
        )
        response.raise_for_status()
        return response

    def check(self, current_version: str) -> UpdateInfo | None:
        try:
            response = self._get(RELEASES_API)
            try:
                release = response.json()
            finally:
                response.close()
            version = str(release["tag_name"]).removeprefix("v")
            if version_tuple(version) <= version_tuple(current_version):
                return None
            if release.get("draft") or release.get("prerelease"):
                return None
            expected_name = f"KuroPanelStudio-Setup-{version}-Windows-x64.exe"
            asset = next((item for item in release.get("assets", []) if item.get("name") == expected_name), None)
            if asset is None or ASSET_NAME.fullmatch(expected_name) is None:
                raise UpdateError("La nueva versión no tiene un instalador compatible.")
            size = int(asset.get("size", 0))
            url = str(asset.get("browser_download_url", ""))
            if not 0 < size <= MAX_INSTALLER_BYTES or not _safe_release_url(url):
                raise UpdateError("El instalador publicado no es válido.")
            digest = str(asset.get("digest") or "")
            checksum = digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""
            if not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
                checksum = self._checksum_from_release(release, expected_name)
            release_url = str(release.get("html_url") or "")
            if not release_url.startswith(f"https://github.com/{OWNER}/{REPOSITORY}/releases/tag/"):
                release_url = ""
            return UpdateInfo(version, expected_name, url, size, checksum.lower(), release_url,
                              str(release.get("body") or "")[:1000])
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            raise UpdateError(f"No se pudo consultar GitHub Releases: {error}") from error

    def _checksum_from_release(self, release: dict, filename: str) -> str:
        asset = next((item for item in release.get("assets", []) if item.get("name") == "SHA256SUMS.txt"), None)
        url = str(asset.get("browser_download_url", "")) if asset else ""
        if not _safe_release_url(url):
            raise UpdateError("La Release no incluye una suma SHA-256 verificable.")
        response = self._get(url)
        try:
            if len(response.content) > 128 * 1024:
                raise UpdateError("El archivo de sumas es demasiado grande.")
            content = response.text
        finally:
            response.close()
        for line in content.splitlines():
            match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
            if match and match[2] == filename:
                return match[1].lower()
        raise UpdateError("No se encontró el SHA-256 del instalador.")

    def download(self, info: UpdateInfo, destination: Path,
                 progress: Callable[[int], None] = lambda _value: None,
                 cancelled: Callable[[], bool] = lambda: False) -> Path:
        """Stage only a complete installer whose size and SHA-256 match the Release."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        digest = hashlib.sha256()
        received = 0
        try:
            with self._get(info.url, stream=True) as response, partial.open("wb") as output:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if cancelled():
                        raise UpdateCancelled("Descarga cancelada.")
                    if not block:
                        continue
                    received += len(block)
                    if received > info.size:
                        raise UpdateError("El instalador excede el tamaño publicado.")
                    output.write(block)
                    digest.update(block)
                    progress(min(100, received * 100 // info.size))
            if cancelled():
                raise UpdateCancelled("Descarga cancelada.")
            if received != info.size or digest.hexdigest().lower() != info.sha256.lower():
                raise UpdateError("La descarga no coincide con el tamaño o SHA-256 publicado.")
            partial.replace(destination)
            return destination
        except (OSError, requests.RequestException) as error:
            raise UpdateError(f"No se pudo descargar la actualización: {error}") from error
        finally:
            partial.unlink(missing_ok=True)
