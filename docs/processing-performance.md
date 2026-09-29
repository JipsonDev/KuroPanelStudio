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
