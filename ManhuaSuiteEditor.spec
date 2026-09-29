# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


root = Path(SPEC).resolve().parent
binaries = collect_dynamic_libs("onnxruntime")
torch_cuda = root / ".venv-gpu" / "Lib" / "site-packages" / "torch" / "lib"
# ONNX Runtime loads cuDNN's component DLLs dynamically only when the first
# convolution runs. Dependency analysis sees the small cudnn64 loader but not
# these children, which made the packaged app fail although CUDA was reported
# as available. Keep the complete cuDNN family plus its CUDA/cuBLAS runtime.
cuda_runtime_patterns = (
    "cublas*.dll", "cudart*.dll", "cudnn*.dll", "nvJitLink*.dll",
    "nvrtc64_130_0.dll", "nvrtc-builtins*.dll", "zlibwapi.dll",
)
cuda_runtime_files = {
    candidate.resolve()
    for pattern in cuda_runtime_patterns
    for candidate in torch_cuda.glob(pattern)
    if candidate.is_file()
}
for candidate in sorted(cuda_runtime_files, key=lambda path: path.name.casefold()):
    binaries.append((str(candidate), "torch/lib"))
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

# PSD compositors are selected dynamically. OCR, YOLO and LaMa all use the
# bundled ONNX graphs, so shipping PyTorch/Ultralytics would only add gigabytes
# and make Windows scan hundreds of unused DLLs on every cold launch.
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
        "polars", "_polars_runtime_32", "dashscope", "matplotlib", "scipy",
        "pytest",
    ],
    noarchive=False,
    optimize=1,
)
# Windows provides an unversioned icuuc.dll used by Qt6Core. Another package
# can expose ICU 78 on PATH during analysis; bundling it first makes QtCore
# fail to import because that DLL only exports version-suffixed symbols.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.casefold() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KuroPanelStudio",
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
    name="KuroPanelStudio",
)
