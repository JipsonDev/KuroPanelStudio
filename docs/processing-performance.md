# Procesamiento y detección: mejoras verificadas

Comparación local contra `c82693de74ebd12e1172ae582abddf30a77a60fd`
(`origin/main` al clonar), el 7 de septiembre de 2026.

## Cambios

- El detector cubre la página con franjas completas y ancla la última al borde
  inferior. Evita procesar una franja extra cuando la anterior ya cubre toda la
  página y evita cambiar drásticamente la escala del texto en una cola pequeña.
- La supresión de duplicados usa operaciones NumPy y mantiene los umbrales
  anteriores: IoU mayor que 0,35 o contención de la caja débil mayor que 75 %.
  Las pruebas comparan sus resultados con el algoritmo original, incluidos
  empates de confianza y cajas contenidas.
- ONNX prepara directamente el tensor de entrada, respeta dimensiones y lotes
  fijos del modelo, descarta coordenadas no finitas y cajas sin área, y devuelve
  únicamente las predicciones de las imágenes solicitadas.
- El respaldo PyTorch recibe canales BGR, según el
  [contrato de entrada de Ultralytics](https://docs.ultralytics.com/modes/predict).
  La ruta ONNX conserva RGB.
- DirectML configura las opciones que exige
  [ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
  y se identifica correctamente en el indicador de ejecución.
- OCR consulta la caché antes de abrir la imagen y prepara nuevos recortes
  mientras hay solicitudes en curso. La cantidad de JPEG y tareas pendientes
  queda limitada por la concurrencia activa. Conserva una solicitud por caja
  y el orden original de los resultados, aunque las respuestas lleguen mezcladas.
- La concurrencia puede disminuir durante una operación si el proveedor se
  ralentiza; el perfil de recursos bajos conserva su máximo de dos solicitudes.
  La calidad del perfil forma parte de la clave de caché.
- La normalización conserva palabras como `begin` y `end` dentro del diálogo.
  Las etiquetas de transporte en líneas independientes siguen eliminándose.
- La cancelación detiene la preparación y el envío de nuevas cajas. Una llamada
  HTTP ya iniciada puede tardar hasta su timeout en terminar; no se interrumpe
  forzosamente un hilo que está usando la conexión.

## Medición

Windows 11, Python 3.14.0, ONNX Runtime 1.29.0, NumPy 2.5.3, OpenCV 5.0.0.
CPU con dos hilos de inferencia. Mediana de tres ejecuciones, modelo previamente
calentado y caché del detector vaciada antes de cada medición completa.

| Operación | Original | Modificado | Cambio |
| --- | ---: | ---: | ---: |
| Detección ONNX, página de 800×3700 | 473,8 ms | 311,2 ms | 34 % menos tiempo |
| Supresión de 1200 candidatos | 268,5 ms | 56,2 ms | 4,8 veces más rápida |
| Preparación hasta la primera solicitud OCR | 181,4 ms | 8,1 ms | 95,5 % menos tiempo |
| OCR de 156 cajas, transporte simulado | 593,4 ms | 473,6 ms | 20 % menos tiempo |
| OCR de 156 cajas ya en caché | 6,74 ms | 0,44 ms | 93,5 % menos tiempo |

La página sintética contiene 13 diálogos negros y coloreados: ambas versiones
devuelven 13 cajas y cubren el centro de los 13 diálogos. Ambas conservan los
mismos 1117 candidatos en la prueba de supresión. Esto verifica esta muestra;
no demuestra una mejora general de precisión en manga, CJK o texto estilizado.
No se han cambiado ni reentrenado los pesos del detector.

El benchmark OCR usa respuestas locales con 10 ms de latencia simulada. No usa
credenciales, no envía imágenes y no mide la latencia ni la precisión de Alibaba.
Los resultados GPU y la precisión OCR con un proveedor real requieren validación
con ese hardware y páginas representativas. La aceleración de franjas depende
de la altura: en páginas donde se mantiene su número, el ahorro será menor.

## Reproducir

Desde la raíz del repositorio, con las dependencias y los modelos LFS instalados:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/benchmark_processing.py --baseline-ref c82693de74ebd12e1172ae582abddf30a77a60fd --output benchmark.json
```

`--baseline-ref` ejecuta el código de esa revisión local: debe ser una revisión
de confianza. Sin ese argumento solo se mide el código de trabajo actual.

Se añadieron 28 casos de regresión. Resultado final: **253 pruebas y 8 subtests
aprobados**. La suite UI emite cuatro mensajes Qt `Slot 'CanvasView::' not found`
al finalizar, también presentes en las 225 pruebas de la revisión original.

## Verificación del repositorio — 11 de octubre de 2026

La ejecución completa en Windows con Python 3.14 aprobó **448 pruebas y 11
subpruebas** usando una carpeta temporal para `LOCALAPPDATA`. Incluye los
recortes reales disponibles en este equipo y las nuevas pruebas de detección,
recorte OCR, reutilización de peticiones y fragmentos de limpieza.

Con el perfil habitual, dos ejecuciones completas se interrumpieron por una
violación de acceso nativa de Qt al procesar eventos en
`test_runtime_language_switch_keeps_stable_filter_ids_and_user_text`. Las siete
pruebas de idioma aprobaron por separado, y las otras 441 pruebas y 11
subpruebas aprobaron juntas. Usar la carpeta temporal permitió ejecutar la suite
completa; eso no demuestra que la causa del cierre esté corregida en la app.

Para verificar sin mezclar las preferencias y archivos del usuario:

```powershell
$taskPreviousData = $env:LOCALAPPDATA
$taskTestData = Join-Path $env:TEMP ('kuro-repo-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskTestData | Out-Null
try {
    $env:LOCALAPPDATA = $taskTestData
    .\.venv\Scripts\python.exe -m pytest -q
} finally {
    $env:LOCALAPPDATA = $taskPreviousData
}
```

### Verificación posterior de limpieza 0.2.12

El 11 de octubre de 2026 se aprobaron **452 pruebas y 11 subpruebas** con
`--ignore=tests/test_ui_language.py`; las siete pruebas de idioma aprobaron en
un proceso separado. La ejecución conjunta volvió a cerrar Qt en el mismo
`app.processEvents()` de la prueba de idioma, esta vez también con
`LOCALAPPDATA` temporal. Por tanto, un perfil temporal no elimina de forma
fiable ese cierre nativo. No se ha corregido ni atribuido a una causa concreta.

La nueva limpieza se verificó además ejecutando OCR-mask y LaMa locales sobre
cinco recortes reales. No se enviaron peticiones a proveedores OCR ni se usaron
credenciales durante esta evaluación. Detalles y límites de las mediciones en
[balloon-cleaning.md](balloon-cleaning.md).

### Guión y exportación 0.2.13

`ScriptPanel.set_regions()` reconstruía todas las tarjetas, aunque la lista no
hubiera cambiado. La actualización ahora reutiliza los editores por identificador
y solo sustituye tarjetas cuyo texto OCR o rol haya cambiado. Conserva el cursor y
deshacer de los demás. La cola tampoco se reconstruye cuando sus textos coinciden,
y las vistas previas de párrafos largos se acortan sin modificar el texto guardado.

Con Qt en modo `offscreen`, 500 tarjetas tardaban aproximadamente **872 ms** en
refrescarse sin cambios. Después de la corrección, la mediana de cinco refrescos
fue **8 ms**; para 100 tarjetas, **0,7 ms**. Pegar 500 entradas en el campo de cola
tardó **1,3 ms** y crear sus elementos de lista **2,8 ms**. Son mediciones locales
del panel; la creación inicial de las 500 tarjetas sigue siendo más costosa y
estas cifras no representan la composición ni la exportación de un capítulo.

La edición individual del guion actualiza inmediatamente el modelo y la vista
previa del lienzo. La composición del contorno y los efectos se aplazan hasta
180 ms después de la última edición y solo afectan a los índices pendientes.

El fallo de exportación se reprodujo con una caja `(-12, 30, 100, 50)` en una
página de 180 × 180: un recorte NumPy con coordenada negativa generaba una máscara
de 50 × 0. El cálculo de límites llamaba `argmax` sobre un eje vacío. Las cajas
que cruzan el borde ahora conservan su máscara rectangular completa, y el
compositor recorta el resultado a la página. Las máscaras vacías también devuelven
un resultado de desbordamiento que conserva el texto, sin lanzar esa excepción.

Verificación del 11 de octubre: **475 pruebas y 11 subpruebas aprobadas juntas**
con `.venv/Scripts/python.exe -m pytest -q`. Incluye cuatro formatos de exportación
(PNG, JPG, WebP y PSD), cuatro bordes de página y las regresiones de edición del
guion. Esta ejecución pasó también las pruebas de idioma; no se ha identificado
ni corregido la causa del cierre nativo intermitente de Qt registrado más arriba.

### Composición visible y exportación 0.2.14 local

El lienzo configuraba fuentes en puntos; Pillow recibía el mismo número como
píxeles. A 96 DPI, 24 puntos corresponden a 32 píxeles. La exportación volvía
a medir las líneas y podía cambiar su espaciado, posición y caracteres de
respaldo al usar fuentes de manga con cobertura parcial de acentos.

El lienzo guarda ahora el documento y sus medidas finales junto a cada región,
con una firma que invalida esos datos al cambiar el texto, estilo o dimensiones.
Mover una caja conserva su composición y traslada el origen relativo.
La exportación rasteriza el documento con Qt sobre QImage, sin crear widgets
en el trabajador. Conserva puntos, contornos, pinceles, espaciado y fuentes de
respaldo. Los datos se conservan también al guardar y abrir un proyecto.
En exportaciones sin aplicación Qt o sin documento vigente, se usa el motor
de respaldo con la conversión de puntos a píxeles corregida.

PSD recibe el resultado del mismo compositor. Su constructor coloca juntos el
contenido y la máscara; los límites incluyen el texto fuera de la caja inicial
del OCR, recortados únicamente por los bordes de la página.

Las pruebas comparan los píxeles de los glifos en ambas direcciones, tolerando
hasta cuatro píxeles por redondeo y rasterización. Cubren alineación, interlineado,
escala, movimiento, formatos, proyecto guardado, documentos invalidados,
trabajador, contornos y subrayado con degradado. Tres pruebas adicionales usan
fuentes de manga instaladas en este equipo y se omiten si no están disponibles.
No se ha validado el proyecto original de las capturas del usuario; se reprodujo
su texto con fuentes instaladas y páginas controladas.

Verificación del 11 de octubre de 2026: **496 pruebas y 11 subpruebas aprobadas**
con `.venv/Scripts/python.exe -m pytest -q`, incluidas 21 comparaciones nuevas
de composición. Este resultado no atribuye ni resuelve el cierre nativo
intermitente de Qt documentado en las verificaciones anteriores.
