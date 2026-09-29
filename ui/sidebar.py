from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ui.widgets.controls import SidebarButton


class Sidebar(QFrame):
    tool_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(88)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("SidebarScroll")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.verticalScrollBar().setSingleStep(72)
        content = QWidget()
        content.setObjectName("SidebarContent")
        layout = QVBoxLayout(content)
        self.button_layout = layout
        layout.setContentsMargins(3, 10, 3, 10)
        layout.setSpacing(9)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)
        self.buttons: list[SidebarButton] = []
        # Only workspace tools live here. Settings stays in the header, so the
        # rail does not expose duplicate destinations or placeholder actions.
        for icon_name, name in (("edit", "Editar"), ("images", "Páginas"),
                                ("layers", "Capas"), ("scan", "Procesar"),
                                ("script", "Guión"), ("text", "Texto"),
                                ("effects", "Efectos"), ("sparkles", "SFX")):
            button = SidebarButton(icon_name, name)
            button.setFixedSize(72, 64)
            button.selected.connect(self._select)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()
        self._select("Páginas")

    def _select(self, name: str) -> None:
        self.set_current(name, emit=True)

    def set_current(self, name: str, *, emit: bool = False) -> None:
        for button in self.buttons:
            button.set_active(button.tool_name == name)
            if button.tool_name == name:
                self.scroll.ensureWidgetVisible(button, 0, 12)
        if emit:
            self.tool_changed.emit(name)
