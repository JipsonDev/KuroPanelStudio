"""Workspace composition and responsive panel placement, independent of editing logic."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QScrollArea, QSplitter, QTabWidget


class EditorWorkspace(QSplitter):
    focus_changed = Signal(bool)
    dock_collapsed_changed = Signal(bool)

    def __init__(self, pages, layers, canvas, tools, inspector, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setObjectName("WorkspaceSplitter")
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)
        self.assets = QTabWidget()
        self.assets.setObjectName("AssetsTabs")
        # The sidebar already switches these views; a second tab row only
        # duplicates navigation and steals vertical space from the page list.
        self.assets.tabBar().hide()
        self.assets.setMinimumWidth(240)
        self.assets.setMaximumWidth(330)
        self.assets.addTab(pages, "Páginas")
        self.layers_scroll = QScrollArea()
        self.layers_scroll.setObjectName("LayersScroll")
        self.layers_scroll.setWidgetResizable(True)
        self.layers_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.layers_scroll.setWidget(layers)
        self.assets.addTab(self.layers_scroll, "Capas")
        self.dock = QTabWidget()
        self.dock.setObjectName("WorkspaceDock")
        self.dock.setMinimumWidth(300)
        self.dock.setMaximumWidth(390)
        self.dock.addTab(tools, "Herramientas")
        self.dock.addTab(inspector, "Inspector")
        self.tools = tools
        self.addWidget(self.assets)
        self.addWidget(canvas)
        self.addWidget(self.dock)
        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 1)
        self.setStretchFactor(2, 0)
        self.setSizes([300, 760, 355])
        self.compact = False
        self.focus_mode = False
        self.dock_collapsed = False
        self._compact_open_sizes = [760, 350]
        self._saved_sizes = {}
        self._initial_split_applied = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_split_applied:
            self._initial_split_applied = True
            QTimer.singleShot(0, self._apply_initial_split)

    def _apply_initial_split(self):
        if not self.compact and self.width() > 1180:
            self.setSizes([min(310, int(self.width() * .23)),
                           max(350, self.width() - 680), 355])

    def adapt_to_width(self, width):
        compact = width < 1180
        if compact == self.compact:
            return
        if not self.focus_mode:
            self._saved_sizes[self.compact] = (
                self._compact_open_sizes if self.compact and self.dock_collapsed else self.sizes()
            )
        self.compact = compact
        if compact:
            self.assets.setMaximumWidth(16777215)
            self.dock.addTab(self.assets, "Documento")
        else:
            if self.dock_collapsed:
                self.dock_collapsed = False
                self.dock_collapsed_changed.emit(False)
            self.dock.removeTab(self.dock.indexOf(self.assets))
            self.insertWidget(0, self.assets)
            self.assets.setMaximumWidth(330)
            self.dock.setVisible(not self.focus_mode)
        if not compact:
            self.assets.setVisible(not self.focus_mode)
        if not self.focus_mode:
            self.setSizes(self._saved_sizes.get(compact, [760, 350] if compact else [300, 760, 355]))

    def set_focus_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == self.focus_mode:
            return
        if enabled:
            self._saved_sizes[self.compact] = self.sizes()
        self.focus_mode = enabled
        self.dock.setVisible(not enabled and not self.dock_collapsed)
        if not self.compact:
            self.assets.setVisible(not enabled)
        if not enabled:
            self.setSizes(self._saved_sizes.get(self.compact, [760, 350] if self.compact else [300, 760, 355]))
        self.focus_changed.emit(enabled)

    def set_dock_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if not self.compact or collapsed == self.dock_collapsed:
            return
        if collapsed:
            self._compact_open_sizes = self.sizes()
        self.dock_collapsed = collapsed
        self.dock.setVisible(not collapsed and not self.focus_mode)
        if not collapsed and not self.focus_mode:
            self.setSizes(self._compact_open_sizes)
        self.dock_collapsed_changed.emit(collapsed)

    def show_tools(self):
        self.set_focus_mode(False)
        self.set_dock_collapsed(False)
        self.dock.setCurrentWidget(self.tools)
