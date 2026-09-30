"""Runtime translations for the editor's visible Qt controls.

Widget text is presentation only: project data, provider identifiers and OCR /
translation language settings keep their original values.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QPlainTextEdit,
    QSpinBox, QTabBar, QTabWidget, QTextEdit, QWidget,
)


ES_TO_EN = {
    "⚠ El texto no cabe en el globo": "⚠ Text does not fit inside the balloon",
    # Window, navigation and empty state.
    "Espacio de edición de manhuas": "Manhua editing workspace",
    "Abrir": "Open", "Guardar": "Save", "Exportar": "Export",
    "Editar": "Edit", "Páginas": "Pages", "Capas": "Layers",
    "Procesar": "Process", "Guión": "Script", "Texto": "Text",
    "Efectos": "Effects", "Herramientas": "Tools", "Documento": "Document",
    "Abrir capítulo": "Open chapter", "Abrir proyecto": "Open project",
    "Guardar proyecto": "Save project", "Exportar página": "Export page",
    "Exportar capítulo": "Export chapter", "Comparar": "Compare",
    "Abrir carpeta del capítulo…": "Open chapter folder…",
    "Abrir archivo PSD/PSB…": "Open PSD/PSB file…",
    "Abrir capítulo (Ctrl+O) o archivo PSD/PSB": "Open a chapter (Ctrl+O) or PSD/PSB file",
    "Guardar proyecto (Ctrl+S)": "Save project (Ctrl+S)",
    "Exportar la página actual": "Export the current page",
    "Deshacer": "Undo", "Rehacer": "Redo",
    "Deshacer (Ctrl+Z)": "Undo (Ctrl+Z)", "Rehacer (Ctrl+Y)": "Redo (Ctrl+Y)",
    "Cambiar proyecto…": "Switch project…", "Configuración…": "Settings…",
    "Buscar actualizaciones…": "Check for updates…",
    "Buscar actualizaciones automáticamente al iniciar": "Check for updates automatically at startup",
    "Actualizaciones": "Updates",
    "Instala la aplicación de Windows para recibir actualizaciones automáticas.":
        "Install the Windows application to receive automatic updates.",
    "Sin actualizaciones": "No updates",
    "No se pudo buscar actualizaciones": "Could not check for updates",
    "Actualización disponible": "Update available",
    "Se descargará y verificará el instalador antes de cerrar la aplicación.":
        "The installer will be downloaded and verified before the application closes.",
    "Actualizar ahora": "Update now", "Más tarde": "Later",
    "Proceso en curso": "Task in progress",
    "Espera a que termine antes de actualizar.": "Wait for it to finish before updating.",
    "Descargando actualización…": "Downloading update…",
    "Actualizando KuroPanel Studio": "Updating KuroPanel Studio",
    "No se pudo actualizar": "Could not update",
    "No se pudo abrir el instalador": "Could not open the installer",
    "Marca de agua…": "Watermark…", "Menú de aplicación": "Application menu",
    "Proyecto, configuración y marca de agua": "Project, settings and watermark",
    "Ocultar o recuperar paneles": "Hide or restore panels",
    "Ocultar o recuperar paneles (Ctrl+Shift+F)": "Hide or restore panels (Ctrl+Shift+F)",
    "TU PRÓXIMA PÁGINA": "YOUR NEXT PAGE",
    "Una historia empieza aquí": "A story begins here",
    "Abre tu capítulo": "Open your chapter",
    "Abre un capítulo o arrastra tus imágenes para empezar a editar.":
        "Open a chapter or drop your images here to start editing.",
    "Abrir una carpeta de imágenes (Ctrl+O)": "Open an image folder (Ctrl+O)",
    "Abrir PSD / PSB": "Open PSD / PSB",
    "01  Detectar y leer\n02  Limpiar y traducir\n03  Rotular y exportar":
        "01  Detect and read\n02  Clean and translate\n03  Typeset and export",
    "Siguiente página pendiente": "Next pending page",
    "Modo de enfoque": "Focus mode",
    "Quitar último retoque": "Remove last retouch",
    "Pincel de color": "Color brush", "Tampón de clonar": "Clone stamp",
    "Pincel corrector puntual": "Spot healing brush",
    "Restaurar original": "Restore original",
    "Pincel de máscara": "Mask brush", "Eliminar cuadro": "Delete box",

    # Pages and page status.
    "Sin capítulo cargado": "No chapter loaded",
    "Capítulo sin imágenes cargadas": "Chapter has no images loaded",
    "Dimensiones pendientes": "Dimensions pending",
    "Todas": "All", "Sin detectar": "Undetected",
    "Pendiente OCR": "OCR pending", "Pendiente traducción": "Translation pending",
    "Pendiente limpieza": "Cleaning pending", "Con error": "With errors",
    "Sin rotular": "Not typeset", "Rotuladas": "Typeset",
    "Buscar página…": "Search pages…", "Buscar página": "Search pages",
    "Filtrar páginas": "Filter pages",
    "Vista en cuadrícula": "Grid view", "Vista en lista": "List view",
    "Invertir orden de visualización": "Reverse display order",
    "Ir a la siguiente página pendiente": "Go to next pending page",
    "Ir a la siguiente página pendiente del filtro (Ctrl+Alt+N)":
        "Go to next pending page in the filter (Ctrl+Alt+N)",
    "Opciones de imagen": "Image options", "Abrir página": "Open page",
    "Cajas detectadas": "Boxes detected", "OCR completado": "OCR complete",
    "Imagen limpiada": "Image cleaned", "Traducción completada": "Translation complete",
    "Página rotulada": "Page typeset",
    "Error de proceso en esta página": "Processing error on this page",
    "Estados del flujo de cada página": "Workflow status for each page",
    "D Detectada · O OCR · L Limpia · T Traducida · R Rotulada":
        "D Detected · O OCR · L Cleaned · T Translated · R Typeset",

    # Processing dock.
    "Capítulo cargado": "Chapter loaded", "Ocultar aviso de capítulo": "Dismiss chapter notice",
    "&Traducir": "&Translate", "&Limpiar": "&Clean",
    "Leer texto (OCR)": "Read text (OCR)",
    "Detecta las cajas y reconoce solo el texto pendiente.":
        "Detect boxes and recognize only pending text.",
    "Proveedor y modelo": "Provider and model", "Configura el motor de OCR": "Configure the OCR engine",
    "Escribe el identificador del modelo": "Enter the model ID",
    "Selecciona un modelo o escribe su identificador exacto de Alibaba Cloud":
        "Select a model or enter its exact Alibaba Cloud ID",
    "Selecciona un modelo o escribe el identificador disponible en tu cuenta.":
        "Select a model or enter the ID available in your account.",
    "Configurar proveedor OCR": "Configure OCR provider",
    "Abrir proveedor y modelo": "Open provider and model",
    "Detectar cajas de texto": "Detect text boxes", "Leer texto": "Read text",
    "Releer caja seleccionada": "Reread selected box",
    "Realiza una nueva solicitud al proveedor, aunque haya OCR guardado":
        "Send a new request to the provider, even if OCR is cached",
    "Se reutiliza el OCR guardado. Releer caja realiza una nueva solicitud.":
        "Saved OCR is reused. Rereading a box sends a new request.",
    "Sesión OCR: sin solicitudes": "OCR session: no requests",
    "Ajustes de detección": "Detection settings",
    "Sensibilidad, idioma y filtros": "Sensitivity, language and filters",
    "La detección usa el modelo local. Puedes detectar cajas antes de leer el texto.":
        "Detection uses the local model. You can detect boxes before reading text.",
    "tokens no reportados": "tokens not reported",
    "Traducir diálogo": "Translate dialogue",
    "Traduce el texto reconocido usando el glosario del proyecto.":
        "Translate recognized text using the project glossary.",
    "Configura el motor de traducción": "Configure the translation engine",
    "Configurar proveedor de traducción": "Configure translation provider",
    "Sin proyecto de traducción asignado": "No translation project assigned",
    "Traducir texto": "Translate text",
    "Limpiar página": "Clean page", "Limpiar capítulo": "Clean chapter",
    "Prepara la máscara, revisa los trazos y aplica la limpieza.":
        "Prepare the mask, review strokes and apply cleaning.",
    "Motor de limpieza": "Cleaning engine", "Modelo local y rendimiento": "Local model and performance",
    "Configurar motor de limpieza": "Configure cleaning engine",
    "Preparar limpieza": "Prepare cleaning",
    "Primero revisa la máscara roja. Puedes borrar falsos positivos antes de aplicar LaMa.":
        "Review the red mask first. You can erase false positives before applying LaMa.",
    "Editar máscara": "Edit mask", "Aplicar limpieza": "Apply cleaning",
    "Control de calidad": "Quality control",
    "Comparar original, máscara protegida y resultado caja por caja":
        "Compare original, protected mask and result box by box",
    "Retoque manual": "Manual retouch",
    "Pinceles, restauración y máscara": "Brushes, restoration and mask",
    "Pintar (P)": "Paint (P)",
    "Pincel de color (P). Alt + clic toma un color del lienzo":
        "Color brush (P). Alt + click picks a canvas color",
    "Tampón de clonar (S)": "Clone stamp (S)",
    "Tampón de clonar (S). Alt + clic fija el origen de textura":
        "Clone stamp (S). Alt + click sets the texture source",
    "Corrector puntual (H)": "Spot healing (H)",
    "Corrector puntual (H). Pinta para mezclar y fusionar la textura circundante":
        "Spot healing (H). Paint to blend nearby texture",
    "Restaurar original (R)": "Restore original (R)",
    "Recupera con el pincel los píxeles exactos de la imagen original, sin quitar otros retoques":
        "Brush back exact source pixels without removing other retouches",
    "Crear máscara (M)": "Create mask (M)",
    "Pincel de máscara (M). Dibuja, revisa en rojo y luego aplica LaMa":
        "Mask brush (M). Draw, review in red, then apply LaMa",
    "Cuentagotas: toma un color del lienzo (también Alt + clic)":
        "Eyedropper: pick a canvas color (also Alt + click)",
    "Elimina el último parche del pincel, incluso si venía guardado en el proyecto":
        "Remove the latest brush patch, even if it was saved in the project",
    "Reparar antiguos": "Repair old strokes",
    "Elimina los trazos largos creados por el antiguo error de desplazamiento":
        "Remove long strokes created by the former offset bug",
    "OPACIDAD LIMPIEZA": "CLEANING OPACITY",
    "Caja seleccionada": "Selected box", "Página actual": "Current page",
    "Todo el capítulo": "Entire chapter", "Configurar OCR": "Configure OCR",
    "Configurar traducción": "Configure translation",
    "Lectura y portapapeles": "Reading and clipboard",
    "Opciones de copia y formato": "Copy and formatting options",
    "FLUJO Y LECTURA": "WORKFLOW AND READING",
    "Flujo y lectura": "Workflow and reading",
    "Ajustes del pincel": "Brush settings",
    "Lectura manga · derecha → izquierda": "Manga reading · right → left",
    "Ordena las cajas de cada fila de derecha a izquierda y mantiene el avance de arriba abajo":
        "Order each row's boxes from right to left, then continue downward",
    "Autodetectar cajas de texto con YOLO": "Automatically detect text boxes with YOLO",
    "Detectar cajas (YOLO)": "Detect boxes (YOLO)",
    "Pincel máscara": "Mask brush", "Dibuja manualmente la máscara de limpieza": "Draw the cleaning mask manually",
    "GROSOR DEL PINCEL": "BRUSH SIZE",

    # Inspector, script and canvas.
    "Página anterior": "Previous page", "Página siguiente": "Next page",
    "Texto de la capa": "Layer text",
    "Selecciona una caja para ver su OCR original.": "Select a box to view its original OCR.",
    "El OCR completo aparecerá numerado aquí.": "Full OCR will appear here with numbers.",
    "Copiar página": "Copy page", "Copiar capítulo": "Copy chapter",
    "Pegar y distribuir texto": "Paste and distribute text",
    "Traducción o texto final de esta capa…": "Translation or final text for this layer…",
    "Aplicar texto": "Apply text", "Eliminar seleccionadas": "Delete selected",
    "Exporta sin cajas visibles y conserva la resolución original.":
        "Export without visible boxes and preserve the original resolution.",
    "Página": "Page", "OCR de la página": "Page OCR",
    "Aplicar al terminar de escribir": "Apply when finished typing",
    "Gestión de cajas": "Box management", "Última": "Last",
    "Exportación final": "Final export",
    "Sin OCR. Puedes escribir directamente el texto final.":
        "No OCR. You can enter the final text directly.",
    "Escribe la traducción...": "Enter the translation...",
    "Centrar en el lienzo": "Center on canvas",
    "Copiar traducción": "Copy translation",
    "Traducir los globos de la página activa": "Translate balloons on the active page",
    "Copiar todo el guión al portapapeles": "Copy the entire script to the clipboard",
    "Búsqueda y Reemplazo": "Find and Replace",
    "Texto a buscar...": "Find text...", "Reemplazar con...": "Replace with...",
    "Coincidir mayúsculas / minúsculas": "Match case",
    "Palabra completa": "Whole word", "Todo el proyecto": "Entire project",
    "DIÁLOGOS DE LA PÁGINA": "PAGE DIALOGUE", "Filtrar diálogos...": "Filter dialogue...",
    "Diálogo": "Dialogue", "Guión y Traducción": "Script and Translation",
    "REEMPLAZAR POR": "REPLACE WITH", "ÁMBITO": "SCOPE",
    "¡Guión copiado al portapapeles!": "Script copied to clipboard!",
    "Arrastra para mover · Ctrl + clic o Supr para borrar":
        "Drag to move · Ctrl + click or Delete to remove",
    "Arrastra para mover · Esquinas para redimensionar · Ctrl + clic para borrar":
        "Drag to move · Corners to resize · Ctrl + click to remove",
    "Arrastra para mover · Esquinas para redimensionar · Ctrl + arrastrar esquina para escalar texto · Ctrl + clic dentro para borrar":
        "Drag to move · Corners to resize · Ctrl + drag a corner to scale text · Ctrl + click inside to remove",
    "Arrastra para comparar limpieza y original": "Drag to compare clean and original",
    "Abre un capítulo para comenzar": "Open a chapter to begin",
    "Editando en el lienzo · Ctrl+Enter guarda · Esc cancela":
        "Editing on canvas · Ctrl+Enter saves · Esc cancels",
    "Restablecer zoom al 100%": "Reset zoom to 100%",
    "Original a la izquierda · limpieza a la derecha · arrastra el divisor":
        "Original on left · cleaned on right · drag the divider",
    "Comparar original y limpieza": "Compare original and cleaned",
    "Ocultar o mostrar panel de herramientas": "Hide or show tools panel",
    "Ocultar panel para ampliar el lienzo": "Hide panel to enlarge the canvas",
    "Ajustar imagen": "Fit image", "Mostrar panel de herramientas": "Show tools panel",
    "Abrir imágenes": "Open images", "Ajustar": "Fit", "Más": "More",
    "Traducir": "Translate", "PÁGINA": "PAGE", "Capa": "Layer",
    "EXPORTACIÓN FINAL": "FINAL EXPORT", "TEXTO DE LA CAPA": "LAYER TEXT",
    "OCR DE LA PÁGINA": "PAGE OCR", "GESTIÓN DE CAJAS": "BOX MANAGEMENT",
    "FUSIÓN": "BLENDING", "TIPOGRAFÍA PROFESIONAL": "PROFESSIONAL TYPESETTING",
    "GUIÓN Y TRADUCCIÓN": "SCRIPT AND TRANSLATION",
    "Sin proyecto tipográfico\nConfigúralo antes de comenzar el capítulo":
        "No typesetting project\nConfigure one before starting the chapter",
    "Selecciona primero una biblioteca y después un proyecto.":
        "Select a library first, then a project.",
    "Diálogo  ·  GRITOS  ·  Aa 123": "Dialogue  ·  SHOUTS  ·  Aa 123",
    "Modelo local": "Local model", "CAPAS": "LAYERS",
    "AJUSTES DEL PINCEL": "BRUSH SETTINGS",
    "Sin proyecto asignado · los términos nuevos no se conservarán":
        "No project assigned · new terms will not be saved",
    "●  LaMa se cargará al primer uso": "●  LaMa will load on first use",
    "●  Agrega la API key de Alibaba Cloud en Configuración":
        "●  Add the Alibaba Cloud API key in Settings",
    "Arrastra libremente las cuatro esquinas directamente sobre el SFX en el lienzo. Los valores permanecen editables aquí.":
        "Drag the four corners directly on the SFX canvas. Values remain editable here.",
    "P color · S clonar · H corrector · R restaurar · M máscara  |  Shift+mover: tamaño  |  Alt+clic: origen/color":
        "P paint · S clone · H heal · R restore · M mask  |  Shift+move: size  |  Alt+click: source/color",
    "Configuración guardada": "Settings saved",
    "Credenciales cifradas y flujo de IA actualizado.":
        "Credentials encrypted and AI workflow updated.",

    # Layers.
    "Región de texto": "Text region", "Esperando cambios": "Waiting for changes",
    "Imagen original": "Original image", "Base · resolución original": "Base · original resolution",
    "Imagen limpia": "Cleaned image", "Parches LaMa · no destructivo": "LaMa patches · non-destructive",
    "Limpieza automática": "Automatic cleaning", "Capas PSD": "PSD layers",
    "Pintura manual": "Manual paint", "Restauración original": "Original restoration",
    "Buscar por nombre o texto…": "Search by name or text…",
    "Renombrar capa": "Rename layer", "Duplicar capa": "Duplicate layer",
    "Mostrar u ocultar selección": "Show or hide selection",
    "Bloquear o desbloquear selección": "Lock or unlock selection",
    "Subir en el orden": "Move up", "Bajar en el orden": "Move down",
    "Eliminar capa": "Delete layer", "Más acciones de capa": "More layer actions",
    "Aún no hay cajas de texto": "No text boxes yet", "Detectar cajas": "Detect boxes",
    "Buscar cajas de texto en la página actual": "Find text boxes on the current page",
    "Nombre de la capa:": "Layer name:", "Sin parches": "No patches",
    "Ocultar capa": "Hide layer", "Mostrar capa": "Show layer",
    "Desbloquear geometría": "Unlock geometry", "Bloquear geometría": "Lock geometry",
    "Mostrar u ocultar": "Show or hide", "Bloquear o desbloquear": "Lock or unlock",
    "Subir capa": "Move layer up", "Bajar capa": "Move layer down",
    "Abre una página para detectar texto": "Open a page to detect text",
    "Desbloquear capa": "Unlock layer", "Bloquear capa": "Lock layer",
    "El PSD se recarga después de guardarlo en Photoshop":
        "PSD reloads after you save it in Photoshop",
    "Recargar automáticamente después de guardar en Photoshop":
        "Reload automatically after saving in Photoshop",
    "Sincronizar PSD ahora": "Sync PSD now",

    # Typesetting, effects and SFX.
    "Selecciona una capa de texto": "Select a text layer",
    "Herramientas de texto": "Text tools",
    "ESTILOS RÁPIDOS": "QUICK STYLES",
    "Diálogo": "Dialogue", "Grito": "Shout", "Pensamiento": "Thought",
    "Susurro": "Whisper", "Narración": "Narration",
    "Proyecto y roles": "Project and roles", "Fuente y color": "Font and color",
    "Ajuste al globo": "Balloon fit", "Opciones avanzadas": "Advanced options",
    "Estilos guardados": "Saved styles", "FUENTE": "FONT",
    "TAMAÑO MÁXIMO": "MAXIMUM SIZE", "TAMAÑO FIJO": "FIXED SIZE",
    "Ajustar fuente automáticamente": "Fit font automatically",
    "El texto se centra y mantiene distancia del borde detectado.":
        "Text is centered with clearance from the detected outline.",
    "El texto usa la caja rectangular y su margen interior.":
        "Text uses the rectangular box and its inner margin.",
    "El texto vertical usa la caja; el ajuste al globo queda en pausa.":
        "Vertical text uses the box; balloon fitting is paused.",
    "COMPRESIÓN MÁXIMA": "MAXIMUM CONDENSING",
    "MARGEN INTERIOR DE CAJA": "BOX INNER MARGIN",
    "Comprimir antes de reducir": "Condense before shrinking",
    "Tipografía profesional": "Professional typesetting",
    "ESTILOS RÁPIDOS (1 CLIC)": "QUICK STYLES (1 CLICK)",
    "💬 Diálogo": "💬 Dialogue", "⚡ Grito": "⚡ Shout",
    "💭 Pensar": "💭 Thought", "🤫 Susurro": "🤫 Whisper",
    "📜 Narrar": "📜 Narration", "💥 SFX": "💥 SFX",
    "1 · Proyecto y rol": "1 · Project and role",
    "2 · Fuente de capa": "2 · Layer font",
    "3 · Composición": "3 · Composition",
    "4 · Apariencia": "4 · Appearance", "5 · Estilos": "5 · Styles",
    "Sin proyecto tipográfico": "No typesetting project",
    "Elige la biblioteca, el proyecto y luego el rol que usará esta capa.":
        "Choose the library, project and role for this layer.",
    "Puedes reemplazar la fuente aunque la capa tenga un perfil asignado.":
        "You can override the font even when the layer has a profile.",
    "Fuente manual · Segoe UI": "Manual font · Segoe UI",
    "Negrita (Bold)": "Bold", "Extra negrita": "Extra bold",
    "Seminegrita": "Semibold",
    "Como fue escrito": "As written", "TODO MAYÚSCULAS": "ALL CAPS",
    "todo minúsculas": "all lowercase",
    "VISTA PREVIA DE LA FUENTE": "FONT PREVIEW",
    "Ajustar tamaño de fuente automáticamente": "Fit font size automatically",
    "Ajustar texto ahora": "Fit text now",
    "Seguir contorno del globo": "Follow balloon outline",
    "Centra el texto dentro del área detectada y adapta cada línea al ancho seguro del globo.":
        "Center text in the detected interior and fit each line to the balloon's safe width.",
    "FORMA DE ADAPTACIÓN": "SHAPE MODE",
    "Automático (según el globo)": "Automatic (by balloon)",
    "Ovalado / Elipse (Diálogo)": "Oval / Ellipse (Dialogue)",
    "Diamante / Puntiagudo (Gritos)": "Diamond / Pointed (Shouts)",
    "Rectangular (Cuadros)": "Rectangular (Boxes)",
    "MARGEN MÍNIMO DEL GLOBO": "MINIMUM BALLOON MARGIN",
    "El programa aumenta este margen en globos grandes o con contornos gruesos para que las letras no toquen el borde.":
        "The app increases this margin for large balloons or thick outlines so letters stay clear of the edge.",
    "Centrado óptico": "Optical centering",
    "Detectar automáticamente": "Detect automatically",
    "Chino": "Chinese", "Japonés": "Japanese", "Coreano": "Korean",
    "Izquierda": "Left", "Centro": "Center", "Derecha": "Right",
    "Arriba": "Top", "Abajo": "Bottom",
    "Separación silábica en español": "Spanish hyphenation",
    "Evitar palabras huérfanas": "Avoid orphan words",
    "Puntuación colgante": "Hanging punctuation",
    "Comprimir antes de reducir la fuente": "Condense before shrinking font",
    "Texto vertical": "Vertical text",
    "Componer el texto verticalmente": "Set text vertically",
    "⚠ El texto no cabe dentro del globo": "⚠ Text does not fit inside the balloon",
    "Color del texto": "Text color", "Personaje o narrador": "Character or narrator",
    "Selecciona una biblioteca…": "Select a library…",
    "Selecciona un proyecto…": "Select a project…",
    "Usar fuente manual": "Use manual font",
    "PROYECTO ACTIVO": "ACTIVE PROJECT", "ROL PARA ESTA CAPA": "ROLE FOR THIS LAYER",
    "ESTILO DE LA FUENTE": "FONT STYLE", "FORMATO DE TEXTO": "TEXT FORMAT",
    "TAMAÑO": "SIZE", "REGLAS DEL IDIOMA": "LANGUAGE RULES",
    "ESCALA MANUAL": "MANUAL SCALE", "ESCALA HORIZONTAL": "HORIZONTAL SCALE",
    "ESCALA VERTICAL": "VERTICAL SCALE", "ALINEACIÓN HORIZONTAL": "HORIZONTAL ALIGNMENT",
    "POSICIÓN VERTICAL": "VERTICAL POSITION", "ROTACIÓN": "ROTATION",
    "MARGEN INTERIOR": "INNER PADDING",
    "Fijo para este capítulo": "Fixed for this chapter",
    "Listo para usar": "Ready to use",
    "Nombre del preset": "Preset name", "Activar trazo exterior": "Enable outer stroke",
    "Segundo trazo exterior": "Second outer stroke",
    "Relleno degradado": "Gradient fill", "Activar resplandor": "Enable glow",
    "Activar sombra": "Enable shadow", "Restablecer efectos": "Reset effects",
    "Efectos de texto": "Text effects", "Preset profesional": "Professional preset",
    "Color del trazo": "Stroke color", "Color del segundo trazo": "Second stroke color",
    "Color inicial": "Start color", "Color final": "End color",
    "ÁNGULO": "ANGLE", "Color del resplandor": "Glow color",
    "Sombra paralela": "Drop shadow", "Color de la sombra": "Shadow color",
    "DESPLAZAMIENTO X": "X OFFSET", "DESPLAZAMIENTO Y": "Y OFFSET",
    "OPACIDAD DE SOMBRA": "SHADOW OPACITY", "Fusión": "Blend",
    "Trazo exterior": "Outer stroke",
    "Convertir esta capa en SFX vectorial": "Convert this layer to vector SFX",
    "Organizar automáticamente dentro de la caja": "Arrange automatically within the box",
    "Curva Bézier editable en el lienzo": "Editable Bézier curve on canvas",
    "ARCO / CURVA": "ARC / CURVE", "INCLINACIÓN": "TILT",
    "EXPANSIÓN": "EXPANSION", "IMPACTO / ROTACIÓN": "IMPACT / ROTATION",
    "Activar malla de deformación 3 × 3": "Enable 3 × 3 distortion mesh",
    "Restablecer perspectiva": "Reset perspective",
    "Conservar como forma editable": "Keep as editable shape",
    "Convertir texto a forma": "Convert text to shape",
    "SFX y texto especial": "SFX and special text",
    "Perspectiva por nodos": "Node perspective",

    # Settings and common dialogs.
    "Idioma de la interfaz": "Interface language", "Interfaz": "Interface",
    "Cambia los menús y controles de KuroPanel Studio. No modifica los idiomas de OCR ni de traducción.":
        "Change KuroPanel Studio menus and controls. OCR and translation languages stay unchanged.",
    "Configuración · Proveedores y seguridad": "Settings · Providers and security",
    "Credenciales": "Credentials", "Mostrar claves": "Show keys",
    "Clave guardada y cifrada para este usuario": "Key saved and encrypted for this user",
    "Pega aquí la clave del proveedor": "Paste the provider key here",
    "Sin configurar": "Not configured",
    "Los campos con puntos ya están guardados. Al pulsar Guardar se cifran con Windows DPAPI; si vacías un campo, esa clave se elimina.":
        "Masked fields are already saved. Save encrypts keys with Windows DPAPI; clearing a field removes that key.",
    "Modelo OCR": "OCR model", "URL de Alibaba OCR": "Alibaba OCR URL",
    "Copia la URL base de tu región y espacio de trabajo desde Model Studio. La clave y el modelo deben estar disponibles allí. Vacío usa el servidor internacional.":
        "Copy the base URL for your region and workspace from Model Studio. The key and model must be available there. Blank uses the international server.",
    "Proveedor de traducción": "Translation provider", "Modelo": "Model",
    "Idioma origen": "Source language", "Idioma destino": "Target language",
    "OCR y traducción": "OCR and translation",
    "Autoguardado (minutos)": "Autosave (minutes)",
    "Recargar PSD automáticamente al guardarlo en Photoshop":
        "Reload PSD automatically when saved in Photoshop",
    "Conserva el zoom, las cajas y los ajustes de capa al detectar Ctrl+S en Photoshop":
        "Keep zoom, boxes and layer settings when Ctrl+S is detected in Photoshop",
    "Sincronización PSD": "PSD sync", "Recuperación": "Recovery",
    "Las limpiezas permanecen como parches no destructivos; nunca se sobrescriben los originales.":
        "Cleaning stays as non-destructive patches; originals are never overwritten.",
    "Automático según el equipo": "Automatic for this computer",
    "Bajo consumo": "Low power", "Equilibrado": "Balanced",
    "Máximo rendimiento": "Maximum performance",
    "Automático (recomendado)": "Automatic (recommended)",
    "No descargar": "Do not unload",
    "Perfil de recursos": "Resource profile", "Procesador de modelos": "Model processor",
    "Liberar GPU sin uso": "Release idle GPU", "Caché de imágenes": "Image cache",
    "Comportamiento": "Behavior", "Rendimiento": "Performance",
    "1 página en RAM · texto solo en el área visible · sin precarga · 1 tarea de modelo.":
        "1 page in RAM · text only in the visible area · no preload · 1 model task.",
    "Página actual y vecinas · composición visible progresiva · 2 tareas de E/S.":
        "Current and nearby pages · progressive visible composition · 2 I/O tasks.",
    "Hasta 2 páginas vecinas por lado · cachés amplias · precarga y mayor paralelismo.":
        "Up to 2 nearby pages per side · larger caches · preloading and more parallelism.",
    "Automático usa CUDA cuando está disponible. Los lotes se ajustan a la VRAM libre y los modelos inactivos se descargan sin borrar OCR ni máscaras.":
        "Automatic uses CUDA when available. Batches adapt to free VRAM and idle models unload without deleting OCR or masks.",
    "Perfiles de fuentes": "Font profiles",
    "Escribe el identificador del modelo OCR.": "Enter the OCR model ID.",
    "Configuración OCR": "OCR settings",
    "Guardar cambios": "Save changes", "Cancelar": "Cancel",
    "Marca de agua": "Watermark",
    "Seleccionar PNG…": "Select PNG…", "Sin archivo seleccionado": "No file selected",
    "Repartir por el capítulo completo": "Distribute across the entire chapter",
    "Cantidad automática según la altura del capítulo": "Automatic count based on chapter height",
    "Columna alineada": "Aligned column",
    "Alternar izquierda y derecha": "Alternate left and right",
    "Evitar cajas de texto detectadas": "Avoid detected text boxes",
    "Dejar espacio en las uniones de páginas": "Leave space at page joins",
    "Mostrar previsualización durante la edición": "Show preview while editing",
    "Redistribuir esta página": "Redistribute this page",
    "Redistribuir todo el capítulo": "Redistribute entire chapter",
    "Tamaño, posición y apariencia": "Size, position and appearance",
    "Porcentaje del ancho de página": "Percent of page width",
    "Ancho exacto en píxeles": "Exact width in pixels",
    "Mantener dentro de la página": "Keep within page",
    "Quitar de página": "Remove from page",
    "Quitar de capítulo": "Remove from chapter",
    "Carga un PNG para comenzar.": "Load a PNG to begin.",
    "Distribución": "Distribution", "Tamaño": "Size",
    "Posición y alineación": "Position and alignment",
    "MARGEN HORIZONTAL": "HORIZONTAL MARGIN", "MARGEN VERTICAL": "VERTICAL MARGIN",
    "MODO DE FUSIÓN": "BLEND MODE",
    "SEPARACIÓN MÍNIMA ENTRE MARCAS": "MINIMUM WATERMARK SPACING",
    "CANTIDAD TOTAL EN EL CAPÍTULO": "TOTAL COUNT IN CHAPTER",
    "Seleccionar proyecto de trabajo": "Select working project",
    "Proyecto para este capítulo": "Project for this chapter",
    "TIPO DE PROYECTO": "PROJECT TYPE",
    "Buscar proyecto por nombre…": "Search projects by name…",
    "Selecciona un proyecto": "Select a project",
    "Usar este proyecto": "Use this project",
    "Todos los proyectos": "All projects",
    "No hay proyectos que coincidan con la búsqueda.": "No projects match your search.",
    "Aquí verás las fuentes y el glosario asociados.":
        "Associated fonts and glossary will appear here.",
    "Este proyecto todavía no tiene fuentes asignadas.":
        "This project has no assigned fonts yet.",
    "Biblioteca de perfiles": "Profile library",
    "Selecciona una biblioteca y un proyecto": "Select a library and project",
    "Selecciona primero una biblioteca y un proyecto.":
        "Select a library and project first.",
    "Los perfiles se guardan automáticamente dentro del programa. Sigue el orden: crea un tipo, crea un proyecto y asigna sus fuentes.":
        "Profiles are saved automatically in the app. Create a type, then a project, then assign its fonts.",
    "Segoe UI\nAa 123  ·  DIÁLOGO  ·  漢字  ·  한글":
        "Segoe UI\nAa 123  ·  DIALOGUE  ·  漢字  ·  한글",
    "Selecciona un tipo": "Select a type", "Selecciona un proyecto": "Select a project",
    "1 · Biblioteca": "1 · Library", "2 · Proyecto de traducción": "2 · Translation project",
    "+ Crear": "+ Create", "Renombrar": "Rename", "Eliminar": "Delete",
    "Crear una nueva biblioteca, por ejemplo Manhwas":
        "Create a new library, for example Manhwas",
    "Crear un proyecto dentro de la biblioteca seleccionada":
        "Create a project in the selected library",
    "Fuentes y estilos del proyecto": "Project fonts and styles",
    "Plantillas:": "Templates:",
    "Buscar rol o fuente en este proyecto…": "Search roles or fonts in this project…",
    "Nombre del rol (ej: Diálogo, Gritos, Narrador…)":
        "Role name (e.g. Dialogue, Shouts, Narrator…)",
    "Buscar fuente instalada o del proyecto…": "Search installed or project font…",
    "Nombre del rol": "Role name", "Fuente tipográfica": "Font family",
    "Tamaño base": "Base size", "Grosor / Peso": "Weight",
    "Énfasis": "Emphasis", "Mayúsculas": "Capitalization",
    "Cursiva": "Italic", "Subrayado": "Underline", "Tachado": "Strikethrough",
    "Vista previa": "Preview",
    "Completa el nombre de uso y selecciona una fuente.":
        "Enter a role name and select a font.",
    "Vaciar la selección y crear una asignación independiente":
        "Clear selection and create a separate assignment",
    "+ Agregar asignación": "+ Add assignment",
    "Importar TTF/OTF…": "Import TTF/OTF…",
    "Copiar una fuente externa dentro de este proyecto":
        "Copy an external font into this project",
    "Glosario y contexto de traducción del proyecto":
        "Project glossary and translation context",
    "Se usa automáticamente al traducir. Puedes pegar JSON, CSV, tablas Markdown, original=traducción, original: traducción, flechas, Tab o texto libre. El sistema lo normaliza al guardar y la API mantiene esos términos.":
        "Used automatically for translation. Paste JSON, CSV, Markdown tables, original=translation, original: translation, arrows, tabs or free text. The app normalizes it when saving and the API keeps those terms.",
    "Guardar contexto": "Save context", "Copiar glosario": "Copy glossary",
    "Pegar y combinar": "Paste and merge", "Restaurar prompt": "Restore prompt",
    "Selecciona un proyecto para editar su contexto.":
        "Select a project to edit its context.",
    "Nuevo tipo de proyecto": "New project type",
    "Nombre del tipo (ejemplo: Manhwas):": "Type name (example: Manhwas):",
    "Nuevo proyecto": "New project",
    "Nombre del proyecto (ejemplo: Valiente):": "Project name (example: Valiente):",
    "Renombrar tipo": "Rename type", "Nuevo nombre del tipo de proyecto:": "New project type name:",
    "Eliminar tipo de proyecto": "Delete project type",
    "Renombrar proyecto": "Rename project", "Nuevo nombre del proyecto:": "New project name:",
    "Eliminar proyecto": "Delete project",
    "Cambios pendientes · se guardarán automáticamente.":
        "Pending changes · they will be saved automatically.",
    "Glosario copiado. Puedes pegarlo en otro proyecto o editor.":
        "Glossary copied. You can paste it into another project or editor.",
    "Nueva asignación: escribe un nombre y elige su fuente y estilo.":
        "New assignment: enter a name and choose its font and style.",
    "Importar fuente": "Import font",
    "Color de la fuente": "Font color",
    "Cambios sin guardar. Revisa el nombre y pulsa Guardar.":
        "Unsaved changes. Review the name and click Save.",
    "Normal (400)": "Normal (400)", "Seminegrita (600)": "Semibold (600)",
    "Negrita / Bold (700)": "Bold (700)",
    "Extra negrita (800)": "Extra bold (800)",
    "PROMPT PERMANENTE": "PERMANENT PROMPT",
    "GLOSARIO · ORIGINAL → TRADUCCIÓN → CATEGORÍA → NOTA":
        "GLOSSARY · ORIGINAL → TRANSLATION → CATEGORY → NOTE",
    "Crea o selecciona primero un tipo de proyecto.":
        "Create or select a project type first.",
    "Como fue escrito (Original)": "As written (Original)",
    "Nombre de uso": "Role name", "Nombre que aparecerá en Texto:": "Name shown in Text:",
    "Fuente seleccionada:": "Selected font:",
    "No se encontró ninguna fuente con ese nombre.":
        "No font with that name was found.",
    "Las fuentes siguientes pertenecen solamente a este proyecto.":
        "The following fonts belong only to this project.",
    "MAYÚSCULAS": "CAPITALIZATION",
    "Selecciona un tipo y un proyecto antes de asignar fuentes.":
        "Select a type and project before assigning fonts.",
    "Selecciona una fuente válida de los resultados del menú.":
        "Select a valid font from the menu results.",
    "Selecciona un tipo y un proyecto antes de importar una fuente.":
        "Select a type and project before importing a font.",

    # Progress and status.
    "Sin imagen": "No image", "Sin operaciones medidas": "No measured operations",
    "●  Listo": "●  Ready", "●  Procesando": "●  Processing",
    "Detalles": "Details", "Ver detalle": "View details",
    "Cancelar operación en curso": "Cancel current operation",
    "Progreso de la operación": "Operation progress",
    "Error de proceso": "Processing error",
    "Listo": "Ready", "Procesando…": "Processing…", "Completado": "Completed",
    "Sin imagen · 0 × 0 px": "No image · 0 × 0 px",
}

# Short-lived operation names, warnings and progress copy also pass through
# the same presentation layer when a toast, menu or task bar is shown.
ES_TO_EN.update({
    "Color tomado": "Color picked",
    "Retoque eliminado": "Retouch removed",
    "Tiras eliminadas": "Strips removed",
    "Página copiada": "Page copied", "Capítulo copiado": "Chapter copied",
    "Caja copiada": "Box copied", "Texto distribuido": "Text distributed",
    "Capa eliminada": "Layer removed", "Capa consolidada": "Layer consolidated",
    "No se pudo sincronizar el PSD": "Could not sync PSD",
    "Proyecto asignado": "Project assigned", "Proyecto cambiado": "Project changed",
    "Preset guardado": "Preset saved", "Efectos copiados": "Effects copied",
    "Efectos pegados": "Effects pasted", "Estilo guardado": "Style saved",
    "Falta la marca": "Watermark missing", "Marca aplicada": "Watermark applied",
    "Tipografía aplicada": "Typesetting applied", "Detección YOLO": "YOLO detection",
    "OCR completado": "OCR complete", "OCR del capítulo": "Chapter OCR",
    "Traducción": "Translation", "Traducción del capítulo": "Chapter translation",
    "Máscara de texto": "Text mask", "Limpieza": "Cleaning",
    "Limpieza de capítulo": "Chapter cleaning",
    "Texto aplicado": "Text applied", "Exportación": "Export",
    "Exportación del capítulo": "Chapter export",
    "Proyecto guardado": "Project saved", "Proyecto abierto": "Project opened",
    "Pincel de color · P": "Color brush · P",
    "Trazo descartado": "Stroke discarded", "Capa bloqueada": "Layer locked",
    "Sin retoques": "No retouches", "Sin tiras": "No strips",
    "Proceso en curso": "Operation in progress", "Sin página": "No page",
    "Sin texto": "No text", "Caja sin texto": "Empty box",
    "Sin capítulo": "No chapter", "Portapapeles vacío": "Clipboard empty",
    "Nada que consolidar": "Nothing to consolidate",
    "Sin PSD activo": "No active PSD", "Imagen sincronizada": "Image synced",
    "Biblioteca cambiada": "Library changed",
    "Selecciona una capa": "Select a layer",
    "Sin efectos copiados": "No copied effects", "Sin proyectos": "No projects",
    "Proceso cancelado": "Operation cancelled",
    "No se pudo cargar la imagen": "Could not load image",
    "Carpeta no válida": "Invalid folder", "Cargar capítulo": "Load chapter",
    "No se pudo cargar el capítulo": "Could not load chapter",
    "Sin imágenes": "No images", "Abre un capítulo": "Open a chapter",
    "Selecciona una caja": "Select a box",
    "Sin cajas de texto": "No text boxes", "Sin OCR": "No OCR",
    "Glosario actualizado": "Glossary updated",
    "No hay regiones": "No regions", "Limpieza bloqueada": "Cleaning locked",
    "No hay cajas": "No boxes", "Sin máscara": "No mask",
    "Máscara cancelada": "Mask cancelled", "Capas bloqueadas": "Locked layers",
    "No hay cajas en el capítulo": "No boxes in the chapter",
    "Sin tinta detectada": "No text ink detected",
    "Sin limpieza para revisar": "No cleaning to review",
    "Sin máscara editable": "No editable mask",
    "Texto o capa faltante": "Missing text or layer",
    "Sin proyecto": "No project", "Proyecto incompleto": "Incomplete project",
    "Trabajo recuperado": "Work recovered",
    "Páginas no reconocidas": "Unrecognized pages",
    "PSD actualizado": "PSD up to date", "PSD pendiente": "PSD pending",
    "OCR ya disponible": "OCR already available",
    "OCR mediante API": "OCR via API",
    "Traducción ya disponible": "Translation already available",
    "No se pudo guardar": "Could not save", "No se pudo abrir": "Could not open",
    "No se pudo recuperar": "Could not recover",
    "Corrector puntual · H": "Spot healing · H",
    "Restaurar original · R": "Restore original · R",
    "Pincel de máscara · M": "Mask brush · M",
    "Editor de máscara": "Mask editor", "Cuentagotas": "Eyedropper",
    "Selecciones eliminadas": "Selections removed",
    "Función sin backend": "Function unavailable",
    "Se quitó el último parche manual de esta página.":
        "The latest manual patch on this page was removed.",
    "Se retiraron sus parches sin modificar la imagen original.":
        "Its patches were removed without changing the original image.",
    "Puedes pegarlos en una o varias capas seleccionadas.":
        "You can paste them onto one or more selected layers.",
    "Se restauró el cambio anterior.": "The previous change was restored.",
    "Se volvió a aplicar el cambio.": "The change was applied again.",
    "Las regiones reales se dibujaron sobre el canvas.":
        "Detected regions were drawn on the canvas.",
    "El texto reconocido se insertó en las cajas y en el panel de aplicación.":
        "Recognized text was inserted into boxes and the apply panel.",
    "Las traducciones quedaron editables por capa.":
        "Translations remain editable per layer.",
    "La máscara está lista para revisión.": "The mask is ready for review.",
    "La máscara revisada se aplicó sin modificar las zonas borradas.":
        "The reviewed mask was applied without changing erased areas.",
    "Se detectó un salto fuera del lienzo; no se modificó la imagen.":
        "A stroke jumped outside the canvas; the image was not changed.",
    "Desbloquea la capa de retoque antes de modificarla.":
        "Unlock the retouch layer before editing it.",
    "Esta página no tiene parches manuales.": "This page has no manual patches.",
    "No hay un parche manual que eliminar.": "There is no manual patch to remove.",
    "Esta página no tiene retoques guardados.": "This page has no saved retouches.",
    "No se encontraron parches con el patrón defectuoso.":
        "No patches with the faulty pattern were found.",
    "La máscara contenía un salto fuera del lienzo.":
        "The mask contained a stroke outside the canvas.",
    "Espera a que termine la tarea actual.": "Wait for the current task to finish.",
    "Abre un capítulo antes de copiar texto.": "Open a chapter before copying text.",
    "La página todavía no contiene OCR ni texto pendiente.":
        "This page has no OCR or pending text yet.",
    "El capítulo todavía no contiene OCR ni texto pendiente.":
        "This chapter has no OCR or pending text yet.",
    "La caja seleccionada todavía no contiene texto.":
        "The selected box has no text yet.",
    "Abre un capítulo antes de pegar texto.": "Open a chapter before pasting text.",
    "Copia primero el OCR o las traducciones.": "Copy OCR or translations first.",
    "Desbloquea la capa antes de eliminarla.": "Unlock the layer before deleting it.",
    "Desbloquea la capa antes de consolidarla.":
        "Unlock the layer before consolidating it.",
    "Esta capa necesita al menos dos parches.":
        "This layer needs at least two patches.",
    "Selecciona una página PSD o PSB para sincronizarla.":
        "Select a PSD or PSB page to sync.",
    "El preset debe partir de una capa de texto.":
        "The preset must start from a text layer.",
    "Copia primero los efectos de una capa.": "Copy effects from a layer first.",
    "Abre Ajustes para crear tu primer proyecto de traducción.":
        "Open Settings to create your first translation project.",
    "Elige la caja cuyo texto deseas componer.":
        "Choose the box whose text you want to typeset.",
    "Espera a que termine la limpieza, OCR o traducción antes de deshacer.":
        "Wait for cleaning, OCR or translation to finish before undoing.",
    "Espera a que termine la limpieza, OCR o traducción antes de rehacer.":
        "Wait for cleaning, OCR or translation to finish before redoing.",
    "No hay cambios anteriores.": "There are no earlier changes.",
    "No hay cambios posteriores.": "There are no later changes.",
    "No se aplicaron resultados de la tarea.": "No task results were applied.",
    "Arrastra una carpeta local que contenga imágenes.":
        "Drop a local folder containing images.",
    "Leyendo imágenes…": "Reading images…",
    "La carpeta no contiene imágenes compatibles.":
        "The folder has no supported images.",
    "Selecciona una carpeta con imágenes antes de ejecutar YOLO.":
        "Select an image folder before running YOLO.",
    "Haz clic en una caja antes de ejecutar OCR individual.":
        "Click a box before running OCR on it.",
    "Espera a que termine la operación actual.":
        "Wait for the current operation to finish.",
    "Selecciona una carpeta con imágenes antes de ejecutar OCR.":
        "Select an image folder before running OCR.",
    "Primero ejecuta Autodetectar texto (YOLO).":
        "Run automatic text detection (YOLO) first.",
    "Carga imágenes antes de ejecutar OCR por lote.":
        "Load images before running batch OCR.",
    "Detecta o dibuja cajas en las páginas primero.":
        "Detect or draw boxes on the pages first.",
    "Ejecuta OCR o escribe texto en las cajas antes de traducir.":
        "Run OCR or enter text in boxes before translating.",
    "Ninguna capa contiene texto original para traducir.":
        "No layer has source text to translate.",
    "Ejecuta la detección antes de usar LaMa.":
        "Run detection before using LaMa.",
    "Desbloquea la capa Limpieza automática.":
        "Unlock the Automatic cleaning layer.",
    "Detecta o dibuja una caja antes de limpiar.":
        "Detect or draw a box before cleaning.",
    "Haz clic en una caja del lienzo y pulsa Una capa.":
        "Click a canvas box and choose One layer.",
    "No se detectó tinta de texto dentro de las cajas.":
        "No text ink was detected inside the boxes.",
    "El borrador salió del lienzo y no se aplicó.":
        "The eraser went outside the canvas and was not applied.",
    "Primero genera y revisa la máscara OCR.":
        "Generate and review the OCR mask first.",
    "No se aplicó ninguna limpieza.": "No cleaning was applied.",
    "Carga imágenes antes de limpiar el capítulo.":
        "Load images before cleaning the chapter.",
    "Detecta o dibuja cajas en las páginas antes de ejecutar el lote.":
        "Detect or draw boxes on the pages before running the batch.",
    "Desbloquea la capa Limpieza automática para aplicar el resultado.":
        "Unlock the Automatic cleaning layer to apply the result.",
    "Ajusta las cajas al texto antes de limpiar.":
        "Fit boxes to the text before cleaning.",
    "Aplica primero la limpieza automática a una o más cajas.":
        "Apply automatic cleaning to one or more boxes first.",
    "Repite la detección de esta caja para reconstruirla.":
        "Detect this box again to rebuild it.",
    "Selecciona una capa y escribe su texto final.":
        "Select a layer and enter its final text.",
    "Abre un capítulo antes de guardar.": "Open a chapter before saving.",
    "Abre un capítulo antes de exportar.": "Open a chapter before exporting.",
    "Abre un capítulo antes de guardar el proyecto.":
        "Open a chapter before saving the project.",
    "No se encontró ninguna imagen original del proyecto.":
        "No original project image was found.",
    "Guárdalo como proyecto para conservar esta sesión.":
        "Save it as a project to keep this session.",
    "Desbloquea esa capa de retoque antes de pintar.":
        "Unlock that retouch layer before painting.",
    "Los encabezados no coinciden con las imágenes del capítulo.":
        "The headers do not match the chapter images.",
    "No se encontraron líneas para distribuir.":
        "No lines were found to distribute.",
    "El archivo activo ya coincide con Photoshop.":
        "The active file already matches Photoshop.",
    "OCR ya completado · sin solicitudes nuevas":
        "OCR already complete · no new requests",
    "Todas las cajas de esta página ya contienen OCR.":
        "All boxes on this page already have OCR.",
    "OCR del capítulo ya completado": "Chapter OCR already complete",
    "Todas las cajas del capítulo ya contienen OCR.":
        "All chapter boxes already have OCR.",
    "Haz clic en una caja antes de traducirla.":
        "Click a box before translating it.",
    "Página ya traducida · sin solicitudes nuevas":
        "Page already translated · no new requests",
    "Todas las cajas de esta página ya están traducidas.":
        "All boxes on this page are already translated.",
    "Capítulo ya traducido": "Chapter already translated",
    "Todas las cajas del capítulo ya están traducidas.":
        "All chapter boxes are already translated.",
    "Ninguna página contiene texto original para traducir.":
        "No page has source text to translate.",
    "Pinta sobre manchas o imperfecciones para fusionar la textura.":
        "Paint over marks or imperfections to blend the texture.",
    "Pinta para recuperar exactamente los píxeles de la imagen original.":
        "Paint to restore exact pixels from the original image.",
    "Pinta la máscara, revísala en rojo y pulsa Aplicar limpieza.":
        "Paint the mask, review it in red and click Apply cleaning.",
    "Pinta sobre las zonas rojas que LaMa no debe modificar.":
        "Paint over red areas that LaMa must not change.",
    "Haz clic sobre el color que deseas tomar.":
        "Click the color you want to pick.",
    "Se quitaron las regiones seleccionadas.":
        "Selected regions were removed.",
})


PATTERNS = (
    (r"^Editando capa (\d+) · los cambios se ven en tiempo real$",
     r"Editing layer \1 · changes appear live"),
    (r"^Fuente manual  ·  (.+)$", r"Manual font  ·  \1"),
    (r"^Rol (.+)  ·  (.+)$", r"Role \1  ·  \2"),
    (r"^Ya tienes la versión (\d+\.\d+\.\d+)\.$", r"You already have version \1."),
    (r"^KuroPanel Studio (\d+\.\d+\.\d+) está disponible\.\nVersión actual: (\d+\.\d+\.\d+)\.$",
     r"KuroPanel Studio \1 is available.\nCurrent version: \2."),
    (r"^Páginas \((\d+)\)$", r"Pages (\1)"),
    (r"^Capítulo actual: (\d+) imágenes$", r"Current chapter: \1 images"),
    (r"^Capítulo actual: (\d+) imágenes · (\d+) rotuladas$",
     r"Current chapter: \1 images · \2 typeset"),
    (r"^Carpeta: (.+?)(?: · (\d+) rotuladas)?$", None),
    (r"^(\d+) imágenes disponibles\.$", r"\1 images available."),
    (r"^(\d+) imagen disponible\.$", r"\1 image available."),
    (r"^Página (\d+) de (\d+)$", r"Page \1 of \2"),
    (r"^Caja #(\d+) · OCR y traducción$", r"Box #\1 · OCR and translation"),
    (r"^Capas PSD \((\d+)\)$", r"PSD layers (\1)"),
    (r"^Retoque · (\d+) parches$", r"Retouch · \1 patches"),
    (r"^(\d+) diálogos$", r"\1 dialogues"),
    (r"^Sesión OCR: (\d+) solicitud\(es\)$", r"OCR session: \1 requests"),
    (r"^●  Configura (.+) y su modelo$", r"●  Configure \1 and its model"),
    (r"^Configura (.+) y su modelo$", r"Configure \1 and its model"),
    (r"^Última: (.+) · (.+)$", r"Last: \1 · \2"),
    (r"^Detectado: ([\d.]+) GB RAM · (\d+) núcleos físicos · perfil recomendado (.+)\.$",
     r"Detected: \1 GB RAM · \2 physical cores · recommended profile \3."),
    (r"^(.+) · (\d+) rotuladas$", r"\1 · \2 typeset"),
)

EN_TO_ES = {english: spanish for spanish, english in ES_TO_EN.items()}


def translate_text(source: str, language: str) -> str:
    """Translate only known UI copy; never guess at user or document text."""
    if not source:
        return source
    if language != "en":
        return EN_TO_ES.get(source, source)
    direct = ES_TO_EN.get(source)
    if direct is not None:
        return direct
    for expression, replacement in PATTERNS:
        match = re.fullmatch(expression, source)
        if match:
            if replacement is None:  # folder name is user data
                return f"Folder: {match[1]}" + (f" · {match[2]} typeset" if match[2] else "")
            return match.expand(replacement)
    return source


class UiTranslator(QObject):
    """Translate static and newly updated Qt text without changing control IDs."""

    def __init__(self, app, language: str = "es") -> None:
        super().__init__(app)
        self.app = app
        self.language = language if language in {"es", "en"} else "es"
        self._busy = False
        app.installEventFilter(self)

    def set_language(self, language: str) -> None:
        self.language = language if language in {"es", "en"} else "es"
        for window in self.app.topLevelWidgets():
            self.refresh(window)

    def eventFilter(self, watched, event):
        if self._busy or self.language != "en":
            return False
        if event.type() == QEvent.Show and isinstance(watched, QWidget):
            self.refresh(watched)
        elif event.type() == QEvent.Paint and isinstance(
            watched, (QLabel, QAbstractButton, QGroupBox, QLineEdit,
                      QTextEdit, QPlainTextEdit, QSpinBox, QTabBar, QTabWidget, QComboBox),
        ):
            self._busy = True
            try:
                self._translate_one(watched)
                if isinstance(watched, QTabBar) and isinstance(watched.parentWidget(), QTabWidget):
                    self._translate_one(watched.parentWidget())
            finally:
                self._busy = False
        elif event.type() == QEvent.ToolTip:
            self._busy = True
            try:
                self._translate_one(watched)
            finally:
                self._busy = False
        return False

    def refresh(self, root: QObject) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._translate_one(root)
            for child in root.findChildren(QWidget):
                self._translate_one(child)
            for action in root.findChildren(QAction):
                self._translate_one(action)
        finally:
            self._busy = False

    def _field(self, obj, key: str, current: str, setter) -> None:
        source_key = f"_kuro_i18n_source_{key}"
        rendered_key = f"_kuro_i18n_rendered_{key}"
        language_key = f"_kuro_i18n_language_{key}"
        rendered = obj.property(rendered_key)
        if not current and rendered is None:
            return
        if rendered is not None and current == rendered and obj.property(language_key) == self.language:
            return
        if rendered is None or current != rendered:
            obj.setProperty(source_key, current)
        source = obj.property(source_key)
        translated = translate_text(str(source or ""), self.language)
        if current != translated:
            setter(translated)
        obj.setProperty(rendered_key, translated)
        obj.setProperty(language_key, self.language)

    def _indexed(self, obj, key: str, count: int, getter, setter) -> None:
        state = dict(obj.property(f"_kuro_i18n_{key}") or {})
        for index in range(count):
            current = getter(index)
            source, rendered = state.get(index, (current, None))
            if current != rendered:
                source = current
            translated = translate_text(source, self.language)
            if current != translated:
                was_blocked = obj.blockSignals(True)
                try:
                    setter(index, translated)
                finally:
                    obj.blockSignals(was_blocked)
            state[index] = (source, translated)
        obj.setProperty(f"_kuro_i18n_{key}", state)

    def _translate_one(self, obj) -> None:
        if not isinstance(obj, (QWidget, QAction)):
            return
        if obj.property("kuro_i18n_ignore"):
            return
        if isinstance(obj, QWidget):
            self._field(obj, "title", obj.windowTitle(), obj.setWindowTitle)
            self._field(obj, "tooltip", obj.toolTip(), obj.setToolTip)
            self._field(obj, "accessible", obj.accessibleName(), obj.setAccessibleName)
        if isinstance(obj, QAction):
            self._field(obj, "text", obj.text(), obj.setText)
            self._field(obj, "tooltip", obj.toolTip(), obj.setToolTip)
        if isinstance(obj, QLabel):
            self._field(obj, "text", obj.text(), obj.setText)
        elif isinstance(obj, QAbstractButton):
            self._field(obj, "text", obj.text(), obj.setText)
        elif isinstance(obj, QGroupBox):
            self._field(obj, "group_title", obj.title(), obj.setTitle)
        if isinstance(obj, (QLineEdit, QTextEdit, QPlainTextEdit)):
            self._field(obj, "placeholder", obj.placeholderText(), obj.setPlaceholderText)
        if isinstance(obj, QSpinBox):
            self._field(obj, "special_value", obj.specialValueText(), obj.setSpecialValueText)
        if isinstance(obj, QTabWidget):
            self._indexed(obj, "tabs", obj.count(), obj.tabText, obj.setTabText)
        if isinstance(obj, QComboBox) and obj.property("kuro_i18n_choices"):
            # Opt in only fixed choices with stable IDs. Project names, font
            # roles and model IDs are user data and must never be translated.
            indices = [index for index in range(obj.count()) if obj.itemData(index) is not None]
            if indices:
                state = dict(obj.property("_kuro_i18n_combo") or {})
                for index in indices:
                    current = obj.itemText(index)
                    source, rendered = state.get(index, (current, None))
                    if current != rendered:
                        source = current
                    translated = translate_text(source, self.language)
                    if current != translated:
                        was_blocked = obj.blockSignals(True)
                        try:
                            obj.setItemText(index, translated)
                        finally:
                            obj.blockSignals(was_blocked)
                    state[index] = (source, translated)
                obj.setProperty("_kuro_i18n_combo", state)
