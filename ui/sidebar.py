from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ui.widgets.controls import SidebarButton


class Sidebar(QFrame):
    tool_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(64)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 10)
        layout.setSpacing(8)
        self.buttons: list[SidebarButton] = []
        # Only workspace tools live here. Settings stays in the header, so the
        # rail does not expose duplicate destinations or placeholder actions.
        for icon_name, name in (("script", "Guión"), ("text", "Texto"), ("effects", "Efectos"), ("sparkles", "SFX"), ("tools", "Procesar")):
            button = SidebarButton(icon_name, name)
            button.selected.connect(self._select)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()
        self._select("Procesar")

    def _select(self, name: str) -> None:
        for button in self.buttons:
            button.set_active(button.tool_name == name)
        self.tool_changed.emit(name)
