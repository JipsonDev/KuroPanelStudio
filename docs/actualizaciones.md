# Aplicación Windows y actualizaciones

KuroPanel Studio usa un instalador por usuario de Inno Setup. El programa y los
modelos quedan en `%LOCALAPPDATA%\Programs\KuroPanel Studio`; la configuración,
las credenciales, la recuperación y las descargas de actualización quedan en
`%LOCALAPPDATA%\ManhuaSuiteEditor`. Reinstalar o actualizar la app no borra
esos datos ni los proyectos guardados fuera de la instalación.

La app instalada consulta `releases/latest` de GitHub al iniciar, salvo que se
desactive **Configuración → Interfaz → Buscar actualizaciones automáticamente**.
El menú superior ofrece **Buscar actualizaciones…** en cualquier momento. Una
actualización se ofrece antes de descargarla; la descarga muestra progreso y
puede cancelarse. El instalador solo se ejecuta si coinciden tamaño y SHA-256
de la Release. La app guarda su estado de recuperación, se cierra, el instalador
actualiza la misma ubicación y abre la nueva versión. Ejecutar desde el código
fuente no instala actualizaciones.

## Publicar una versión sin cargar archivos manualmente

1. Sube al repositorio todos los cambios de la versión. El flujo necesita los
   modelos ONNX por Git LFS y el código en la etiqueta.
2. Crea y sube una etiqueta semántica nueva, por ejemplo:

   ```powershell
   git tag v0.2.2
   git push origin v0.2.2
   ```

3. El flujo `.github/workflows/windows-release.yml` ejecuta las pruebas en
   Windows, compila la app, genera el instalador y `SHA256SUMS.txt`, y crea la
   GitHub Release con las notas de `installer/RELEASE_NOTES.md`. Comprueba que la ejecución termine correctamente antes de
   anunciar la versión.

Cada versión requiere un binario nuevo, pero GitHub Actions lo construye y lo
adjunta automáticamente. Quienes ya instalaron la app reciben el aviso desde
ella; no necesitan descargar otro `.exe` por su cuenta. Las actualizaciones
son del instalador completo, no diferenciales.

Para compilar localmente, instala las dependencias de `requirements-release.txt`
y PyInstaller en `.venv`, instala Inno Setup 6 y ejecuta:

```powershell
.\build_windows.ps1 -Version 0.2.2
.\build_installer.ps1 -Version 0.2.2
```

`build_windows.ps1` incorpora la versión a la app y restaura los archivos
fuente de versión al terminar. La edición GPU local sigue disponible con
`.venv-gpu`; el flujo automático distribuye la edición ONNX para CPU, sin
las dependencias voluminosas de PyTorch. CUDA requiere un paquete aparte.
