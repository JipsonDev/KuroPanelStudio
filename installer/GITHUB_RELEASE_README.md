# KuroPanel Studio @VERSION@ — Windows x64

Esta carpeta está preparada para publicarse como una **GitHub Release**.

## Instalación

1. Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`.
2. Ejecuta el instalador y elige si deseas un acceso directo en el escritorio.
3. Abre KuroPanel Studio desde el menú Inicio.

El instalador usa la carpeta del usuario y no necesita permisos de administrador. Incluye OCR, YOLO, LaMa, OpenCV y el runtime ONNX para CPU. La aceleración CUDA requiere una compilación GPU aparte.

Las claves de API no están incluidas. Cada usuario debe configurarlas dentro del programa; se guardan cifradas para esa cuenta de Windows.

## Verificar la descarga

En PowerShell, desde la carpeta descargada:

```powershell
Get-FileHash .\KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe -Algorithm SHA256
```

Compara el resultado con `SHA256SUMS.txt`.

## Publicación en GitHub

El instalador se adjunta automáticamente a GitHub Releases mediante el flujo
`windows-release.yml` cuando se sube una etiqueta `vX.Y.Z`.
