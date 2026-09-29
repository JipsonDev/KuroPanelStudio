"""Release updates must be newer, compatible and verified before installation."""
from __future__ import annotations

import hashlib

import pytest

from core.app_updater import ReleaseUpdater, UpdateCancelled, UpdateError, UpdateInfo, version_tuple


class Response:
    def __init__(self, *, data=None, payload=b"") -> None:
        self.data = data
        self.content = payload
        self.text = payload.decode("utf-8", "replace")

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), max(1, chunk_size // 2)):
            yield self.content[start:start + max(1, chunk_size // 2)]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Session:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **_kwargs):
        return self.responses[url]


def release(payload=b"installer", *, digest=True):
    name = "KuroPanelStudio-Setup-0.2.0-Windows-x64.exe"
    url = f"https://github.com/JipsonDev/KuroPanelStudio/releases/download/v0.2.0/{name}"
    sha = hashlib.sha256(payload).hexdigest()
    installer = {"name": name, "browser_download_url": url, "size": len(payload)}
    if digest:
        installer["digest"] = f"sha256:{sha}"
    checksum_url = "https://github.com/JipsonDev/KuroPanelStudio/releases/download/v0.2.0/SHA256SUMS.txt"
    data = {"tag_name": "v0.2.0", "draft": False, "prerelease": False,
            "html_url": "https://github.com/JipsonDev/KuroPanelStudio/releases/tag/v0.2.0",
            "assets": [installer, {"name": "SHA256SUMS.txt", "browser_download_url": checksum_url}]}
    from core.app_updater import RELEASES_API
    session = Session({RELEASES_API: Response(data=data), url: Response(payload=payload),
                       checksum_url: Response(payload=f"{sha}  {name}\n".encode())})
    return ReleaseUpdater(session), url


def test_version_order_and_release_asset_selection():
    updater, _url = release()
    assert version_tuple("v0.10.0") > version_tuple("0.9.9")
    assert updater.check("0.1.9").version == "0.2.0"
    assert updater.check("0.2.0") is None
    with pytest.raises(UpdateError):
        version_tuple("0.2.0-beta")


def test_checksum_fallback_and_verified_download(tmp_path):
    updater, _url = release(b"real installer", digest=False)
    info = updater.check("0.1.0")
    progress = []
    path = updater.download(info, tmp_path / info.filename, progress.append)
    assert path.read_bytes() == b"real installer"
    assert progress[-1] == 100
    assert not path.with_name(path.name + ".part").exists()


def test_mismatched_or_cancelled_download_is_never_staged(tmp_path):
    updater, _url = release(b"different")
    info = updater.check("0.1.0")
    destination = tmp_path / info.filename
    incorrect = UpdateInfo(info.version, info.filename, info.url, info.size, "0" * 64,
                           info.release_url, info.notes)
    with pytest.raises(UpdateError, match="SHA-256"):
        updater.download(incorrect, destination)
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()
    with pytest.raises(UpdateCancelled):
        updater.download(info, destination, cancelled=lambda: True)
    assert not destination.exists()
