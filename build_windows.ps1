param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "0.2.9",
    [switch]$LocalOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$gpuPython = Join-Path $projectRoot ".venv-gpu\Scripts\python.exe"
$cpuPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $gpuPython) { $gpuPython } else { $cpuPython }

if (-not (Test-Path -LiteralPath $python)) {
    throw "No se encontro .venv-gpu ni .venv."
}

$versionPath = Join-Path $projectRoot "assets\build_version.json"
$versionInfoPath = Join-Path $projectRoot "assets\windows_version_info.txt"
$previousVersion = [IO.File]::ReadAllBytes($versionPath)
$previousVersionInfo = [IO.File]::ReadAllBytes($versionInfoPath)
$parts = $Version.Split('.')
$channel = if ($LocalOnly) { "local" } else { "github" }
$json = "{`n  `"version`": `"$Version`",`n  `"update_channel`": `"$channel`"`n}`n"
[IO.File]::WriteAllText($versionPath, $json, [Text.UTF8Encoding]::new($false))
$versionInfo = [IO.File]::ReadAllText($versionInfoPath)
$versionInfo = [regex]::Replace($versionInfo, 'filevers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)', "filevers=($($parts[0]), $($parts[1]), $($parts[2]), 0)")
$versionInfo = [regex]::Replace($versionInfo, 'prodvers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)', "prodvers=($($parts[0]), $($parts[1]), $($parts[2]), 0)")
$versionInfo = [regex]::Replace($versionInfo, "(StringStruct\(u'(?:FileVersion|ProductVersion)', u')\d+\.\d+\.\d+(')", "`${1}$Version`$2")
[IO.File]::WriteAllText($versionInfoPath, $versionInfo, [Text.UTF8Encoding]::new($false))

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
        $resolvedBuild = (Resolve-Path -LiteralPath $temporaryBuild).Path
        $resolvedRoot = (Resolve-Path -LiteralPath $projectRoot).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
        if (-not $resolvedBuild.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "La carpeta temporal esta fuera del proyecto: $resolvedBuild"
        }
        Remove-Item -LiteralPath $temporaryBuild -Recurse -Force
    }
    Write-Host "Ejecutable listo en dist\KuroPanelStudio\KuroPanelStudio.exe"
}
finally {
    Pop-Location
    [IO.File]::WriteAllBytes($versionPath, $previousVersion)
    [IO.File]::WriteAllBytes($versionInfoPath, $previousVersionInfo)
}
