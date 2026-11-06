"""Actionable, drop-enabled empty state for the canvas."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from ui.widgets.controls import ModernButton


class WelcomePanel(QWidget):
    action_requested = Signal(str)
    path_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addStretch()
        card = QFrame()
        card.setObjectName("WelcomeCard")
        card.setMaximumWidth(460)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 28, 26, 28)
        layout.setSpacing(16)
        for text, name in (
            ("Abre un capítulo", "WelcomeTitle"),
            ("Arrastra una carpeta de imágenes o abre un proyecto guardado.", "WelcomeDescription"),
        ):
            label = QLabel(text)
            label.setObjectName(name)
            label.setWordWrap(True)
            layout.addWidget(label)
            if name == "WelcomeTitle":
                self.title = label
        open_button = ModernButton("Abrir capítulo", "Primary", icon_name="folder")
        open_button.clicked.connect(lambda: self.action_requested.emit("open_folder"))
        open_button.setToolTip("Abrir una carpeta de imágenes (Ctrl+O)")
        layout.addWidget(open_button)
        psd_button = ModernButton("Abrir PSD / PSB", icon_name="layers")
        psd_button.clicked.connect(lambda: self.action_requested.emit("open_psd"))
        layout.addWidget(psd_button)
        project_button = ModernButton("Abrir proyecto editable", icon_name="folder")
        project_button.clicked.connect(lambda: self.action_requested.emit("open_project"))
        layout.addWidget(project_button)
        steps = QLabel("Detecta las cajas en Procesar, revisa el texto y exporta desde la cabecera.")
        steps.setWordWrap(True)
        steps.setObjectName("WelcomeSteps")
        self.steps = steps
        layout.addWidget(steps)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(card, 1)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 400
        self.title.setText("Abre un capítulo")
        self.title.setWordWrap(True)
        self.steps.setVisible(self.height() >= 380)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.path_dropped.emit(url.toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()
