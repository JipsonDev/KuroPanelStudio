from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QWidget

from ui.widgets.controls import ModernButton
from ui.widgets.icons import icon


class TopBar(QFrame):
    """Dense header with vector-only controls and clear editor identity."""

    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(54)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 11, 0)
        layout.setSpacing(8)

        logo = QLabel()
        logo.setObjectName("BrandLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(30, 30)
        logo.setPixmap(icon("logo", "#F5F7FA", 20).pixmap(QSize(20, 20)))
        logo.setStyleSheet("background:#0797AC; border:1px solid #00CFE8; border-radius:8px;")
        layout.addWidget(logo)
        brand_box = QFrame()
        brand_layout = QHBoxLayout(brand_box)
        brand_layout.setContentsMargins(0, 0, 8, 0)
        brand_layout.setSpacing(7)
        brand = QLabel("KuroPanel Studio")
        brand.setObjectName("BrandTitle")
        brand.setStyleSheet("font-size:14px; font-weight:700;")
        brand_layout.addWidget(brand)
        pro = QLabel("PRO")
        pro.setStyleSheet("color:#00CFE8; border:1px solid #28738A; border-radius:3px; padding:2px 4px; font-size:9px; font-weight:700;")
        brand_layout.addWidget(pro)
        layout.addWidget(brand_box)

        self.nav_buttons: list[tuple[ModernButton, str]] = []
        open_button = ModernButton("Abrir", "HeaderNav", icon_name="folder")
        open_menu = QMenu(open_button)
        open_folder = open_menu.addAction("Abrir carpeta del capítulo…")
        open_psd = open_menu.addAction("Abrir archivo PSD/PSB…")
        open_folder.triggered.connect(lambda: self.action_requested.emit("open_folder"))
        open_psd.triggered.connect(lambda: self.action_requested.emit("open_psd"))
        open_button.setMenu(open_menu)
        layout.addWidget(open_button)
        self.nav_buttons.append((open_button, "Abrir"))
        project_button = ModernButton("Proyecto", "HeaderNav", icon_name="edit")
        project_button.setIconSize(QSize(15, 15))
        project_button.setToolTip("Cambiar biblioteca o proyecto de traducción activo")
        project_button.clicked.connect(lambda: self.action_requested.emit("switch_project"))
        layout.addWidget(project_button)
        self.nav_buttons.append((project_button, "Proyecto"))
        settings = ModernButton("Configuración", "HeaderNav", icon_name="settings")
        settings.setIconSize(QSize(15, 15))
        settings.clicked.connect(lambda: self.action_requested.emit("settings"))
        layout.addWidget(settings)
        self.nav_buttons.append((settings, "Configuración"))
        watermark = ModernButton("Marca de agua", "HeaderNav", icon_name="stamp")
        watermark.setIconSize(QSize(15, 15))
        watermark.clicked.connect(lambda: self.action_requested.emit("watermark"))
        layout.addWidget(watermark)
        self.nav_buttons.append((watermark, "Marca de agua"))
        layout.addStretch()

        for icon_name, action, tooltip in (("undo", "undo", "Deshacer (Ctrl+Z)"), ("redo", "redo", "Rehacer (Ctrl+Y)")):
            history_button = ModernButton("", "HeaderNav", icon_name=icon_name)
            history_button.setFixedWidth(34)
            history_button.setToolTip(tooltip)
            history_button.clicked.connect(lambda _, value=action: self.action_requested.emit(value))
            layout.addWidget(history_button)

        export = ModernButton("Exportar", "Coral", icon_name="download")
        export.setIconSize(QSize(15, 15))
        export.setMinimumWidth(108)
        export.clicked.connect(lambda: self.action_requested.emit("save_current"))
        layout.addWidget(export)

    def adapt_to_width(self, width: int) -> None:
        """Keep the toolbar inside narrow laptop screens without wrapping."""
        compact = width < 1360
        minimal = width < 1120
        for index, (button, label) in enumerate(self.nav_buttons):
            button.setText("" if compact else label)
            button.setToolTip(label)
            button.setVisible(not minimal or index == 0)
        brand = self.findChild(QLabel, "BrandTitle")
        if brand:
            brand.setVisible(width >= 1020)
