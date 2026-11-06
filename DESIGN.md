# KuroPanel Studio: interfaz de edición

Esta mejora conserva el logo y el acento dorado de KuroPanel y simplifica el
editor existente. El modo es **Operate**: leer el capítulo, detectar cajas,
limpiar y rotular. El contenido del lienzo tiene prioridad sobre los controles.

- Segoe UI: 12 px para controles, 11 px para información secundaria,
  15 px para títulos de panel y 22 px para el estado inicial.
- Superficies carbón: lienzo `#191A1C`, panel `#222326`, controles `#2A2C30`,
  divisores `#3B3D42`. Texto `#EEEFF1`, secundario `#B8BCC3`.
- Dorado `#E7BC64` para la acción principal y la selección. Los destinos
  activos usan fondo neutro y un icono dorado; no bloques amarillos grandes.
- Controles con radio de 4 px, espaciado de 4/8/12/16 px y paneles planos.
  Las opciones secundarias usan filas desplegables, sin tarjetas anidadas.
- Cabecera de 54 px, navegación lateral de 72 px. El ancho recuperado se
  dedica al lienzo. En ventana compacta, Documento se integra en el panel.

## Un lugar por acción

- Cabecera: abrir carpeta/proyecto/PSD, guardar, deshacer/rehacer,
  exportar página/capítulo/tira y elegir formato de página.
- Lienzo: zoom, ajuste, comparación, tira continua y navegación de páginas.
- Procesar: detección YOLO local, reconocimiento OCR, traducción y limpieza.
  Proveedor y modelo permanecen en una sola sección desplegable por proceso.
- Caja seleccionada: edición de texto, OCR y gestión de cajas. No repite
  navegación, zoom, progreso general ni acciones de archivo.
- La barra superior de progreso conserva cancelación y errores del lote.
  El pie de Procesar conserva el alcance y la acción principal al desplazar.

Las acciones contextuales de capas, copia y edición siguen disponibles.
La simplificación cambia dónde se encuentran los comandos, no los proyectos.
