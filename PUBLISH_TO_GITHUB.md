# Actualizar el repositorio existente

El repositorio público es [JipsonDev/KuroPanelStudio](https://github.com/JipsonDev/KuroPanelStudio).
Conserva el historial de `main` y publica los cambios mediante commits nuevos;
no hace falta crear otro repositorio ni forzar el `push`.

Antes de cada publicación, revisa `git status` y ejecuta las pruebas. No
incluyas `dist/`, `github-release/`, entornos virtuales, cachés, proyectos
personales, `settings.json`, `credentials.dat` ni claves de API. `.gitignore`
excluye esos archivos. Los modelos ONNX y los pesos que ya forman parte del
proyecto se gestionan con Git LFS según `.gitattributes`.

Sube primero los commits de código a `main`. Para crear un instalador de una
versión nueva, sube después una etiqueta semántica que coincida con esa
versión, por ejemplo `v0.2.9`. El flujo
`.github/workflows/windows-release.yml` ejecuta las pruebas, compila el
instalador y publica la GitHub Release con su SHA-256 y las notas de cambios automáticamente.
Comprueba que el flujo termine antes de anunciar la versión. Los usuarios de
la app instalada recibirán la actualización desde el programa.

Más detalles en [Actualizaciones](docs/actualizaciones.md). El instalador no
se añade al historial Git.
