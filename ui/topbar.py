"""Primary document actions and an always-accessible application menu."""
from pathlib import Path
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from ui.widgets.controls import ModernButton
from ui.widgets.icons import icon


class TopBar(QFrame):
    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(70)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 16, 0)
        layout.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("BrandLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(42, 42)
        logo_path = Path(__file__).resolve().parents[1] / "assets/sample/kuro-logo.png"
        logo.setPixmap(QPixmap(str(logo_path)).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                       if logo_path.exists() else icon("logo", "#FFC928", 28).pixmap(QSize(28, 28)))
        layout.addWidget(logo)
        brand_box = QFrame()
        brand_layout = QVBoxLayout(brand_box)
        brand_layout.setContentsMargins(0, 0, 18, 0)
        brand_layout.setSpacing(1)
        self.brand = QLabel("KuroPanel Studio")
        self.brand.setObjectName("BrandTitle")
        brand_layout.addWidget(self.brand)
        self.subtitle = QLabel("Espacio de edición de manhuas")
        self.subtitle.setObjectName("BrandCaption")
        brand_layout.addWidget(self.subtitle)
        layout.addWidget(brand_box)
        self.open_button = ModernButton("Abrir", "HeaderNav", icon_name="folder")
        self.open_button.setToolTip("Abrir capítulo (Ctrl+O) o archivo PSD/PSB")
        open_menu = QMenu(self.open_button)
        for label, action in (("Abrir carpeta del capítulo…", "open_folder"), ("Abrir archivo PSD/PSB…", "open_psd")):
            open_menu.addAction(label).triggered.connect(lambda _, value=action: self.action_requested.emit(value))
        self.open_button.setMenu(open_menu)
        layout.addWidget(self.open_button)
        self.save_button = ModernButton("Guardar", "HeaderNav", icon_name="save")
        self.save_button.setToolTip("Guardar proyecto (Ctrl+S)")
        self.save_button.clicked.connect(lambda: self.action_requested.emit("save_project"))
        layout.addWidget(self.save_button)
        self.export_button = ModernButton("Exportar", "HeaderNav", icon_name="download")
        self.export_button.setToolTip("Exportar la página actual")
        self.export_button.clicked.connect(lambda: self.action_requested.emit("save_current"))
        layout.addWidget(self.export_button)
        layout.addStretch(1)
        self.history_buttons = {}
        for icon_name, action, tip in (("undo", "undo", "Deshacer (Ctrl+Z)"), ("redo", "redo", "Rehacer (Ctrl+Y)")):
            button = ModernButton("", "HeaderNav", icon_name=icon_name)
            button.setFixedWidth(34)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(lambda _, value=action: self.action_requested.emit(value))
            self.history_buttons[action] = button
            layout.addWidget(button)
        self.focus_button = ModernButton("", "HeaderNav", icon_name="maximize")
        self.focus_button.setCheckable(True)
        self.focus_button.setToolTip("Ocultar o recuperar paneles (Ctrl+Shift+F)")
        self.focus_button.setAccessibleName("Ocultar o recuperar paneles")
        self.focus_button.clicked.connect(lambda: self.action_requested.emit("toggle_focus"))
        layout.addWidget(self.focus_button)
        self.menu_button = ModernButton("", "HeaderNav", icon_name="settings")
        self.menu_button.setFixedWidth(38)
        self.menu_button.setToolTip("Proyecto, configuración y marca de agua")
        self.menu_button.setAccessibleName("Menú de aplicación")
        self.app_menu = QMenu(self.menu_button)
        for label, action in (("Cambiar proyecto…", "switch_project"), ("Configuración…", "settings"),
                              ("Marca de agua…", "watermark"), ("Buscar actualizaciones…", "check_updates")):
            self.app_menu.addAction(label).triggered.connect(lambda _, value=action: self.action_requested.emit(value))
        self.menu_button.setMenu(self.app_menu)
        layout.addWidget(self.menu_button)

    def set_focus_mode(self, enabled: bool) -> None:
        self.focus_button.setChecked(enabled)

    def adapt_to_width(self, width: int) -> None:
        self.subtitle.setVisible(width >= 900)
        self.brand.setText("KuroPanel" if width < 900 else "KuroPanel Studio")
        self.focus_button.setText("")
        self.save_button.setText("" if width < 900 else "Guardar")
