# Validación de limpieza con páginas reales

Se revisaron cinco imágenes del directorio local `prueba [stitched]`, con siete
globos o cuadros de diálogo anotados en
`tests/fixtures/real_cleaning_cases.json`. Las imágenes originales no se copian
al repositorio. Cada anotación incluye la caja detectada y uno o varios
rectángulos ajustados a las líneas de texto. Así, las esquinas oscuras del
contorno dentado no se cuentan como letras residuales.

El evaluador ejecuta la misma preparación de máscara y limpieza que el editor.
Genera, para cada globo, una comparación **original / máscara / limpio**, más
`summary.json` y un panorama `overview.jpg` bajo `graphify-out/real-cleaning/`.

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_real_cleaning `
  --images 'C:\ruta\a\prueba [stitched]' `
  --output graphify-out/real-cleaning/final-v6
```

## Fallo y corrección

La máscara anterior dependía demasiado de la activación del localizador OCR.
En una página real dejó partes enteras de caracteres chinos: los dos globos
de `156/01.png` conservaron **2 398** y **16 429** píxeles oscuros después
de la limpieza. El detector de texto sí había encontrado sus cajas.

Ahora la limpieza recupera componentes negros pequeños y contrastados contra
un fondo local claro. Exige varios componentes compatibles con texto, limita
su tamaño y densidad, y no adopta trazos que crucen los bordes de la caja.
Una excepción estrecha recupera puntuación separada junto a la caja y el
último carácter cuando la propia página lo corta en el borde físico. Las
imágenes con fondo oscuro, ilustración dominante o letras de efecto siguen
utilizando la ruta neuronal; tres efectos reales se inspeccionaron aparte
para comprobar que esta recuperación no los convierte en una máscara grande.
Cuando la selección ocupa casi toda la imagen, también se conserva solo la
máscara OCR para evitar confundir rayos de impacto repetidos con letras.
Una pasada final completa los pequeños huecos de caracteres que ya estaban
cubiertos en al menos un 85 % por la máscara, sin tocar componentes aislados
ni trazos que alcanzan el borde. Esto eliminó las dos motas que quedaban en
el primer carácter del globo radial. La recuperación de texto cortado en el
borde físico de la página también incluye ahora fragmentos de puntuación muy
pequeños cuando los respaldan letras adyacentes.

## Resultado medido

En las líneas de texto de los siete globos anotados hay **93 333** píxeles
originalmente oscuros (`min(R,G,B) < 80`). Tras la limpieza quedan **0** en
esas zonas. La máscara cubre el 100 % de esos píxeles; el globo radial pasó
de 38 residuos oscuros a 0. Las 64 manchas que antes se contaban en el globo
dentado pertenecen a sus esquinas superiores, fuera de las líneas de texto.
Los siete globos conservaron su contorno en la revisión visual.

La máscara final no marca píxeles fuera de la unión de las cajas y las zonas
de texto anotadas. Después de limpiar, **569** píxeles cambiaron fuera de los
rectángulos de texto; solo **2** eran oscuros inicialmente. Corresponden
principalmente a bordes suavizados y puntuación junto a la caja. No hubo
cambios fuera de la unión de cajas y zonas de texto. Estas cifras permiten
detectar invasiones del dibujo *fuera* de esas zonas, pero no son una medida
perfecta de daño dentro de una caja que se superpone a la ilustración. La
comparación visual por globo sigue siendo necesaria, especialmente con globos
translúcidos y efectos. El umbral de píxel oscuro no detecta posibles huellas
grises muy tenues; el resultado visual aún puede requerir un retoque manual.

`tests/test_real_cleaning_cases.py` comprueba que, cuando esas imágenes y los
modelos locales están disponibles, cada globo conserva al menos 98 % de
cobertura del texto oscuro en su máscara y no marca dibujo fuera de las
anotaciones. El globo radial exige cobertura completa para impedir que
reaparezcan las motas. En otros equipos, esas cinco pruebas se omiten; las pruebas
sintéticas siguen ejecutándose normalmente.
