# KuroPanel Studio @VERSION@

## Guion más fluido

- Las tarjetas se actualizan sin reconstruir toda la lista, conservando el cursor, el foco y deshacer cuando su contenido no cambia.
- Pegar o editar una traducción muestra una vista previa inmediata. La composición final y los efectos se agrupan tras 180 ms sin nuevas ediciones.
- La cola mantiene su selección en los refrescos sin cambios. Los párrafos largos tienen una vista previa corta y el texto completo en la ayuda emergente.
- Con 500 diálogos, el refresco del panel sin cambios bajó de unos 872 ms a 8 ms en la medición local; esta cifra no mide todo el procesamiento del capítulo.

## Exportación de imágenes

- Corregido `attempt to get argmax of an empty sequence` cuando una caja cruza el borde de la página. Se conserva su tamaño y se recorta el resultado al tamaño de la imagen.
- Exportación comprobada en PNG, JPG, WebP y PSD con cajas parcialmente y completamente fuera de página.

## Limpieza y conservación del dibujo

- Recuperación de pequeños restos y halos grises próximos a letras confirmadas sobre fondos planos blancos, coloreados y oscuros, con protección de líneas largas, textura y márgenes.
- El fondo se estima sin incluir el texto ya enmascarado. La corrección de degradados exige más evidencia para conservar la reconstrucción de LaMa cuando hay dibujo superpuesto.
- En una subzona blanca real anotada, los residuos grises bajaron de 87 píxeles a 0. Las siete zonas mantienen cero residuos oscuros con el criterio de evaluación anterior. Quedan irregularidades en un borde gris superpuesto; los resultados necesitan revisión visual.
- Se mantiene LaMa especializado en manga. Estas mejoras locales no consumen créditos de API ni añaden descargas de modelos. Caché de máscaras versión 44.

## Detección y consumo OCR

- Segunda búsqueda local de texto en globos cerrados que quedaron sin marcar, con una revisión de YOLO a mayor escala solo en esas zonas.
- Conservación de bloques grandes respaldados por líneas alineadas, para evitar omitir líneas junto a un borde.
- Recortes OCR con menos márgenes uniformes y ampliaciones innecesarias, conservando trazos tenues, puntuación y resolución para diálogos alargados.
- Reutilización de recortes idénticos entre cajas, páginas y reinicios: comparten una petición. La relectura explícita sigue generando una solicitud nueva.
- Las respuestas truncadas se muestran como error, sin guardarlas como completas ni repetir automáticamente la petición.
- Revisión visual con navegación y acciones separadas; las tres vistas se reajustan al redimensionar la ventana.

## Instalación y verificación

La suite local aprobó **475 pruebas y 11 subpruebas** en una ejecución conjunta.
El cierre nativo intermitente de Qt observado en ejecuciones anteriores no tiene
todavía una causa confirmada; esta ejecución satisfactoria no demuestra que esté resuelto.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; su SHA-256 está en
`SHA256SUMS.txt`. La edición pública mantiene el canal de GitHub Releases para
**Buscar actualizaciones**. Si usas una edición con canal exclusivamente local,
instala esta edición pública una vez para recibir futuras versiones de GitHub.
