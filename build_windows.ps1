$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-gpu\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "No se encontro el entorno .venv-gpu."
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean "ManhuaSuiteEditor.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller termino con codigo $LASTEXITCODE."
    }
    # ``build`` contains PyInstaller's intermediate EXE. It is not portable
    # and fails because its neighboring ``_internal`` runtime is deliberately
    # assembled only by COLLECT under ``dist``. Remove the tempting duplicate
    # so users can only launch the completed application.
    $temporaryBuild = Join-Path $projectRoot "build"
    if (Test-Path -LiteralPath $temporaryBuild) {
        Remove-Item -LiteralPath $temporaryBuild -Recurse -Force
    }
    Write-Host "Ejecutable listo en dist\KuroPanelStudio\KuroPanelStudio.exe"
}
finally {
    Pop-Location
}
