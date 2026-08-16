$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-gpu\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "No se encontro el entorno .venv-gpu."
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean "ManhuaSuiteEditorCompact.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller termino con codigo $LASTEXITCODE."
    }

    # The compact edition is CPU-first. These providers cannot run without
    # their matching external runtimes and otherwise only add package weight.
    $runtime = Join-Path $projectRoot "dist\KuroPanelStudioCompact\_internal\onnxruntime\capi"
    foreach ($provider in @("onnxruntime_providers_cuda.dll", "onnxruntime_providers_tensorrt.dll")) {
        $providerPath = Join-Path $runtime $provider
        if (Test-Path -LiteralPath $providerPath) {
            Remove-Item -LiteralPath $providerPath -Force
        }
    }

    # ONNX Runtime's GPU wheel exposes its PyTorch preload search path during
    # analysis. PyInstaller can therefore copy orphaned cuBLAS DLLs even though
    # torch itself is excluded and the compact build uses CPUExecutionProvider.
    $orphanedTorch = Join-Path $projectRoot "dist\KuroPanelStudioCompact\_internal\torch"
    if (Test-Path -LiteralPath $orphanedTorch) {
        Remove-Item -LiteralPath $orphanedTorch -Recurse -Force
    }

    $temporaryBuild = Join-Path $projectRoot "build"
    if (Test-Path -LiteralPath $temporaryBuild) {
        Remove-Item -LiteralPath $temporaryBuild -Recurse -Force
    }
    $compactRoot = Join-Path $projectRoot "dist\KuroPanelStudioCompact"
    $archive = Join-Path $projectRoot "dist\KuroPanelStudioCompact.zip"
    Compress-Archive -Path (Join-Path $compactRoot "*") -DestinationPath $archive -CompressionLevel Optimal -Force
    Write-Host "Edicion compacta lista en dist\KuroPanelStudioCompact\KuroPanelStudioCompact.exe"
    Write-Host "Paquete comprimido listo en dist\KuroPanelStudioCompact.zip"
}
finally {
    Pop-Location
}
