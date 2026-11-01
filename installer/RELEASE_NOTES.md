# KuroPanel Studio @VERSION@

## Tira más nítida y marcas equilibradas

- La vista continua conserva más detalle al reducir y usa los píxeles originales a aumentos habituales de lectura. También tiene en cuenta la densidad de pantalla.
- La carga da prioridad a todas las teselas visibles, con un presupuesto de 48 MiB y ajuste del detalle para ventanas grandes. La exportación sigue a resolución original.
- Nuevo ajuste predeterminado: marca de ancho **22 %**, opacidad **55 %**, lados alternados, unos **7.000 px** entre centros y **máximo 24** automáticas en todo el capítulo.
- **Usar ajuste equilibrado** conserva tu PNG y reemplaza las posiciones manuales para rehacer el reparto completo. Los proyectos guardados mantienen su diseño hasta aplicar el ajuste.
- El límite automático se puede modificar y no limita una cantidad fija o posiciones manuales.
- Se mantienen MI-GAN, LaMa, la protección de globos y la exportación de la tira por bandas.

Verificación local: **558 pruebas aprobadas, 6 omitidas y 11 subpruebas aprobadas**. Incluye trazos finos, densidad de pantalla, cobertura visible dentro del presupuesto de memoria y persistencia del ajuste y del límite de marcas.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; verifica `SHA256SUMS.txt`. Incluye modelos y runtime ONNX para CPU, sin claves de API.
