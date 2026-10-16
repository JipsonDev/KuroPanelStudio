# Distribución de marcas de agua

El editor trata las páginas ordenadas como una tira continua: suma sus alturas,
calcula una sola distribución para el capítulo y convierte las coordenadas a
cada archivo. La separación mínima se mide entre los bordes de las marcas,
incluso cuando están en páginas distintas. No se carga una imagen gigante.

El reparto anterior reiniciaba la cantidad y los márgenes en cada página. La
protección local de las uniones no garantizaba la separación solicitada entre
archivos. Ahora solo hay márgenes exteriores del capítulo; las uniones impiden
que un logo se corte, pero no reinician la separación ni la alternancia.

## Uso

1. Abre **Configuración → Marca de agua** y selecciona un PNG.
2. Activa **Repartir por el capítulo completo**.
3. Elige **Columna alineada**, **Alternar izquierda y derecha** o
   **Adaptar al espacio libre**. El último busca otro lado en la misma zona
   cuando el texto bloquea la ubicación preferida.
4. Ajusta la separación mínima, en píxeles de la página original, y la cantidad
   automática o manual **para el capítulo completo**. En automático, **Distancia
   entre centros** controla la densidad; cero conserva el cálculo según dimensiones.
   La columna usa la cuadrícula de alineación visible;
   el modo alternado empieza en el lado seleccionado, o a la derecha si se centra.
5. Activa **Evitar cajas de texto detectadas** para proteger esas regiones.
   Se aplica también cuando hay una sola marca. Requiere cajas detectadas;
   no analiza automáticamente el dibujo ni ejecuta OCR.
6. Para corregir un proyecto con posiciones antiguas, pulsa **Redistribuir todo
   el capítulo** y **Todo el capítulo**. Esto reemplaza las posiciones manuales.
   **Redistribuir esta página** solo elimina las posiciones manuales de esa página;
   la vuelve a incorporar al cálculo global.

Si no hay espacio suficiente, se limita la cantidad por capacidad. Cada marca
conserva su zona del capítulo y solo se mueve dentro de un cuarto de su intervalo
normal. Si esa zona está bloqueada, se omite la marca sin trasladarla a otra página.
Una página completamente bloqueada puede quedar sin marcas. El estado muestra
la cantidad visible; los ajustes manuales pueden reducirla también.

Las posiciones manuales válidas se conservan hasta redistribuirlas; pueden
incumplir la separación entre sí. Si una página contiene posiciones duplicadas,
superpuestas o fuera de sus límites, se reconstruye su reparto automáticamente
al visualizar o exportar; no se modifica el archivo original del proyecto. El reparto automático reserva espacio alrededor de ellas.
Las páginas desactivadas siguen aportando su altura, pero no reciben marcas.
Sin repetición se mantiene la colocación individual anterior.

En la reproducción de tres páginas de 800 × 1000 px, cuatro marcas solicitadas
con separación mínima de 500 px producían antes seis marcas (dos por página)
y un hueco mínimo entre archivos de 240 px. El nuevo cálculo produce cuatro
marcas en total y un hueco mínimo de 690 px.

![Antes y después del reparto continuo](images/chapter-watermarks.png)

## Detalles y validación

- `core/chapter_watermarks.py` calcula el reparto global, las zonas libres por
  página y las coordenadas locales. Vista previa, exportación individual y
  exportación del capítulo usan el mismo plan.
- `core/watermark_layout.py` trabaja con intervalos libres y una pasada inversa
  que reserva espacio. No recorre píxeles ni utiliza servicios externos.
- `core/watermark_manager.py` limita la cantidad por capacidad, respeta la
  separación mínima y ajusta logos demasiado grandes o girados cuando está
  activado **Mantener dentro de la página**.
- El lienzo y la exportación utilizan las mismas coordenadas y límites verticales.
  Durante la carga, se usa la altura original de la escena, no la de la miniatura:
  esta última podía comprimir todas las marcas contra el borde inferior del
  recorte inicial, cerca del comienzo de la página larga.
- Un clic para seleccionar una marca ya no guarda toda la página como posiciones
  manuales; solo se guardan al desplazar una marca o eliminarla.
- Mover marcas manualmente y cambiar después la opacidad conserva sus posiciones.
- El panel muestra tamaño, alineación y distribución. Márgenes y apariencia
  quedan plegados; los botones de aplicación permanecen fijos abajo.

En la verificación inicial pasaron **367 pruebas y 8 subpruebas**, incluidas 26 regresiones del
reparto entre páginas y las 13 comprobaciones anteriores de
capacidad, regiones bloqueadas, alternancia, márgenes extremos, logos girados,
preservación de movimientos manuales y coincidencia entre lienzo y exportación.
Las ocho regresiones más recientes cubren miniaturas de páginas largas, zonas
bloqueadas, desplazamientos extremos, recuperación de posiciones inválidas y
selección sin guardar un reparto manual.
Persisten cuatro mensajes de Qt al cerrar sobre un slot de `CanvasView`, ya
presentes antes del cambio, sin fallos de pruebas.

```powershell
python -m pytest tests/test_chapter_watermarks.py tests/test_watermark_distribution.py -q
python scripts/preview_watermarks.py
python scripts/preview_chapter_watermarks.py
```

El script genera ejemplos sintéticos y una captura del diálogo Qt en
`graphify-out/ui-preview/`, sin cargar documentos del usuario ni llamar a APIs.


## Mejoras de 0.2.15 local

## Tamaño y posición

«Medir solo el logo visible» recorta únicamente píxeles completamente
transparentes antes de medir el ancho. No modifica el PNG original. Se puede
desactivar para conservar un margen transparente intencional. Un PNG sin
contenido visible no genera marcas invisibles.

El porcentaje se refiere al ancho de cada página; el modo píxeles conserva el
ancho solicitado sujeto a los límites de la página. En reparto por capítulo,
el ajuste de altura usa como referencia la página más alta habilitada del mismo
ancho, evitando tamaños distintos al cortar una tira. Si una página es demasiado
corta para ese logo, no se coloca una miniatura. La rotación sigue incluida en
los límites de tamaño y colocación. Las páginas de anchos distintos pueden
conservar tamaños distintos en el modo porcentaje.

El tamaño final mostrado incluye la rotación y los límites de ajuste. La cuadrícula
selecciona la alineación. Márgenes, desplazamientos y apariencia quedan en un
grupo desplegable separado. Los cambios numéricos se confirman con Enter o al
abandonar el campo, evitando recalcular el capítulo con cada dígito intermedio.

## Densidad y obstáculos

«Distancia entre centros» determina una cantidad aproximada en modo automático:
un capítulo de 10.000 píxeles con distancia de 2.500 produce cuatro posiciones
ideales. Cero utiliza el cálculo automático anterior. La distancia puede variar
para conservar un logo entero, evitar texto o satisfacer el espacio mínimo.
La cantidad manual sigue siendo para el capítulo completo.

«Separación mínima» mide el espacio libre entre el final de un logo y el
principio del siguiente, incluidos los cambios de página.

En «Adaptar al espacio libre», cada marca conserva su zona vertical. Primero
busca en el lado seleccionado y después prueba otras columnas dentro de esa
misma zona. Si ninguna posición cabe, se omite; no se traslada a una zona vecina
ni se apila con sus marcas. La protección utiliza cajas de texto detectadas:
no reconoce caras, manos ni otros dibujos. El resultado necesita revisión visual.

Las posiciones manuales prevalecen. Para sustituirlas se utiliza «Redistribuir
todo el capítulo». La vista previa y la exportación consumen el mismo plan y
la misma preparación del PNG.

## Verificación de 0.2.15 local

El 16 de octubre de 2026 pasó la suite completa: **513 pruebas y 11 subpruebas**.
Los 17 casos nuevos incluyen PNG con margen transparente, PNG vacío, páginas
cortas, densidad en capítulos con cortes desiguales, cambio de columna al evitar
texto, información de la interfaz y traducciones nuevas al inglés.

Tres comparaciones rasterizan el lienzo y componen la imagen final con opacidad
del 65 % y rotaciones de 0°, 30° y 90°. La diferencia máxima admitida y observada
en esas pruebas es un nivel por canal. Se usaron imágenes controladas; no se
evaluó un capítulo concreto ni el PNG original del usuario.
