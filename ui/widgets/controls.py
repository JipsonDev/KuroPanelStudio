from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget
from ui.widgets.icons import icon


class ModernButton(QPushButton):
    def __init__(self, text: str = "", role: str = "", parent: QWidget | None = None, icon_name: str | None = None) -> None:
        super().__init__(text, parent)
        if role:
            self.setObjectName(role)
        self.setCursor(Qt.PointingHandCursor)
        if icon_name:
            self.setIcon(icon(icon_name, "#F5F7FA" if role in {"Primary", "Coral", "ToolActive"} else "#AAB8C5"))
            self.setIconSize(QSize(16, 16))


class SectionTitle(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text.upper())
        self.setObjectName("SectionTitle")


class CollapsibleSection(QWidget):
    """Compact disclosure section used by dense editor side panels."""

    def __init__(self, title: str, expanded: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.header = QToolButton()
        self.header.setObjectName("DisclosureHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.toggled.connect(self.set_expanded)
        layout.addWidget(self.header)
        self.body = QFrame()
        self.body.setObjectName("DisclosureBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(9, 7, 9, 9)
        self.body_layout.setSpacing(7)
        self.body.setVisible(expanded)
        layout.addWidget(self.body)

    def set_expanded(self, expanded: bool) -> None:
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.blockSignals(False)
        self.body.setVisible(expanded)

    def is_expanded(self) -> bool:
        return not self.body.isHidden()

    def set_collapsible(self, collapsible: bool) -> None:
        """Keep the section visible while retaining its compact visual heading."""
        if collapsible:
            self.header.setCheckable(True)
            self.header.setArrowType(Qt.DownArrow if self.is_expanded() else Qt.RightArrow)
            self.header.setCursor(Qt.PointingHandCursor)
            return
        self.set_expanded(True)
        self.header.setCheckable(False)
        self.header.setArrowType(Qt.NoArrow)
        self.header.setCursor(Qt.ArrowCursor)


class SidebarButton(QToolButton):
    selected = Signal(str)

    def __init__(self, icon_name: str, text: str) -> None:
        super().__init__()
        self.tool_name = text
        self.icon_name = icon_name
        self.setObjectName("SidebarButton")
        self.setText(text)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setFixedSize(54, 58)
        self.setToolTip(text)
        self.setIcon(icon(icon_name, "#AAB8C5", 18))
        self.setIconSize(QSize(18, 18))
        self.setStyleSheet("padding: 5px 2px 4px 2px;")
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(lambda: self.selected.emit(self.tool_name))

    def set_active(self, active: bool) -> None:
        self.setObjectName("SidebarActive" if active else "SidebarButton")
        self.setIcon(icon(self.icon_name, "#F5F7FA" if active else "#AAB8C5", 18))
        self.style().unpolish(self)
        self.style().polish(self)


class Toggle(QWidget):
    """A small switch. Mirrors the QAbstractButton API (isChecked/setChecked) so it
    can be used anywhere a checkbox-like widget is expected, plus keyboard support."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.checked = checked
        self.setFixedSize(28, 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def isChecked(self) -> bool:
        return self.checked

    def setChecked(self, checked: bool, emit: bool = False) -> None:
        """Set state programmatically. `emit=False` by default to avoid feedback loops
        when a caller is just syncing the widget to an already-known value."""
        if checked == self.checked:
            return
        self.checked = checked
        self.update()
        if emit:
            self.toggled.emit(self.checked)

    def _toggle(self) -> None:
        self.checked = not self.checked
        self.toggled.emit(self.checked)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._toggle()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._toggle()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        track = QColor("#00CFE8") if self.checked else QColor("#667788")
        knob = QColor("#F5F7FA")
        if not self.isEnabled():
            track.setAlpha(110)
            knob.setAlpha(160)
        painter.setBrush(track)
        painter.drawRoundedRect(0, 1, 28, 14, 7, 7)
        painter.setBrush(knob)
        painter.drawEllipse(14 if self.checked else 2, 3, 10, 10)
        if self.hasFocus():
            painter.setPen(QColor("#00CFE8"))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(0, 0, 27, 15, 8, 8)


class ToastNotification(QFrame):
    """Compact, stackable notification with an explicit semantic state."""

    dismissed = Signal()

    _ACCENTS = {
        "success": "#38D477",
        "error": "#FF6B6B",
        "warning": "#FFB84D",
        "info": "#4DA8FF",
    }
    _ICONS = {"success": "check", "error": "x", "warning": "alert", "info": "info"}

    def __init__(self, title: str, detail: str, parent: QWidget, kind: str = "success") -> None:
        super().__init__(parent)
        kind = kind if kind in self._ACCENTS else "info"
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        self.message_key = (str(title), str(detail), str(kind))
        self.setFixedWidth(max(260, min(340, parent.width() - 36)))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(7)

        accent_bar = QFrame()
        accent_bar.setObjectName("ToastAccent")
        accent_bar.setProperty("kind", kind)
        accent_bar.setFixedWidth(3)
        layout.addWidget(accent_bar)

        icon_label = QLabel()
        icon_label.setObjectName("ToastIcon")
        icon_label.setFixedSize(24, 24)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setPixmap(icon(self._ICONS[kind], self._ACCENTS[kind], 15).pixmap(15, 15))
        layout.addWidget(icon_label)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 7, 0, 7)
        copy.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("ToastTitle")
        accent = self._ACCENTS.get(kind, self._ACCENTS["success"])
        title_label.setStyleSheet(f"font-weight: 700; color: {accent};")
        copy.addWidget(title_label)
        detail_label = QLabel(detail)
        detail_label.setObjectName("ToastDetail")
        detail_label.setWordWrap(True)
        detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        copy.addWidget(detail_label)
        layout.addLayout(copy, 1)

        close_button = QToolButton()
        close_button.setObjectName("ToastClose")
        close_button.setIcon(icon("x", "#8E989D", 13))
        close_button.setIconSize(QSize(13, 13))
        close_button.setFixedSize(20, 20)
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.setToolTip("Cerrar aviso")
        close_button.clicked.connect(self.dismissed.emit)
        layout.addWidget(close_button, 0, Qt.AlignTop)
