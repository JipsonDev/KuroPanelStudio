# KuroPanel Studio @VERSION@

## Tira continua y marcas de agua

- **Tira continua** recorre todas las páginas del capítulo sin crear una imagen gigante. Carga solo las teselas cercanas, con cachés limitadas y trabajos fuera del hilo de interfaz.
- Arrastra o borra las marcas en la tira. Las posiciones quedan asociadas a cada página y se conservan al guardar, exportar y deshacer.
- Doble clic abre una página para OCR, limpieza o edición de texto. Las páginas permanecen separadas en el proyecto; las selecciones OCR no cruzan archivos.
- **Exportar tira** guarda un solo PNG a resolución original, con textos, retoques y marcas. Escribe página a página y conserva el destino anterior si se cancela.
- El reparto denso ya no descarta la marca intermedia cuando la cantidad solicitada sí cabe con la separación mínima.
- Los archivos nuevos se importan en orden natural: 1, 2, 10. Los proyectos guardados conservan su orden.
- Se mantienen MI-GAN y LaMa, la protección de globos y las correcciones de exportación de marcas.

## Rendimiento y distribución

Verificación local: **552 pruebas aprobadas, 6 omitidas y 11 subpruebas aprobadas**. Incluye posiciones manuales, exportación real de la tira, cancelación, teselas y cambio de capítulos.

En una prueba sintética local, pasar de 30 a 300 páginas mantuvo tres páginas decodificadas para el mismo recorte (14,58 MB de caché). Exportar 120 páginas a 900 × 216 000 tardó 4,29 s sin asignar el raster completo de 583,2 MB. Los tiempos varían según contenido y equipo. Un PNG muy alto puede superar límites de otros visores; la vista virtual sigue usando los originales.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; verifica `SHA256SUMS.txt`. Incluye modelos y runtime ONNX para CPU, sin claves de API. El canal público recibe actualizaciones desde GitHub Releases.
