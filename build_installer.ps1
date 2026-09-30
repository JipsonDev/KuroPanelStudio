param(
    [string]$Version = "0.2.2"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceDir = Join-Path $projectRoot "dist\KuroPanelStudio"
$sourceExe = Join-Path $sourceDir "KuroPanelStudio.exe"
$outputDir = Join-Path $projectRoot "github-release\KuroPanelStudio-v$Version"
$installerName = "KuroPanelStudio-Setup-$Version-Windows-x64.exe"
$installerPath = Join-Path $outputDir $installerName
$specPath = Join-Path $projectRoot "installer\KuroPanelStudio.iss"

if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "No se encontro la distribucion compilada: $sourceExe"
}

$candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$compiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) {
    throw "Inno Setup 6 no esta instalado. Instala JRSoftware.InnoSetup con winget."
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

Push-Location (Split-Path -Parent $specPath)
try {
    & $compiler "/DMyAppVersion=$Version" "/DSourceDir=$sourceDir" "/DOutputDir=$outputDir" $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup termino con codigo $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $installerPath)) {
    throw "El compilador no genero el instalador esperado: $installerPath"
}

$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
$checksum = "$hash  $installerName`r`n"
[IO.File]::WriteAllText((Join-Path $outputDir "SHA256SUMS.txt"), $checksum, [Text.UTF8Encoding]::new($false))

$readmeSource = Join-Path $projectRoot "installer\GITHUB_RELEASE_README.md"
$notesSource = Join-Path $projectRoot "installer\RELEASE_NOTES.md"
$readme = [IO.File]::ReadAllText($readmeSource).Replace("@VERSION@", $Version)
$notes = [IO.File]::ReadAllText($notesSource).Replace("@VERSION@", $Version)
[IO.File]::WriteAllText((Join-Path $outputDir "README.md"), $readme, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $outputDir "RELEASE_NOTES.md"), $notes, [Text.UTF8Encoding]::new($false))

$sizeMb = [math]::Round((Get-Item -LiteralPath $installerPath).Length / 1MB, 1)
$buildInfo = @(
    "Producto: KuroPanel Studio",
    "Version: $Version",
    "Plataforma: Windows x64",
    "Archivo: $installerName",
    "Tamano MB: $sizeMb",
    "SHA256: $hash",
    "Generado UTC: $([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))"
) -join "`r`n"
[IO.File]::WriteAllText((Join-Path $outputDir "BUILD_INFO.txt"), $buildInfo + "`r`n", [Text.UTF8Encoding]::new($false))

Write-Host "Instalador listo: $installerPath"
Write-Host "SHA256: $hash"
Write-Host "Tamano: $sizeMb MB"
