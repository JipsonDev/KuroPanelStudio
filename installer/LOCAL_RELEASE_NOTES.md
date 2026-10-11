# KuroPanel Studio @VERSION@

## Composición de texto al exportar

- Corregido el texto más pequeño al guardar: la exportación conserva el tamaño, las líneas y el centrado que muestra el lienzo.
- Las fuentes de manga y sus letras acentuadas se dibujan con el mismo motor tipográfico de la interfaz, también durante la exportación en segundo plano.
- Corregida la posición de las máscaras de texto en PSD y el recorte cuando la composición ocupa más espacio que la caja inicial del OCR.
- Para un proyecto anterior, abre la página en el lienzo y vuelve a exportarla para incorporar sus medidas finales.
- Verificación: 496 pruebas y 11 subpruebas aprobadas, incluidas 21 comparaciones nuevas de composición.

## Pegado de guion y exportación de imágenes

- Guión actualiza las tarjetas existentes y conserva el cursor, el foco y deshacer cuando el contenido no cambia. Evita reconstruir toda la lista con cada refresco.
- La cola conserva su selección si no ha cambiado. Los párrafos largos tienen una vista previa corta y una ayuda emergente con el texto completo.
- Pegar o editar una traducción muestra una vista previa inmediata en el lienzo. La composición final y los efectos se agrupan tras 180 ms sin nuevas ediciones.
- Corregido el error `attempt to get argmax of an empty sequence` al guardar imágenes con cajas movidas fuera de un borde. Se conserva el tamaño de la caja y se recorta el dibujo al tamaño de la página.
- Exportación verificada en PNG, JPG, WebP y PSD. Con 500 diálogos, el refresco del panel sin cambios bajó de unos 872 ms a 8 ms en la medición local.
- Verificación de esta compilación: 475 pruebas y 11 subpruebas aprobadas juntas. El cierre nativo de Qt observado de forma intermitente en pruebas anteriores sigue sin una causa confirmada.

## Limpieza de halos y conservación del dibujo

- La máscara recupera motas grises y halos de compresión muy próximos a letras confirmadas, también sobre fondos planos coloreados y oscuros. Conserva las marcas alejadas, las líneas largas y el margen de selección.
- El color de fondo se estima excluyendo el texto ya enmascarado, para evitar errores con diálogos densos.
- La corrección de degradados después de LaMa exige más evidencia del contexto. Si una ilustración cruza el texto, conserva la reconstrucción neuronal cuando el fondo no es suficientemente uniforme.
- En la subzona blanca real anotada, los residuos grises bajaron de 87 píxeles a 0 frente a la compilación anterior. Las siete zonas mantienen cero residuos oscuros según el criterio de contraste anterior; quedan irregularidades en un borde gris superpuesto y los resultados necesitan revisión visual.
- La app ya usa LaMa especializado en manga; estas mejoras no descargan más modelos ni consumen créditos de API. La caché de máscaras se recalcula con versión 44.
- Verificación: 452 pruebas y 11 subpruebas aprobadas; las siete pruebas de idioma aprobaron por separado. La ejecución conjunta presenta todavía un cierre nativo de Qt en una prueba de idioma, incluso con preferencias temporales. La causa no está resuelta.

## Detección, consumo OCR y restos de letras

- Una segunda búsqueda local revisa los globos cerrados con tinta que no quedaron cubiertos por las cajas iniciales. Solo esas zonas pasan de nuevo por YOLO, a una escala más útil para letras pequeñas.
- Los bloques grandes recortados por el borde se conservan cuando los respaldan al menos tres líneas alineadas sobre fondo uniforme. En el globo dentado de prueba se recupera la cuarta línea que antes desaparecía del marcado.
- El OCR recorta márgenes uniformes conservando trazos tenues y puntuación, y evita ampliar innecesariamente las imágenes habituales. Las letras pequeñas y los diálogos muy verticales mantienen sus protecciones de resolución.
- Dos recortes idénticos comparten una sola petición, incluso entre páginas o después de reiniciar. La relectura explícita sigue haciendo una petición nueva.
- Las respuestas cortadas por el límite de salida se muestran como error, no se guardan como una lectura completa ni se repiten automáticamente.
- La limpieza completa también pequeños restos grises, coloreados o claros sobre fondo oscuro cuando pertenecen a una letra ya casi cubierta por la máscara. Rechaza textura y componentes aislados.
- La validación incluye cinco recortes reales y siete globos anotados. Cero residuos oscuros en las zonas de texto medidas; el resultado puede necesitar retoques de dibujo superpuesto.

## OCR, limpieza y revisión visual

- El OCR conserva más ancho de carácter en cajas de diálogo muy verticales. El recorte sigue sujeto al límite de píxeles del perfil para controlar el consumo de Alibaba.
- La detección de residuos tras limpiar procesa solo el entorno de cada componente de texto. Mantiene la misma máscara protegida y evita repetir cálculos sobre toda la página.
- El control de calidad separa navegación y acciones para que los botones no queden amontonados. Las tres vistas se reajustan al cambiar el tamaño de la ventana usando los píxeles originales.

## Menú de caja y palabras largas

- Clic derecho en una caja abre **Tipo de globo** con Automático, Diálogo, Grito y Cuadro; la selección afecta solo a esa caja.
- El mismo menú permite activar o desactivar **Separar palabras largas con guion**. Está activo por defecto para cajas nuevas y también aparece en las herramientas de Texto.
- Si una palabra no tiene cortes silábicos reconocibles y no cabe, el compositor puede partirla con un guion al final de la línea. El texto original no cambia y los saltos de línea escritos manualmente se respetan.

## Tipos de globo y fuentes por caja

- En **Texto > Tipo de globo**, cada caja puede marcarse como **Diálogo**, **Grito** o **Cuadro**, o dejarse en **Automático**.
- La detección automática analiza el contorno cerrado: ovalado/circular para diálogo, puntiagudo para grito y rectangular para cuadro. Si no hay suficiente evidencia, usa diálogo y permite corregirlo manualmente.
- Cada tipo toma su fuente del rol **Diálogo**, **Gritos** o **Narrador/Cuadro** del proyecto. Sin un rol configurado, aplica una fuente predeterminada para ese tipo.
- Una fuente o categoría elegida manualmente permanece intacta al volver a detectar o procesar la página.
- Las cajas de texto tienen ahora un borde morado más grueso; la selección y los tiradores se distinguen mejor sobre el dibujo.

## Ajuste de texto y Guión

- Un clic en una caja ya no registra una edición ni modifica su composición.
- Al mover una caja, los saltos de línea, el tamaño y la posición relativa del texto se conservan; la composición guardada también se usa al volver a abrir la página y al exportar.
- En **Guión**, la lista de traducciones pendientes permanece visible mientras se crean cajas. El texto pegado sigue disponible para consulta.
- **Ctrl+Z** al crear una caja restaura la caja y su traducción a la cola, y la lista se actualiza inmediatamente.

## Actualizaciones locales

- **Buscar actualizaciones** detecta instaladores locales recientes registrados por el compilador, verifica su tamaño y SHA-256, y permite instalarlos sin publicarlos en GitHub.
- Para pasar desde la versión 0.2.3 a esta versión, ejecuta este instalador una vez. Las futuras compilaciones locales aparecerán en el botón.

## Redimensionado en tiempo real

- Al arrastrar una esquina, el texto se recompone dentro de la caja mientras se mueve el puntero, incluso cuando está activado el ajuste al globo.
- La vista previa usa una composición ligera durante el gesto; al soltar, se aplica el cálculo final del contorno y los efectos de texto.

## Traducciones en cola para cajas nuevas

- En **Guión**, pega varias traducciones en el campo **Traducciones para cajas nuevas** y pulsa **Preparar cola**.
- Acepta entradas numeradas o párrafos separados por una línea vacía. Los números de lista no aparecen en el texto del globo.
- Cada caja nueva recibe la siguiente traducción. Las cajas anteriores conservan su contenido.
- La cola muestra cuántas entradas quedan y se guarda con el proyecto.

Esta es una compilación local; no se publicó como actualización automática.
