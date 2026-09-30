# KuroPanel Studio @VERSION@

Esta versión corrige el ajuste del texto al mover o redimensionar su caja.

## Cambios

- Al mover o redimensionar manualmente una caja, sus bordes pasan a limitar el texto. El contenido se recompone al soltarla, en lugar de volver a ocupar el globo completo.
- El lienzo y la exportación respetan la posición y el tamaño de la caja ajustada.
- Las cajas OCR sin ajustes manuales siguen aprovechando el contorno completo del globo.

La suite local pasó **411 pruebas y 8 subpruebas** (5 pruebas omitidas).

El [historial de cambios](https://github.com/JipsonDev/KuroPanelStudio/blob/main/CHANGELOG.md) contiene las versiones anteriores.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe` y verifica su SHA-256 con `SHA256SUMS.txt`. La aplicación instalada también puede ofrecer esta versión desde **Buscar actualizaciones**.
