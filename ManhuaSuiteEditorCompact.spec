# -*- mode: python ; coding: utf-8 -*-
"""CPU-friendly build that keeps production features without PyTorch."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


root = Path(SPEC).resolve().parent
binaries = collect_dynamic_libs("onnxruntime")
datas = [
    (str(root / "assets"), "assets"),
    (str(root / "Models" / "lama.onnx"), "Models"),
    (str(root / "Models" / "ocr.onnx"), "Models"),
    (str(root / "Models" / "yolo12s_animetext.onnx"), "Models"),
    (str(root / "Models" / "yolo12s_1class.yaml"), "Models"),
]
# OCR imports Requests lazily to keep startup fast. PyInstaller cannot discover
# that importlib call by itself, so explicitly retain the HTTP package.
hiddenimports = ["requests"]

# Spanish hyphenation dictionaries are loaded from package data at runtime.
# Keep them explicitly so the frozen typography engine behaves like source.
pyphen_datas, pyphen_binaries, pyphen_hidden = collect_all("pyphen")
datas += pyphen_datas
binaries += pyphen_binaries
hiddenimports += pyphen_hidden

# PSD support selects compositors dynamically, so retain its resources.
package_datas, package_binaries, package_hidden = collect_all("psd_tools")
datas += package_datas
binaries += package_binaries
hiddenimports += package_hidden

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch", "torchvision", "torchaudio", "ultralytics", "sympy",
        "polars", "_polars_runtime_32", "dashscope", "matplotlib",
    ],
    noarchive=False,
    optimize=1,
)
# Use the Windows ICU shim expected by Qt rather than an unrelated ICU wheel.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.casefold() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KuroPanelStudioCompact",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "assets" / "icons" / "app_icon.ico"),
    version=str(root / "assets" / "windows_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="KuroPanelStudioCompact",
)
