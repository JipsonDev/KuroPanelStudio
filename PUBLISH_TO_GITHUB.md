# Publicar KuroPanel Studio en GitHub

La carpeta fuente contiene modelos mayores de 100 MB. Deben publicarse mediante
Git LFS; no desactives ni elimines `.gitattributes`.

## Crear el repositorio

Ejecuta estos comandos dentro de la carpeta fuente preparada. Si la carpeta ya
contiene `.git` y los archivos aparecen en `git status`, omite `git init`,
`git lfs install` y `git add`:

```powershell
git init -b main
git lfs install
git add .
git status
git commit -m "Publicación inicial de KuroPanel Studio 0.1.0"
git remote add origin URL_DEL_REPOSITORIO
git push -u origin main
```

Antes del commit, confirma que `git status` no muestra `api_configs.json`,
`credentials.dat`, `.venv-gpu`, `dist`, `github-release`, proyectos personales
ni imágenes que no quieras publicar.

## Publicar el instalador

1. Crea la etiqueta `v0.1.0` en GitHub.
2. Abre **Releases** y crea una nueva versión usando esa etiqueta.
3. Copia el contenido de `RELEASE_NOTES.md` en la descripción.
4. Adjunta `KuroPanelStudio-Setup-0.1.0-Windows-x64.exe` y
   `SHA256SUMS.txt` desde la carpeta binaria preparada.

El instalador no debe añadirse al historial Git normal.

## Licencia

Elige una licencia antes de hacer público el repositorio. MIT permite una
reutilización amplia; GPL obliga a que las modificaciones distribuidas sigan
siendo libres. No añadas una licencia sin aceptar primero sus consecuencias.
