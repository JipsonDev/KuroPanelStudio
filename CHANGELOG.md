# Historial de cambios

## 0.2.3

- Las cajas de texto movidas o redimensionadas manualmente limitan la composición a sus propios bordes, incluso dentro de un globo grande.
- El texto vuelve a ajustarse al soltar la caja, con la misma geometría en el lienzo y la exportación.
- La detección del globo completo sigue disponible para cajas OCR que todavía no se han ajustado manualmente.

## 0.2.2

- Detección del globo completo alrededor de cajas OCR pequeñas, con margen seguro y uso de la misma geometría al exportar.
- Recomposición en vivo durante la edición con doble clic, aviso de desbordamiento y conservación de deshacer y rehacer.
- Escalado de caja y texto con Ctrl al arrastrar una esquina; edición y controles de redimensionado compatibles.
- Herramientas de texto reorganizadas y estilos de diálogo adaptativos por defecto, respetando ajustes manuales previos.

## 0.2.1

- Actualización de estados en la lista de páginas sin volver a aplicar estilos a insignias que no cambiaron.
- Cola de miniaturas visibles reutilizada mientras se cargan las páginas, y recalculada al desplazarse, filtrar o redimensionar la lista.
- Tamaño de archivo tomado de los metadatos ya obtenidos durante el escaneo del capítulo, sin otra consulta al disco desde la interfaz.
- Notas de versión explícitas en GitHub Releases, publicadas automáticamente junto al instalador y `SHA256SUMS.txt`.

## 0.2.0

- Instalador para Windows con comprobación y aplicación de actualizaciones desde GitHub Releases.
- Interfaz en español e inglés, reorganización de herramientas y progreso de operaciones.
- OCR por cajas y por capítulo, traducción con proveedores configurables y control del gasto de OCR.
- Mejoras de detección de texto, limpieza de globos y revisión visual de la limpieza.
- Distribución de marcas de agua a lo largo de un capítulo continuo.
- Importación PSD/PSB, exportación a resolución original y autoguardado.
