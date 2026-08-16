# KuroPanel Studio 0.1.0 — Windows x64

Esta carpeta está preparada para publicarse como una **GitHub Release**.

## Instalación

1. Descarga `KuroPanelStudio-Setup-0.1.0-Windows-x64.exe`.
2. Ejecuta el instalador y elige si deseas un acceso directo en el escritorio.
3. Abre KuroPanel Studio desde el menú Inicio.

El instalador usa la carpeta del usuario y no necesita permisos de administrador. Incluye OCR, YOLO, LaMa, OpenCV y el runtime CUDA/cuDNN usado por la edición GPU. Cuando CUDA no esté disponible, el programa puede utilizar el proveedor CPU.

Las claves de API no están incluidas. Cada usuario debe configurarlas dentro del programa; se guardan cifradas para esa cuenta de Windows.

## Verificar la descarga

En PowerShell, desde la carpeta descargada:

```powershell
Get-FileHash .\KuroPanelStudio-Setup-0.1.0-Windows-x64.exe -Algorithm SHA256
```

Compara el resultado con `SHA256SUMS.txt`.

## Publicación en GitHub

El instalador supera el límite de archivos normales de un repositorio. Súbelo desde **Releases > Draft a new release** como recurso binario, no mediante un commit del repositorio.

Etiqueta recomendada: `v0.1.0`.
