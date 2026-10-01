# KuroPanel Studio @VERSION@

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
