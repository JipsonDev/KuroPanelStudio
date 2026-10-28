# KuroPanel Studio @VERSION@

## Limpieza MI-GAN

- Motor MI-GAN ONNX incluido para la limpieza automática, por capítulo y con pincel de máscara.
- En **Limpiar página → Motor de limpieza → Modelo local** puedes elegir MI-GAN o LaMa. MI-GAN es la opción inicial de configuraciones nuevas; al actualizar se respeta la preferencia guardada.
- La carga y la inferencia se ejecutan en el proceso aislado para mantener la interfaz disponible.
- Los recortes MI-GAN se limitan a 512 × 512 con contexto, omiten zonas sin selección y solo escriben dentro de la máscara revisada.
- Se conservan las correcciones de puntas y bordes de los globos, el relleno sólido protegido y las posiciones e historial de marcas de agua.
- Ambos empaquetados incluyen los pesos MI-GAN y su licencia MIT. No se requieren servicios ni paquetes adicionales para usarlos.

## Verificación e instalación

Verificación local: **539 pruebas aprobadas, 6 omitidas y 11 subpruebas aprobadas**, incluida una inferencia real MI-GAN en el proceso aislado. La velocidad depende del tamaño de los recortes; MI-GAN no es necesariamente más rápido para zonas pequeñas.

Descarga `KuroPanelStudio-Setup-@VERSION@-Windows-x64.exe`; su SHA-256 está en `SHA256SUMS.txt`. Incluye los modelos y el runtime ONNX para CPU, sin claves de API. La edición pública mantiene el canal de GitHub Releases para **Buscar actualizaciones**.
