# KuroPanel Studio @VERSION@

## Texto exportado como en el lienzo

- Corregido el texto más pequeño al guardar: la exportación conserva el tamaño, los saltos de línea, el centrado y la posición final del lienzo.
- El documento tipográfico se dibuja con el mismo motor Qt de la interfaz. Conserva el espaciado, las fuentes de manga y sus caracteres de respaldo para letras acentuadas, también al exportar en segundo plano.
- PNG, JPG, WebP y PSD comparten la composición guardada. PSD coloca correctamente el contenido y su máscara, y conserva texto que ocupa más espacio que la caja inicial del OCR.
- Para proyectos anteriores, abre la página en el lienzo y vuelve a exportarla para incorporar sus medidas finales.

## Verificación e instalación

Verificación local: **496 pruebas y 11 subpruebas aprobadas**, incluidas 21 comparaciones nuevas de composición, alineación, escala, acentos, contornos, subrayado y formatos de imagen. Se reprodujo el texto de las capturas en páginas controladas; no se dispuso del proyecto original. El cierre nativo intermitente de Qt documentado anteriormente no tiene una causa confirmada.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; su SHA-256 está en `SHA256SUMS.txt`. La edición pública mantiene el canal de GitHub Releases para **Buscar actualizaciones**. Si usas la edición local 0.2.14, instala esta edición pública una vez para recibir futuras versiones de GitHub.
