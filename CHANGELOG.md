# Historial de cambios

## Sin publicar — protección de globos radiales

- Una comprobación final del interior evita que la recuperación automática de letras y halos vuelva a seleccionar rayos y puntas del globo.
- Conservación de bordes discontinuos y de globos cortados por el marco de la imagen, sin modificar el pincel ni las máscaras revisadas manualmente.
- Caché de máscaras versión 45 y regresiones con tres coronas sintéticas y dos selecciones de un ejemplo real conservado fuera del repositorio.
- Verificación: 528 pruebas aprobadas, 6 omitidas y 11 subpruebas. En el ejemplo real, los píxeles del dibujo modificados pasan de 3691 a 0 en una selección amplia, eliminando todo el texto oscuro anotado.

## Sin publicar — posiciones de marcas de agua

- La exportación conserva las posiciones manuales al desactivar «Mantener dentro de la página», incluidas coordenadas negativas y marcas parcialmente fuera de los bordes.
- Mover marcas de una página conserva la distribución y alternancia de las otras. Solo se omiten las marcas automáticas que entran en conflicto con el espacio reservado por una manual.
- Los cambios de marcas de agua se detectan como cambios del documento y participan en deshacer, rehacer y autoguardado.
- Regresiones de planificación, historial y exportación real de capítulos comparada píxel a píxel con el lienzo.

## 0.2.15 — tamaño y distribución de marcas de agua

- El tamaño y la alineación se calculan sobre el contenido visible del PNG; se puede conservar el borde transparente desactivando «Medir solo el logo visible».
- Tamaño uniforme entre páginas del mismo ancho en un capítulo. Los cortes demasiado cortos quedan sin marca en lugar de recibir una miniatura.
- Control de distancia aproximada entre centros para la cantidad automática, independiente del espacio libre mínimo entre logos.
- Modo «Adaptar al espacio libre»: busca otro lado dentro de la misma zona cuando el texto bloquea la ubicación elegida, conservando la separación continua entre páginas.
- Tamaño y alineación accesibles sin desplegar opciones avanzadas, resumen de dimensiones finales y total de marcas del capítulo, y textos nuevos en español e inglés.
- Verificación local: 513 pruebas y 11 subpruebas aprobadas; incluye 17 casos nuevos de geometría, reparto, interfaz y coincidencia entre lienzo y exportación.

## 0.2.14 — composición al exportar

- La exportación conserva las medidas, líneas, centrado y posición finales del lienzo. Corrige el texto más pequeño causado por interpretar puntos tipográficos como píxeles.
- El documento tipográfico del lienzo se dibuja con Qt al exportar, conservando el espaciado y los caracteres de respaldo de las fuentes de manga, también en segundo plano.
- PNG, JPG, WebP y PSD comparten la composición guardada. PSD coloca juntos los píxeles y su máscara y conserva texto que excede la caja inicial del OCR.
- Los proyectos antiguos incorporan las medidas finales cuando se vuelven a mostrar sus páginas en el lienzo. Sin un documento del lienzo válido, se mantiene la composición de respaldo.

## 0.2.13 — limpieza, pegado de guion y exportación

- Las tarjetas de Guión se actualizan sin recrear todos los editores. Conservan el cursor, el foco y el historial de deshacer cuando su contenido no cambia.
- La cola conserva su selección en los refrescos sin cambios y limita el texto de las vistas previas; la traducción completa sigue disponible en el proyecto y en la ayuda emergente.
- Al editar o pegar una traducción, el lienzo muestra una vista previa inmediata y agrupa la composición final tras 180 ms sin nuevas ediciones.
- Corregido `attempt to get argmax of an empty sequence` al exportar cajas que cruzan los bordes de la página. La composición conserva el tamaño solicitado y el resultado se recorta al tamaño de la imagen.
- Pruebas de exportación PNG, JPG, WebP y PSD con cajas parcialmente y completamente fuera de página, y de conservación del cursor, deshacer, filtros y cola.
- Medición local con 500 diálogos: refresco sin cambios de aproximadamente 872 ms a 8 ms. Es una medición del panel, no de toda la edición del capítulo.
- Verificación: 475 pruebas y 11 subpruebas aprobadas juntas. Este resultado no demuestra que el cierre nativo intermitente de Qt observado en ejecuciones anteriores esté resuelto.
- Publicación para Windows mediante GitHub Releases, con las mejoras de limpieza de 0.2.12 y las de detección y consumo OCR de 0.2.11 que hasta ahora se distribuían en compilaciones locales.

## 0.2.12 — limpieza de halos, compilación local

- Recuperación de motas y halos grises junto a letras confirmadas, en fondos planos blancos, coloreados y oscuros, sin extender la máscara a marcas alejadas o líneas largas.
- Estimación del fondo sin incluir texto ya enmascarado y protección del margen de selección y del dibujo próximo.
- Corrección de degradados tras LaMa más conservadora para preservar reconstrucciones de ilustraciones que cruzan el texto.
- Caché de máscaras versión 44 y evaluación adicional de contraste tenue en zonas de fondo plano anotadas. En una subzona real, residuos grises de 87 a 0; las siete zonas mantienen cero residuos oscuros con el criterio anterior.
- Revisión de herramientas de GitHub y confirmación de que el modelo LaMa incluido ya es la variante especializada en manga. Sin nuevas llamadas API, pesos ni dependencias.
- Verificación: 452 pruebas y 11 subpruebas aprobadas juntas; las siete pruebas de idioma aprobaron por separado. Persiste el cierre nativo de Qt al reunirlas en el mismo proceso, incluso con perfil temporal; documentado en `docs/processing-performance.md`.

## 0.2.11 — mejoras disponibles en el código fuente y compilación local

- Segunda búsqueda local de texto en globos cerrados que quedaron sin marcar, con una revisión de YOLO a mayor escala solo en esas zonas.
- Conservación de bloques grandes respaldados por líneas alineadas; corrige el globo de prueba cuya cuarta línea desaparecía del marcado.
- OCR con márgenes uniformes recortados, menos ampliaciones innecesarias y reutilización de recortes idénticos entre cajas, páginas y reinicios. Conserva resolución para diálogos muy alargados.
- Detección de respuestas OCR truncadas, sin guardarlas como lecturas completas ni repetir automáticamente la solicitud.
- Limpieza de pequeños restos grises, coloreados y claros sobre fondos oscuros cuando pertenecen a caracteres ya enmascarados, con protección de textura y contornos.
- Cálculo de residuos limitado al entorno de cada componente, para evitar procesar repetidamente toda la imagen.
- Revisión visual con navegación y acciones separadas; las vistas original, máscara y resultado se reajustan al redimensionar la ventana.
- Evaluación de limpieza con recortes originales guardados y pruebas de cobertura, protección del dibujo y consumo OCR mediante API simulada.
- Verificación del 11 de octubre de 2026: 448 pruebas y 11 subpruebas aprobadas con configuración temporal de usuario. La ejecución con el perfil habitual presentó un cierre nativo de Qt en la prueba de idioma; la reproducción y los comandos se documentan en `docs/processing-performance.md`.

## 0.2.9

- Clasificación automática y selección manual del tipo de globo por caja: diálogo, grito y cuadro, con las fuentes configuradas para esos roles.
- Menú de clic derecho para cambiar el tipo y la separación de palabras largas con guion; el texto original conserva su contenido y saltos de línea.
- Composición estable al tocar o mover cajas, redimensionado en vivo y borde morado más visible.
- Cola de traducciones visible en Guión, con devolución de entradas al deshacer la creación de una caja.
- Canal de actualización local para instaladores generados en el equipo, además de GitHub Releases.

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
