# Investigación de la referencia 38.0.0

Objetivo: entender el reparto de marcas y la tira continua del ejecutable
facilitado por el usuario para implementar funciones equivalentes en KuroPanel.
La implementación nueva es propia; no se importan módulos del otro editor.

## Identidad y alcance

- Artefacto: `Mahua Suite Editor.exe`, 87 578 328 bytes, en la carpeta
  `ManhuaSuiteEditor-38.0.0/Mahua Suite Editor` indicada por el usuario.
- SHA-256: `1436ebd91c0da05920c2bd72772e9f7a10b5fccfd7a66c6c8a863be5bc0138ed`.
- Evidencia REA `inspect_pe_resources`:
  `ev_72cc2bd7f33724fb4af416fb744821b2f32a370600ee554b052e5fad35e0b4c9`.
  Confirma el archivo PE y su inventario de recursos; **no** demuestra por sí
  sola la lógica Python ni su rendimiento.
- El contenedor PyInstaller declara Python 3.14 y su magia PYZ coincide con
  el intérprete local. Se inspeccionaron nombres, constantes e instrucciones
  de los objetos de código empaquetados mediante el lector de PyInstaller y
  `dis`, sin importar ni ejecutar esos módulos.
- Las funciones y números siguientes son metadatos/instrucciones observados
  estáticamente. No se ejecutó la referencia ni se midió su rendimiento.
  Los resultados locales completos se conservan fuera de Git en
  `local-builds/reference-38/`.

## Hallazgos

| Módulo / función empaquetada | Observación estática |
| --- | --- |
| `src.core.tira_cascada.construir_tira` (línea almacenada 135) | Crea una entrada virtual con páginas, tamaños y offsets; desplaza las coordenadas de selecciones y parches. |
| `TiraPerezosa.pagina` (253) | Abre la página solicitada, emplea `OrderedDict` y `RLock`, actualiza el orden de uso y expulsa la más antigua. `CACHE_PAGINAS` vale 6. |
| `TiraPerezosa.crop` (271) | Recorre las páginas que intersectan el recorte y pega solo sus fragmentos. `copy` y `convert` materializan la imagen completa. |
| `src.ui.tira_lienzo.CargadorTira` | Temporizador de 30 ms, carga a una pantalla de distancia y retención a tres pantallas; trabajos en `QThreadPool`, resultados `QImage` y creación de `QPixmap` en la interfaz. |
| `CargadorTira._al_cargar` (172) | Verifica generación/vigencia y divide las páginas cargadas en pixmaps de hasta 4096 píxeles de alto. |
| `ToolsControllerMixin._refresh_watermarks_on_scene` (487) | Tamaño relativo al ancho; cantidad directa o reglas de rango de altura; izquierda/derecha/alternancia; posiciones manuales por índice. En reparto libre usa centros a múltiplos de `altura // (cantidad + 1)`, restando media altura del logo. |
| `ToolsControllerMixin._on_watermark_moved` (792) | Guarda x/y por marca en `wm_positions` de la imagen y prepara su cambio de historial. |

## Aplicación en KuroPanel

KuroPanel conserva las páginas reales y añade un recorrido continuo virtual;
la edición de texto y limpieza se abre por página mediante doble clic. Las
marcas sí se mueven y borran directamente en la tira, conservando coordenadas
locales por página y su participación en el historial.

La referencia inspira la carga por visibilidad, el trabajo fuera del hilo de
interfaz y la separación entre geometría y píxeles. KuroPanel emplea teselas
1024 × 2048 con detalle adaptado al zoom y un presupuesto de pixmaps de 48 MiB.
Su caché fuente depende del presupuesto o de la mayor página, no de la suma de
las páginas. Las generaciones descartan resultados de capítulos anteriores.

El exportador PNG nuevo escribe bloques de filas de una página a la vez y
reemplaza el destino al terminar. Esto evita materializar el capítulo para
exportar. Conserva el ancho original de cada página, centra anchos diferentes
sobre blanco y no reduce píxeles. La tira no une las selecciones de OCR que
cruzan dos archivos; la edición sigue siendo por página.

La corrección del reparto soluciona un fallo propio reproducido: tres logos
de 50 px con separación de 400 px y márgenes de 24 caben en 1000 px, pero el
paso ideal anterior descartaba el del medio. Ahora se usa la capacidad útil y
un paso compatible con la separación, sin cambiar los repartos holgados ni
las posiciones manuales. También se ordenan naturalmente los archivos nuevos:
`1, 2, 10`, conservando el orden guardado de proyectos existentes.
