# KuroPanel Studio @VERSION@

Esta versión mejora la respuesta de la interfaz al trabajar con capítulos largos.

## Cambios

- La lista de páginas reutiliza los estados visuales de OCR, limpieza, traducción y rotulación cuando no han cambiado. Esto reduce los repintados durante el procesamiento y al cambiar de página.
- Las miniaturas visibles se ponen en cola una sola vez por desplazamiento o cambio de filtro. La lista deja de recorrer todas las páginas antes de lanzar cada miniatura.
- El tamaño de cada archivo se toma de los metadatos ya leídos al cargar el capítulo; la interfaz evita una segunda consulta al disco por página.
- La publicación automática de GitHub ahora muestra estas notas de versión en lugar de un resumen genérico de commits.

La suite local pasó **399 pruebas y 8 subpruebas**. En una medición local con 200 páginas, diez actualizaciones de estado pasaron de unos 85 ms a unos 26 ms. El resultado depende del equipo y del capítulo.

El [historial de cambios](https://github.com/JipsonDev/KuroPanelStudio/blob/main/CHANGELOG.md) también resume la versión 0.2.0.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe` y verifica su SHA-256 con `SHA256SUMS.txt`. La aplicación instalada también puede ofrecer esta versión desde **Buscar actualizaciones**.
