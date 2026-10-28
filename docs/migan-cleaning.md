# Limpieza con MI-GAN

MI-GAN y LaMa comparten las máscaras de OCR, su revisión manual, la protección
de contornos y los parches no destructivos. MI-GAN es la opción predeterminada
en configuraciones nuevas; una preferencia LaMa ya guardada se respeta.
La selección está en **Limpiar página → Motor de limpieza → Modelo local** y
se aplica también al pincel de máscara y al procesamiento de capítulos.

Los globos uniformes conservan el relleno sólido protegido, que evita ejecutar
cualquiera de los dos modelos. Cambiar de red no mejora una máscara incorrecta:
las correcciones de puntas y bordes siguen siendo necesarias y permanecen activas.

## Modelo y distribución

- Implementación: [Picsart-AI-Research/MI-GAN](https://github.com/Picsart-AI-Research/MI-GAN).
- Archivo: `Models/migan_pipeline_v2.onnx`, 28 079 181 bytes.
- Exportación enlazada por el proyecto oficial:
  [andraniksargsyan/migan](https://huggingface.co/andraniksargsyan/migan/blob/406830d0fa60666da0071c342ad2fbc8f30c5c64/migan_pipeline_v2.onnx).
- SHA-256: `6f1f3530a1a2324b19752018ce756088b07973cda8d7d890034ace5c8a48c40b`.
- Licencia de los pesos: MIT; se incluye `Models/MI-GAN-LICENSE.txt` tanto en
  el repositorio como en los dos empaquetados de Windows.
- No requiere PyTorch, conexión durante la limpieza ni nuevos paquetes.

El modelo recibe RGB `uint8` NCHW y una máscara `uint8` N1HW: **0 es borrar y
255 es conservar**. El adaptador invierte la máscara del editor, que emplea la
polaridad opuesta. Cada inferencia procesa una imagen: no se usa el eje de lote
dinámico de esta exportación como si fuera el lote de LaMa.

La exportación aplica una mezcla suave en el borde de su agujero. El adaptador
da cuatro píxeles adicionales de trabajo a la red y pega su salida únicamente
dentro de la máscara revisada. El contexto exterior queda idéntico al original.
Para selecciones mayores se usan recortes de hasta 512 × 512, núcleos de 384 y
64 píxeles de contexto por lado. Las zonas sin máscara se omiten; cada recorte
lee la imagen original. El modelo puede redimensionar internamente su contexto
a 512 × 512, pero no se reduce la página completa.

La ejecución permanece en el proceso aislado. El cambio de modelo se comunica
en cada petición y descarga las sesiones anteriores, conservando las máscaras
revisadas y sus cachés. Si falta MI-GAN, se muestra un error: no se cambia de
motor de forma silenciosa.

## Pruebas

`tests/test_migan_cleaning.py` comprueba polaridad, canales y tipos, máscara
manual y automática, orden de resultados, recortes grandes y dispersos,
conservación de píxeles y configuración, ausencia de pesos, descarga de sesiones
y una inferencia real en el proceso aislado. La suite conserva las regresiones
de bordes radiales y marcas de agua.

Los resultados de rendimiento se miden con ambos modelos en CPU, el mismo
perfil de recursos y las mismas imágenes y máscaras. Las cifras de inferencia
del artículo no incluyen el preprocesado y posprocesado de esta exportación
ONNX y no equivalen a la latencia del editor. La rapidez depende del tamaño
del recorte: MI-GAN no es necesariamente más rápido para máscaras pequeñas.

Medición local del 28 de octubre de 2026: Windows, AMD Ryzen 7 7435HS, ONNX
Runtime CPU, perfil equilibrado (2 hilos). Mediana de tres ejecuciones de
`clean_mask` con sesiones calientes, fondo sintético de ondas de color y la
misma máscara de texto. Sin otras pruebas ejecutándose durante la medición.

| Recorte | MI-GAN | LaMa | Resultado |
| --- | ---: | ---: | --- |
| 256 × 256 | 1,51 s | 1,26 s | LaMa más rápido |
| 320 × 480 | 1,55 s | 2,80 s | MI-GAN 1,81 veces más rápido |
| 512 × 512 | 1,57 s | 4,00 s | MI-GAN 2,54 veces más rápido |

La primera operación de 256 × 256, incluyendo creación de sesión, tardó
2,14 s con MI-GAN y 30,28 s con LaMa en esta ejecución. Son mediciones de este
equipo y estas entradas, no una garantía para otras páginas, GPUs o máquinas.
