# Limpieza de selecciones grandes a resolución original

LaMa procesa las selecciones grandes por recortes para evitar enviar una imagen
de dimensiones ilimitadas al modelo. La máscara, las capas y el flujo de
revisión y exportación mantienen su comportamiento habitual.

## Procesamiento

- Cada inferencia recibe como máximo 1024 × 1024 píxeles espaciales.
- Cada núcleo de hasta 896 × 896 incluye hasta 64 píxeles de contexto por lado.
- Los núcleos vacíos se omiten y los dispersos se ajustan a su máscara.
- Se conserva la resolución original, sin redimensionar la imagen.
- Se escriben únicamente los píxeles seleccionados de cada núcleo. El contexto
  siempre procede de la imagen original, sin reutilizar resultados de otros
  recortes.
- Las máscaras vacías devuelven una copia sin ejecutar el modelo.
- Los trabajos grandes se procesan por separado antes de agrupar los pequeños,
  tanto en la ruta CPU como en la ruta de lotes CUDA.

El relleno reflejado a múltiplos de ocho se aplica a cada recorte antes de la
inferencia. El límite del pincel que rechaza saltos accidentales demasiado
grandes sigue activo.

El límite espacial acota cada inferencia; no garantiza un consumo total fijo,
que también depende del modelo, proveedor, lotes e imagen en memoria. Tampoco
garantiza reconstrucciones idénticas a las de una sola inferencia con toda la
imagen, ya que el contexto disponible cambia.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cleaning_tiles.py tests/test_stroke_engine.py -q
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q
```

Las regresiones de `tests/test_cleaning_tiles.py` verifican la cobertura
completa de selecciones grandes, los bordes de los recortes, el límite espacial,
la conservación de píxeles ajenos a la máscara y las máscaras vacías. También
comprueban que la planificación de trabajos CPU/CUDA respete el límite al mezclar
imágenes grandes y pequeñas.

Estas pruebas sustituyen únicamente la inferencia ONNX por un doble
determinista. No equivalen a una prueba con hardware CUDA real. La validación
manual incluyó LaMa real en CPU con una imagen sintética de 1337 × 1253 y una
prueba del runtime empaquetado: ambas conservaron los píxeles fuera de la máscara.
No se establece superioridad visual sobre otros motores ni se sustituye una
comparación con páginas reales.

## Referencia

Se estudió [manga-cleaner](https://github.com/NeTRuNNeRGLiTCH/manga-cleaner),
`python-main`, commit `ee7ac94`, como referencia de comportamiento. KuroPanel ya
utilizaba LaMa ONNX, máscaras OCR y relleno reflejado a múltiplos de ocho.
La implementación añadida es propia; no se incorporaron archivos ni código del
repositorio de referencia.
