# KuroPanel Studio @VERSION@

## Interfaz simplificada y YOLO para todo el capítulo

- Cabecera y navegación compactas, paneles planos y una paleta neutra con acento dorado discreto.
- Se eliminan accesos repetidos a detección y configuración. El Inspector se dedica a la caja seleccionada; el zoom y la navegación quedan en el lienzo.
- **Exportar** reúne página, capítulo y tira continua, además del formato de página. **Abrir** incluye proyectos editables.
- En **Procesar → OCR → Detectar cajas · YOLO**, elige **Todo el capítulo**. La detección es local y no necesita credenciales OCR.
- El lote procesa una página a la vez, muestra progreso y permite cancelar. Conserva las páginas que ya tienen cajas y los cambios manuales hechos durante el proceso.
- Cancelar o fallar no aplica resultados parciales. Deshacer revierte el lote completo; se corrige un error anterior que bloqueaba restaurar el guion.
- Se mantienen MI-GAN, LaMa, marcas de agua y exportación a resolución original.

Verificación local: **566 pruebas aprobadas, 6 omitidas y 11 subpruebas aprobadas**. Incluye YOLO real en CPU con tres páginas sintéticas, progreso, cancelación, protección de texto guardado y cambios concurrentes, menús, idioma y deshacer.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe` y verifica `SHA256SUMS.txt`. Incluye modelos y runtime ONNX para CPU, sin claves de API.
