# KuroPanel Studio @VERSION@

## Limpieza de globos radiales

- Una comprobación final del contorno impide que la recuperación de letras y halos vuelva a seleccionar rayos y puntas del borde en globos de fondo uniforme.
- En el ejemplo de prueba, el daño fuera del texto pasó de 3691 píxeles a 0 con una selección amplia, manteniendo la eliminación de las letras oscuras anotadas.
- La protección no cambia el pincel ni las máscaras revisadas manualmente. Los fondos con textura o degradados conservan la reconstrucción anterior.
- Las máscaras automáticas antiguas se invalidan mediante la caché versión 45. Para reparar una limpieza anterior, vuelve a generar su máscara y ejecutar la limpieza desde el original.

## Posiciones de marcas de agua

- La exportación conserva las posiciones manuales al desactivar «Mantener dentro de la página», incluidas coordenadas negativas y marcas que cruzan un borde.
- Mover marcas en una página mantiene el reparto y la alternancia de las demás. Solo se omite una marca automática si entra en conflicto con la separación de una manual.
- Mover una marca o cambiar su PNG se reconoce como cambio del documento y participa en deshacer, rehacer y autoguardado.

## Limpieza a resolución original

- Las selecciones grandes se procesan en recortes de hasta 1024 × 1024 con contexto, sin reducir la resolución.
- Se omiten máscaras vacías y zonas sin selección, y se conservan los píxeles fuera de la máscara.

## Verificación e instalación

Verificación local: **528 pruebas aprobadas, 6 omitidas y 11 subpruebas aprobadas**. Incluye pruebas de bordes radiales, recortes de LaMa, conservación de posiciones e historial de marcas de agua, y comparación entre lienzo y exportación. Dos pruebas opcionales utilizan una imagen local que no se publica; en GitHub esas pruebas se omiten.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; su SHA-256 está en `SHA256SUMS.txt`. Incluye los modelos y el runtime ONNX para CPU, sin claves de API. La edición pública mantiene el canal de GitHub Releases para **Buscar actualizaciones**. Si usas una edición local, instala esta edición pública una vez para recibir futuras versiones de GitHub.
