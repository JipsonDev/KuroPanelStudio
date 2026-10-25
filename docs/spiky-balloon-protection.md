# Protección final del contorno en globos radiales

## Problema reproducido

El relleno de globos uniformes validaba una máscara inicial, pero la recuperación
posterior de letras oscuras podía añadir de nuevo rayos desconectados del borde.
En una selección amplia, esos trazos pequeños parecían letras. La ampliación
para cubrir halos podía extender el daño después de la validación del contorno.

## Referencias examinadas

- [BallonsTranslator: contornos y flood fill](https://github.com/dmMaze/BallonsTranslator/blob/dev/ballontranslator/utils/textblock_mask.py): separa geometría del globo, texto y fondo. También se revisó su [refinado de máscaras CTD](https://github.com/dmMaze/BallonsTranslator/blob/dev/ballontranslator/modules/textdetector/ctd/textmask.py), que contrasta componentes con la predicción de texto antes de expandirlos.
- [manga-image-translator: refinado de máscara](https://github.com/zyddnys/manga-image-translator/blob/main/manga_translator/mask_refinement/text_mask_utils.py): completa una máscara a partir de las regiones de texto detectadas, en lugar de tratar el rectángulo del globo como una selección de borrado.
- [manga-text-cleaner: detector OpenCV](https://github.com/BulganRdn/manga-text-cleaner/blob/main/mangacleaner/core/detection.py): utiliza interiores erosionados para proteger bordes. Sus filtros de forma no se adoptaron porque rechazan contornos muy irregulares, justamente el caso radial que se necesita conservar.

La implementación es propia y utiliza OpenCV ya incluido en el proyecto. No se
copiaron archivos, código ni pesos de esos repositorios ni se añadieron servicios
remotos o dependencias.

## Corrección

Después de completar letras y halos, `protect_flat_balloon_border` verifica el
contorno del fondo uniforme. Cierra huecos pequeños en las barreras que contrastan
con ese fondo, reconstruye el interior sin usar una envolvente convexa y reserva
un margen hacia dentro. Así se pueden conservar coronas con rayos separados e
interiores cortados por los límites de la imagen.

Se exige apoyo de trazos visibles del original cerca del contorno y concordancia
con la mayor parte de la máscara. Un marco artificial del recorte no basta para
declarar un globo. Sin evidencia suficiente se conserva la ruta anterior.

La protección se aplica únicamente a la máscara automática de rellenos uniformes,
después de todas las ampliaciones. No cambia la máscara que el usuario ya revisó
y entrega a `clean_prepared`, ni limita el pincel manual. Los fondos degradados o
con textura conservan su flujo de reconstrucción. La versión de caché de máscaras
pasa de 44 a 45 para no reutilizar resultados automáticos anteriores.

## Verificación

Los tres casos sintéticos generan coronas de 100, 140 y 180 rayos. La inferencia
de localización se sustituye por líneas de texto conocidas; el resto de la
preparación y limpieza es real. Verifican que se eliminen las letras completas
sin seleccionar rayos ni modificar la ilustración contigua.

Dos comprobaciones opcionales usan la imagen aportada por el usuario, conservada
fuera del repositorio en `local-builds/spiky-balloons/source.png`. Se ejecutan con
el modelo OCR local y la preparación y composición reales:

| Selección | Píxeles del dibujo modificados antes | Después | Píxeles oscuros de texto restantes |
| --- | ---: | ---: | ---: |
| Amplia, alrededor del diálogo | 3691 | 0 | 0 |
| Imagen completa del ejemplo | 563 | 0 | 0 |

El área del texto se anotó como `(108, 154)`–`(575, 340)` sobre el recorte original
de 692 × 399. La métrica de dibujo compara todos los píxeles fuera de esa área,
incluido su suavizado. Estos resultados describen este ejemplo y las pruebas
sintéticas; no garantizan todos los estilos de globo, especialmente cuando texto
y borde se tocan o el fondo es ambiguo.

La suite completa pasó con **528 pruebas, 6 omitidas y 11 subpruebas**. Persisten
los avisos de Qt al cerrar sobre un slot de `CanvasView`; pytest terminó con código 0.

```powershell
python -m pytest tests/test_spiky_balloon_borders.py tests/test_bubble_mask.py tests/test_balloon_cleaning.py -q
python -m pytest -q
```

REA identificó estáticamente el ejecutable instalado mediante sus recursos PE.
Esa evidencia identifica el archivo; no descompila la lógica Python. Para aplicar
el cambio al paquete se comprueba aparte el código de sus módulos PyInstaller.
Los informes REA, la imagen original, las comparaciones y los respaldos locales
permanecen en `local-builds/spiky-balloons/` y no se publican.
