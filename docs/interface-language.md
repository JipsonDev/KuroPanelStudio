# Idioma de la interfaz

En el menú de la esquina superior derecha, abre **Configuración… → Interfaz**,
elige **Español** o **English** y pulsa **Guardar**. Los controles, menús,
avisos y pestañas cambian de idioma en la sesión actual. La elección se guarda
en `settings.json` dentro de los datos locales del usuario y se aplica desde
la pantalla de inicio en la siguiente ejecución.

Este ajuste solo cambia la interfaz. **Idioma origen** e **Idioma destino** en
la pestaña **OCR y traducción** siguen controlando la traducción del contenido;
los datos del proyecto, nombres de archivo, proveedores y modelos no se traducen.

Las cadenas visibles se mantienen en `ui/i18n.py`. Los elementos de una lista
desplegable solo se traducen cuando sus opciones tienen identificadores
estables y el control activa `kuro_i18n_choices`; así, el filtro de páginas
puede mostrarse como *Translation pending* y continuar usando internamente
`Pendiente traducción`. Los textos de usuario en campos editables no se tocan.
