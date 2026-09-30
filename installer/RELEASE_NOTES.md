# KuroPanel Studio @VERSION@

Esta versión mejora el tipeo y la edición de texto sobre las páginas.

## Cambios

- El texto puede usar el contorno completo de un globo aunque la caja de OCR sea pequeña. Si el borde no se detecta con confianza, conserva la caja como área segura.
- La edición con doble clic recompone tamaño, saltos de línea y centrado mientras se escribe, y avisa de inmediato si el texto no cabe. Deshacer y rehacer siguen disponibles.
- La vista previa y la exportación comparten la geometría del globo para mantener la colocación del texto.
- Arrastrar una esquina con Ctrl escala la caja y el texto. Las esquinas también funcionan mientras está abierto el editor de texto.
- La sección de herramientas de texto está mejor organizada y los estilos de diálogo adaptan el texto al globo de forma predeterminada; las elecciones manuales existentes se respetan.

La suite local pasó **408 pruebas y 8 subpruebas** (5 pruebas omitidas).

El [historial de cambios](https://github.com/JipsonDev/KuroPanelStudio/blob/main/CHANGELOG.md) contiene las versiones anteriores.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe` y verifica su SHA-256 con `SHA256SUMS.txt`. La aplicación instalada también puede ofrecer esta versión desde **Buscar actualizaciones**.
