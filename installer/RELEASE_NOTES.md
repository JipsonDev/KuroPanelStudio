# KuroPanel Studio @VERSION@

## Tamaño y distribución de marcas de agua

- El tamaño y la alineación se calculan sobre el contenido visible del PNG, evitando que los márgenes transparentes hagan que el logo parezca pequeño o desplazado. Puedes desactivar «Medir solo el logo visible» para conservar esos márgenes.
- El reparto continuo mantiene un tamaño uniforme entre páginas del mismo ancho. Los cortes demasiado cortos quedan sin marca en lugar de recibir una miniatura.
- «Distancia entre centros» permite ajustar la densidad automática del capítulo. El valor «Automática» conserva el cálculo según dimensiones; la separación mínima sigue protegiendo el espacio libre entre logos.
- El modo «Adaptar al espacio libre» busca otro lado dentro de la misma zona cuando una caja de texto bloquea la ubicación preferida. Se mantienen los modos de columna y alternado, sin reiniciar el reparto en cada página.
- Tamaño y alineación accesibles sin desplegar opciones avanzadas, dimensiones finales en píxeles y total calculado para el capítulo. Los cambios numéricos se confirman antes de recalcular.
- Controles y mensajes nuevos disponibles en español e inglés.

## Aplicar el nuevo reparto

Para sustituir posiciones manuales anteriores, pulsa **Redistribuir todo el capítulo** y después **Todo el capítulo**. La protección utiliza cajas de texto detectadas; no identifica caras, manos ni otros dibujos. Conviene revisar visualmente el resultado.

## Verificación e instalación

Verificación local: **513 pruebas y 11 subpruebas aprobadas**, incluidas 17 nuevas comprobaciones de tamaño visible, páginas cortas, densidad, obstáculos, interfaz y traducción de controles. La comparación entre lienzo y exportación incluye opacidad del 65 % y rotaciones de 0°, 30° y 90°, con diferencias máximas de un nivel por canal en esas pruebas controladas.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; su SHA-256 está en `SHA256SUMS.txt`. La edición pública mantiene el canal de GitHub Releases para **Buscar actualizaciones**. Si usas la edición local 0.2.15, instala esta edición pública una vez para recibir futuras versiones de GitHub.
