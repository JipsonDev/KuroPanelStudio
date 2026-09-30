from __future__ import annotations

import uuid
import math
from collections import OrderedDict
from time import perf_counter

import numpy as np

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextOption, QTransform)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem,
                               QGraphicsSimpleTextItem, QGraphicsTextItem, QGraphicsView,
                               QGraphicsBlurEffect, QGraphicsDropShadowEffect,
                               QStyle, QStyleOptionGraphicsItem,
                               QHBoxLayout, QLabel, QStackedLayout, QVBoxLayout, QWidget)

from core.stroke_engine import StrokeEngine
from core.retouch_layers import normalized_layer_states, patch_layer
from core.balloon_typesetter import (
    balloon_search_rect, effective_balloon_padding, fit_balanced_text,
    text_layout_geometry, usable_mask_bounds,
)
from core.typography_manager import TypographyManager
from core.sfx_layout import automatic_sfx_lines
from core.sfx_transform import (
    MESH_NODES, bezier_baseline, build_warp, mesh_node_position,
    mesh_offset_for_position,
)
from core.text_layout import balloon_layout_signature, fit_rectangular_text, layout_signature
from core.watermark_manager import prepare_watermark, watermark_positions, watermark_vertical_bounds, normalized_watermark
from ui.widgets.controls import ModernButton
from ui.widgets.icons import icon
from ui.welcome_panel import WelcomePanel
from ui.i18n import translate_text


class EffectsTextItem(QGraphicsTextItem):
    """Text item with the same blend modes used by the final exporter."""

    MODES = {
        "normal": QPainter.CompositionMode_SourceOver,
        "multiply": QPainter.CompositionMode_Multiply,
        "screen": QPainter.CompositionMode_Screen,
        "overlay": QPainter.CompositionMode_Overlay,
    }

    def __init__(self, text: str = "", parent: QGraphicsItem | None = None) -> None:
        super().__init__(text, parent)
        self.blend_mode = "normal"

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setCompositionMode(self.MODES.get(self.blend_mode, QPainter.CompositionMode_SourceOver))
        super().paint(painter, option, widget)
        painter.restore()


class InlineTextEditorItem(QGraphicsTextItem):
    """Plain-text editor hosted directly inside a canvas region."""

    commit_requested = Signal()
    cancel_requested = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, text: str = "", parent: QGraphicsItem | None = None) -> None:
        super().__init__(text, parent)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFlag(QGraphicsItem.ItemIsFocusable, True)
        # Text selection belongs to the document cursor. Selecting the whole
        # graphics item adds Qt's dashed focus rectangle around the editor.
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.AllButtons)
        self.setCursor(Qt.IBeamCursor)
        self.setZValue(1)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        # Qt draws a dashed focus rectangle for QGraphicsTextItem even when
        # ItemIsSelectable is off. Hide that chrome while retaining the real
        # document focus, caret and text selection.
        clean_option = QStyleOptionGraphicsItem(option)
        clean_option.state &= ~QStyle.State_HasFocus
        clean_option.state &= ~QStyle.State_Selected
        super().paint(painter, clean_option, widget)

    def keyPressEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Z, Qt.Key_Y):
            if event.key() == Qt.Key_Y or event.modifiers() & Qt.ShiftModifier:
                self.redo_requested.emit()
            else:
                self.undo_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.commit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # Removing a focused graphics item from inside its own focus event is
        # unsafe on some Qt builds. Defer the commit to the next event turn.
        QTimer.singleShot(0, self._emit_deferred_commit)

    def _emit_deferred_commit(self) -> None:
        try:
            self.commit_requested.emit()
        except RuntimeError:
            # The page/editor may have been destroyed before the queued turn.
            return


class WatermarkPixmapItem(QGraphicsPixmapItem):
    """Independent draggable watermark with canvas-local deletion."""

    MODES = EffectsTextItem.MODES

    def __init__(
        self, pixmap: QPixmap, blend_mode: str, bounds: QRectF, keep_inside: bool,
        safe_min_y: float, safe_max_y: float, on_changed, on_deleted,
    ) -> None:
        super().__init__(pixmap)
        self.blend_mode = blend_mode
        self._bounds = bounds
        self._keep_inside = keep_inside
        self._safe_min_y = safe_min_y
        self._safe_max_y = safe_max_y
        self._on_changed = on_changed
        self._on_deleted = on_deleted
        self._drag_start_position = None
        self.setAcceptedMouseButtons(Qt.AllButtons)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setZValue(9)
        self.setToolTip("Arrastra para mover · Ctrl + clic o Supr para borrar")

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self._keep_inside:
            maximum_x = max(0.0, self._bounds.width() - self.boundingRect().width())
            maximum_y = max(0.0, self._bounds.height() - self.boundingRect().height())
            return QPointF(
                max(0.0, min(float(value.x()), maximum_x)),
                max(self._safe_min_y, min(float(value.y()), min(maximum_y, self._safe_max_y))),
            )
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier:
            self._on_deleted(self)
            event.accept()
            return
        scene = self.scene()
        if scene is not None:
            for selected in scene.selectedItems():
                if selected is not self:
                    selected.setSelected(False)
        self.setSelected(True)
        self._drag_start_position = self.pos()
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.OpenHandCursor)
        # A simple click must not freeze the whole automatic page layout as
        # manual coordinates (especially while its loading preview is visible).
        if self._drag_start_position is not None and self.pos() != self._drag_start_position:
            self._on_changed()
        self._drag_start_position = None

    def set_interactive(self, enabled: bool) -> None:
        self.setAcceptedMouseButtons(Qt.AllButtons if enabled else Qt.NoButton)
        self.setFlag(QGraphicsItem.ItemIsSelectable, enabled)
        self.setFlag(QGraphicsItem.ItemIsMovable, enabled)
        if not enabled:
            self.setSelected(False)
            self.unsetCursor()
        else:
            self.setCursor(Qt.OpenHandCursor)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setCompositionMode(self.MODES.get(self.blend_mode, QPainter.CompositionMode_SourceOver))
        super().paint(painter, option, widget)
        if self.isSelected():
            pen = QPen(QColor("#16D5E5"), 1.5, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())
        painter.restore()


class SFXNodeHandle(QGraphicsEllipseItem):
    """Draggable perspective, Bézier or mesh control point."""

    def __init__(self, corner: str, on_moved, node_type: str = "quad") -> None:
        super().__init__(-6, -6, 12, 12)
        self.corner = corner
        self.node_type = node_type
        self._on_moved = on_moved
        self._dragging = False
        self.setBrush(QColor({"quad": "#0CC5D7", "mesh": "#FFB84D", "bezier": "#CF77FF"}.get(node_type, "#0CC5D7")))
        pen = QPen(QColor("#E9FDFF"), 1.5); pen.setCosmetic(True); self.setPen(pen)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(19)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip({
            "quad": "Perspectiva: esquina completamente libre",
            "mesh": "Malla: deforma localmente el SFX",
            "bezier": "Curva Bézier: modifica el recorrido del texto",
        }.get(node_type, "Arrastra para transformar el SFX"))

    def mousePressEvent(self, event) -> None:
        self._dragging = True
        scene = self.scene()
        if scene is not None:
            for selected in scene.selectedItems():
                if selected is not self:
                    selected.setSelected(False)
        self.setSelected(True)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if self._dragging:
            self._on_moved(self, False)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._dragging = False
        self._on_moved(self, True)

    def set_interactive(self, enabled: bool) -> None:
        self.setAcceptedMouseButtons(Qt.AllButtons if enabled else Qt.NoButton)
        self.setFlag(QGraphicsItem.ItemIsMovable, enabled)
        self.setFlag(QGraphicsItem.ItemIsSelectable, enabled)


class TextRegionItem(QGraphicsRectItem):
    """Transparent, draggable region with resize handles and Ctrl-click deletion."""

    HANDLE_SIZE = 14.0
    MINIMUM_SIZE = 12.0
    HANDLE_RADIUS = 5.5

    def __init__(self, region: dict, on_changed, on_deleted, on_preview=None) -> None:
        super().__init__(region["x"], region["y"], region["width"], region["height"])
        self.region = region
        self.locked = bool(region.get("locked", False))
        self._on_changed = on_changed
        self._on_deleted = on_deleted
        self._on_preview = on_preview
        self._resize_edges: tuple[bool, bool, bool, bool] | None = None
        self._gesture_size: tuple[int, int] | None = None
        self._gesture_scaled = False
        self._gesture_moved = False
        self._scale_font_gesture = False
        self._initial_text_scene_origin: QPointF | None = None
        self._initial_text_scale = 1.0
        self._hovered = False
        self.setPen(QPen(QColor("#00CFE8"), 1.8))
        self.setBrush(Qt.NoBrush)
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.ItemIsMovable, not self.locked)
        self.setFlag(QGraphicsRectItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setVisible(bool(region.get("visible", True)))
        # The label and handles must stay visible above OCR text overlays.
        self.setZValue(3)
        self.setToolTip("Arrastra para mover · Esquinas para redimensionar · Ctrl + arrastrar esquina para escalar texto · Ctrl + clic dentro para borrar")

    def _edges_at(self, point) -> tuple[bool, bool, bool, bool]:
        if self.locked:
            return False, False, False, False
        rect = self.rect()
        left = abs(point.x() - rect.left()) <= self.HANDLE_SIZE
        right = abs(point.x() - rect.right()) <= self.HANDLE_SIZE
        top = abs(point.y() - rect.top()) <= self.HANDLE_SIZE
        bottom = abs(point.y() - rect.bottom()) <= self.HANDLE_SIZE
        if left and top:
            return True, False, True, False
        if right and top:
            return False, True, True, False
        if left and bottom:
            return True, False, False, True
        if right and bottom:
            return False, True, False, True
        return False, False, False, False

    def boundingRect(self) -> QRectF:
        margin = self.HANDLE_RADIUS + 4.0
        return super().boundingRect().adjusted(-margin, -margin, margin, margin)

    def _set_cursor(self, edges: tuple[bool, bool, bool, bool]) -> None:
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            self.setCursor(Qt.SizeFDiagCursor)
        elif (right and top) or (left and bottom):
            self.setCursor(Qt.SizeBDiagCursor)
        elif left or right:
            self.setCursor(Qt.SizeHorCursor)
        elif top or bottom:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, event) -> None:
        if self.locked:
            event.accept()
            return
        edges = self._edges_at(event.pos())
        if event.button() == Qt.LeftButton and any(edges):
            # A corner keeps priority over the Ctrl-click delete shortcut.
            scene = self.scene()
            if scene is not None:
                for selected_item in scene.selectedItems():
                    if selected_item is not self:
                        selected_item.setSelected(False)
            self.setSelected(True)
            self._initial_rect = QRectF(self.rect())
            self._initial_scene_rect = self.mapRectToScene(self._initial_rect)
            style = self.region.setdefault("style", {})
            rendered_size = None
            self._initial_text_scene_origin = None
            self._initial_text_scale = 1.0
            if scene is not None:
                views = scene.views()
                if views and hasattr(views[0], "_regions") and hasattr(views[0], "_text_items"):
                    try:
                        idx = views[0]._regions.index(self)
                        if 0 <= idx < len(views[0]._text_items):
                            text_item = views[0]._text_items[idx]
                            rendered_size = text_item.font().pointSize() if hasattr(text_item, "font") else None
                            self._initial_text_scene_origin = text_item.mapToScene(QPointF(0, 0))
                            self._initial_text_scale = float(text_item.scale())
                    except (ValueError, AttributeError):
                        pass
            self._initial_font_size = float(rendered_size or style.get("font_size") or self.region.get("font_size") or 36)
            self._gesture_size = (
                int(round(self.region.get("width", self.rect().width()))),
                int(round(self.region.get("height", self.rect().height()))),
            )
            self._resize_edges = edges
            self._set_cursor(edges)
            self._scale_font_gesture = bool(event.modifiers() & Qt.ControlModifier)
            self._gesture_scaled = False
            self._gesture_moved = False
            event.accept()
            return

        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier:
            self._on_deleted(self)
            event.accept()
            return

        scene = self.scene()
        if scene is not None:
            for selected_item in scene.selectedItems():
                if selected_item is not self:
                    selected_item.setSelected(False)
        self.setSelected(True)
        self._gesture_size = (
            int(round(self.region.get("width", self.rect().width()))),
            int(round(self.region.get("height", self.rect().height()))),
        )
        self._resize_edges = None
        self._gesture_moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.locked:
            event.accept()
            return
        if self._resize_edges and any(self._resize_edges):
            left, right, top, bottom = self._resize_edges
            point = event.pos()
            scale_with_ctrl = bool(
                (event.modifiers() & Qt.ControlModifier) or self._scale_font_gesture or self._gesture_scaled
            )
            if scale_with_ctrl:
                initial = self._initial_rect
                initial_w = max(1.0, initial.width())
                initial_h = max(1.0, initial.height())
                anchor_x = initial.right() if left else initial.left()
                anchor_y = initial.bottom() if top else initial.top()
                scale_w = (anchor_x - point.x()) / initial_w if left else (point.x() - anchor_x) / initial_w
                scale_h = (anchor_y - point.y()) / initial_h if top else (point.y() - anchor_y) / initial_h
                scale = scale_w if abs(scale_w - 1.0) >= abs(scale_h - 1.0) else scale_h
                scale = max(self.MINIMUM_SIZE / min(initial_w, initial_h), scale)
                normalized_rect = QRectF(
                    anchor_x - initial_w * scale if left else anchor_x,
                    anchor_y - initial_h * scale if top else anchor_y,
                    initial_w * scale, initial_h * scale,
                )
                new_size = max(6, min(300, int(round(self._initial_font_size * scale))))
                style = self.region.setdefault("style", {})
                style["font_size"] = new_size
                self.region.pop("font_size", None)
                style["auto_fit"] = False
                self._gesture_scaled = True
            else:
                rect = QRectF(self.rect())
                if left:
                    rect.setLeft(min(point.x(), rect.right() - self.MINIMUM_SIZE))
                if right:
                    rect.setRight(max(point.x(), rect.left() + self.MINIMUM_SIZE))
                if top:
                    rect.setTop(min(point.y(), rect.bottom() - self.MINIMUM_SIZE))
                if bottom:
                    rect.setBottom(max(point.y(), rect.top() + self.MINIMUM_SIZE))
                normalized_rect = rect.normalized()
                self.region.pop("font_size", None)

            self.setRect(normalized_rect)
            self._gesture_moved = self._gesture_moved or normalized_rect != self._initial_rect
            self._commit_geometry(notify=False, change_kind="scale" if scale_with_ctrl else "resize")
            event.accept()
            return
        previous_position = QPointF(self.pos())
        super().mouseMoveEvent(event)
        self._gesture_moved = self._gesture_moved or self.pos() != previous_position
        self._commit_geometry(notify=False)

    def mouseReleaseEvent(self, event) -> None:
        current_size = (
            int(round(self.region.get("width", self.rect().width()))),
            int(round(self.region.get("height", self.rect().height()))),
        )
        resized = bool(self._gesture_size and current_size != self._gesture_size)
        change_kind = "scale" if resized and self._gesture_scaled else "resize" if resized else "move"
        if resized or self._gesture_moved:
            self.region["typeset_box_manual"] = True
        self._scale_font_gesture = False
        self._gesture_scaled = False
        self._gesture_moved = False
        if self._resize_edges and any(self._resize_edges):
            self._resize_edges = None
            self._commit_geometry(change_kind=change_kind, notify=True)
            self._gesture_size = None
            self.setCursor(Qt.SizeAllCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._commit_geometry(change_kind=change_kind, notify=True)
        self._gesture_size = None

    def hoverMoveEvent(self, event) -> None:
        if self.locked:
            self.setCursor(Qt.ArrowCursor)
        else:
            self._set_cursor(self._edges_at(event.pos()))
        super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self.locked:
            event.accept()
            return
        menu = QMenu()
        del_action = menu.addAction("🗑 Eliminar cuadro de texto (Supr / Delete)")
        del_action.triggered.connect(lambda: self._on_deleted(self))
        menu.exec(event.screenPos())
        event.accept()

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(event)

    def _commit_geometry(self, notify: bool = True, change_kind: str = "geometry") -> None:
        previous_x = int(self.region.get("x", 0))
        previous_y = int(self.region.get("y", 0))
        previous_width = int(self.region.get("width", self.rect().width()))
        previous_height = int(self.region.get("height", self.rect().height()))
        scene_rect = self.mapRectToScene(self.rect())
        current_x, current_y = int(scene_rect.x()), int(scene_rect.y())
        current_width, current_height = int(scene_rect.width()), int(scene_rect.height())
        self.region.update({
            "x": current_x, "y": current_y,
            "width": current_width, "height": current_height,
        })
        if self._on_preview is not None:
            live_kind = change_kind if change_kind == "scale" else (
                "resize" if (current_width, current_height) != (previous_width, previous_height) else "move"
            )
            self._on_preview(
                self, live_kind, current_x - previous_x, current_y - previous_y,
            )
        if notify:
            self._on_changed(change_kind)

    def paint(self, painter, option, widget=None) -> None:
        pen = QPen(QColor("#24DCEF") if self.isSelected() else QColor("#00CFE8"), 2 if self.isSelected() else 1.8)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect())
        if self.isSelected() or self._hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#00CFE8"))
            for point in (self.rect().topLeft(), self.rect().topRight(), self.rect().bottomLeft(), self.rect().bottomRight()):
                painter.drawEllipse(point, self.HANDLE_RADIUS, self.HANDLE_RADIUS)
        number = str(self.region.get("number", ""))
        if number:
            badge = QRectF(self.rect().left() + 5, self.rect().top() + 5, 18, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#0797AC"))
            painter.drawEllipse(badge)
            painter.setPen(QColor("#F5F7FA"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.drawText(badge, Qt.AlignCenter, number)


class ComparisonDividerItem(QGraphicsLineItem):
    """Draggable vertical boundary: original on the left, clean result on the right."""

    def __init__(self, moved) -> None:
        super().__init__()
        self._moved = moved
        self._maximum_x = 0.0
        self.setPen(QPen(QColor("#FFB84D"), 2.0))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.SizeHorCursor)
        self.setZValue(18)
        self.setToolTip("Arrastra para comparar limpieza y original")

    def set_bounds(self, width: float, height: float) -> None:
        self._maximum_x = max(0.0, width)
        self.setLine(0, 0, 0, max(0.0, height))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            position = QPointF(max(0.0, min(float(value.x()), self._maximum_x)), 0.0)
            self._moved(position.x())
            return position
        return super().itemChange(change, value)


class CanvasView(QGraphicsView):
    page_available = Signal(bool)
    zoom_changed = Signal(int)
    folder_dropped = Signal(str)
    regions_changed = Signal(object)
    retouch_committed = Signal(object)
    mask_stroke_committed = Signal(object)
    mask_preview_erase_committed = Signal(object)
    color_picked = Signal(QColor)
    brush_size_changed = Signal(int)
    brush_mode_toggle_requested = Signal(str)
    text_layout_status_changed = Signal(object)
    watermark_positions_changed = Signal(object)
    sfx_nodes_changed = Signal(int, object)
    region_edit_requested = Signal(int)
    inline_text_committed = Signal(int, str)
    performance_measured = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        self.scene = QGraphicsScene()
        super().__init__(self.scene, parent)
        self._pixmap_item = QGraphicsPixmapItem()
        # Long webtoon pages can exceed 60,000 px.  The default alpha-mask
        # shape makes Qt inspect that complete raster while zooming even though
        # the page is a plain rectangle.
        self._pixmap_item.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self._pixmap_item.setTransformationMode(Qt.FastTransformation)
        self.scene.addItem(self._pixmap_item)
        self._regions: list[TextRegionItem] = []
        self._clean_items: list[QGraphicsPixmapItem] = []
        self._clean_render_generation = 0
        self._clean_layer_visible = True
        self._clean_layer_opacity = 1.0
        self._retouch_layer_states = normalized_layer_states(None)
        self._pending_clean_patches: list[dict] = []
        self._clean_source_patches: list[dict] = []
        self._staging_clean_items: list[QGraphicsPixmapItem] = []
        self._replace_clean_pending = False
        self._clean_render_started = 0.0
        self._clean_render_timer = QTimer(self)
        self._clean_render_timer.setSingleShot(True)
        self._clean_render_timer.timeout.connect(self._render_clean_batch)
        self._zoom_settle_timer = QTimer(self)
        self._zoom_settle_timer.setSingleShot(True)
        self._zoom_settle_timer.setInterval(140)
        self._zoom_settle_timer.timeout.connect(self._finish_interactive_zoom)
        self._wheel_zoom_timer = QTimer(self)
        self._wheel_zoom_timer.setSingleShot(True)
        self._wheel_zoom_timer.setInterval(16)
        self._wheel_zoom_timer.timeout.connect(self._apply_pending_wheel_zoom)
        self._pending_wheel_zoom = 100
        self._comparison_enabled = False
        self._comparison_ratio = 0.5
        self._clean_clip_item = QGraphicsRectItem()
        self._clean_clip_item.setPen(Qt.NoPen)
        self._clean_clip_item.setBrush(Qt.NoBrush)
        self._clean_clip_item.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
        self._clean_clip_item.setAcceptedMouseButtons(Qt.NoButton)
        self._clean_clip_item.setZValue(1)
        self.scene.addItem(self._clean_clip_item)
        self._comparison_divider = ComparisonDividerItem(self._comparison_divider_moved)
        self._comparison_divider.setVisible(False)
        self.scene.addItem(self._comparison_divider)
        self._text_items: list[QGraphicsTextItem] = []
        self._text_values: list[str] = []
        self._inline_editor: InlineTextEditorItem | None = None
        self._inline_editor_container: QGraphicsRectItem | None = None
        self._inline_editor_index = -1
        self._inline_original_display_text = ""
        self._inline_reflowing = False
        self._inline_overflow_hint: QGraphicsSimpleTextItem | None = None
        self._inline_history: list[tuple[str, int]] = []
        self._inline_history_index = -1
        self._inline_reflow_timer = QTimer(self)
        self._inline_reflow_timer.setSingleShot(True)
        self._inline_reflow_timer.setInterval(90)
        self._inline_reflow_timer.timeout.connect(self._reflow_inline_text)
        self._text_render_generation = 0
        self._text_batch_active = False
        self._text_batch_size = 6
        self._resource_profile = "balanced"
        self._text_viewport_margin = 0.35
        self._retain_offscreen_text = True
        self._deferred_text_indices: set[int] = set()
        self._visible_text_timer = QTimer(self)
        self._visible_text_timer.setSingleShot(True)
        self._visible_text_timer.setInterval(35)
        self._visible_text_timer.timeout.connect(self._render_visible_deferred_text)
        self._last_region_change_kind = "structural"
        self._watermark_items: list[WatermarkPixmapItem] = []
        self._sfx_node_items: list[SFXNodeHandle] = []
        self._sfx_guide_items: list[QGraphicsLineItem] = []
        self._sfx_node_region_index = -1
        self._sfx_preview_timer = QTimer(self)
        self._sfx_preview_timer.setSingleShot(True)
        self._sfx_preview_timer.setInterval(24)
        self._sfx_preview_timer.timeout.connect(self._render_pending_sfx_preview)
        self._pending_sfx_preview_index = -1
        self._balloon_mask_cache: OrderedDict[tuple, tuple[tuple[int, int, int, int], np.ndarray]] = OrderedDict()
        self._balloon_mask_cache_bytes = 0
        self._balloon_mask_cache_limit = 24 * 1024 * 1024
        self._text_layout_cache: OrderedDict[tuple, dict] = OrderedDict()
        self._text_layout_cache_limit = 128
        self._sfx_glyph_cache: OrderedDict[tuple, tuple[list[tuple[str, float, QPainterPath]], str]] = OrderedDict()
        self._sfx_glyph_cache_limit = 64
        self._mask_preview_items: list[QGraphicsPixmapItem] = []
        self._empty_hint: QGraphicsSimpleTextItem | None = None
        self._image_preview_only = False
        self._zoom = 100
        self._manga_mode = False
        self._drawing_item: TextRegionItem | None = None
        self._draw_origin = None
        self._brush_mode: str | None = None
        self._picker_once = False
        self._brush_size = 15
        self._clone_source_point: QPointF | None = None
        self._clone_stroke_start: QPointF | None = None
        self._size_adjust_last_x: float | None = None
        self._size_adjust_remainder = 0.0
        self._brush_color = QColor("#FFFFFF")
        self._stroke_points: list[QPointF | None] = []
        self._stroke_view_points: list[QPointF] = []
        self._stroke_last_view_position: QPointF | None = None
        self._stroke_last_scroll: tuple[int, int] | None = None
        self._stroke_preview: QGraphicsPixmapItem | None = None
        self._brush_cursor = QGraphicsEllipseItem()
        cursor_pen = QPen(QColor("#FFFFFF"), 1.2, Qt.SolidLine)
        cursor_pen.setCosmetic(True)
        self._brush_cursor.setPen(cursor_pen)
        self._brush_cursor.setBrush(QColor(0, 207, 232, 36))
        self._brush_cursor.setZValue(20)
        self._brush_cursor.setAcceptedMouseButtons(Qt.NoButton)
        self._brush_cursor.setVisible(False)
        self.scene.addItem(self._brush_cursor)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#15171A"))
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setFrameShape(QFrame.NoFrame)
        self.setAcceptDrops(True)
        self.verticalScrollBar().valueChanged.connect(self._schedule_visible_text_render)
        self.horizontalScrollBar().valueChanged.connect(self._schedule_visible_text_render)

    def set_resource_policy(self, policy) -> None:
        cache_mb = max(4, int(getattr(policy, "balloon_cache_mb", 24)))
        self._balloon_mask_cache_limit = cache_mb * 1024 * 1024
        self._text_layout_cache_limit = 48 if str(getattr(policy, "name", "balanced")) == "low" else 128
        self._sfx_glyph_cache_limit = 24 if str(getattr(policy, "name", "balanced")) == "low" else 64
        profile = str(getattr(policy, "name", "balanced"))
        self._resource_profile = profile
        self._text_viewport_margin = max(0.0, float(getattr(policy, "text_viewport_margin", 0.35)))
        self._retain_offscreen_text = bool(getattr(policy, "retain_offscreen_text", True))
        self._text_batch_size = 2 if profile == "low" else (12 if profile == "high" else 6)
        self._trim_typesetting_caches()
        self._schedule_visible_text_render()

    def _trim_typesetting_caches(self) -> None:
        while self._balloon_mask_cache and self._balloon_mask_cache_bytes > self._balloon_mask_cache_limit:
            _, removed = self._balloon_mask_cache.popitem(last=False)
            self._balloon_mask_cache_bytes -= int(removed[1].nbytes)
        while len(self._text_layout_cache) > self._text_layout_cache_limit:
            self._text_layout_cache.popitem(last=False)
        while len(self._sfx_glyph_cache) > self._sfx_glyph_cache_limit:
            self._sfx_glyph_cache.popitem(last=False)

    def _clear_typesetting_caches(self) -> None:
        self._balloon_mask_cache.clear(); self._balloon_mask_cache_bytes = 0
        self._text_layout_cache.clear()
        self._sfx_glyph_cache.clear()

    def trim_memory(self) -> None:
        """Release recomputable masks/layouts without touching visible artwork."""
        self._clear_typesetting_caches()
        self._evict_offscreen_text(force=True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.folder_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def set_pixmap(
        self, pixmap: QPixmap | None, source_size: tuple[int, int] | None = None,
        preview: bool = False,
    ) -> None:
        # Layout/mask caches are keyed by pixmap identity and bounded by the
        # resource policy. Keeping them makes returning to a page cheap and
        # avoids rebuilding every text layer after each navigation.
        self.page_available.emit(True)
        self._trim_typesetting_caches()
        self.clear_watermark()
        self.clear_sfx_nodes()
        if self._empty_hint:
            self.scene.removeItem(self._empty_hint)
            self._empty_hint = None
        if pixmap:
            transform = QTransform()
            if preview and source_size and pixmap.width() > 0 and pixmap.height() > 0:
                source_width, source_height = max(1, int(source_size[0])), max(1, int(source_size[1]))
                preview_scale = source_width / pixmap.width()
                transform.scale(preview_scale, preview_scale)
            self._pixmap_item.setTransform(transform)
            self._pixmap_item.setPixmap(pixmap)
            self._pixmap_item.setPos(0, 0)
            if preview and source_size:
                self.scene.setSceneRect(0, 0, max(1, int(source_size[0])), max(1, int(source_size[1])))
            else:
                self.scene.setSceneRect(self._pixmap_item.mapRectToScene(self._pixmap_item.boundingRect()))
            self._image_preview_only = bool(preview)
        else:
            self._image_preview_only = False
            # A single lightweight preview is used until a real page is loaded.
            # Production images remain lazy-loaded through ImageManager.
            placeholder = QPixmap(800, 1200)
            placeholder.fill(QColor("#202328"))
            painter = QPainter(placeholder)
            painter.setPen(QColor("#3A4047"))
            for position in range(0, placeholder.height(), 160):
                painter.drawLine(0, position, placeholder.width(), position)
            painter.setPen(QColor("#AAB8C5")); painter.drawText(245, 100, "Vista previa de página larga")
            painter.end()
            self._pixmap_item.setPixmap(placeholder)
            # Preserve the geometry of a vertical webtoon without allocating a
            # 60k-pixel placeholder in memory.
            self.scene.setSceneRect(0, 0, 800, 59702)
        self._update_comparison_geometry()
        self.fit_image()
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().minimum())

    def set_image(self, image: QImage | None, source_size: tuple[int, int] | None = None) -> None:
        pixmap = (
            QPixmap.fromImage(image, Qt.NoFormatConversion)
            if image is not None and not image.isNull() else None
        )
        self.set_pixmap(pixmap, source_size, preview=False)

    def set_loading_preview(self, image: QImage, source_size: tuple[int, int]) -> None:
        """Replace the previous page quickly while original pixels decode."""
        self._text_render_generation += 1
        self._text_batch_active = False
        self.set_regions([])
        for item in self._text_items:
            self.scene.removeItem(item)
        self._text_items.clear()
        self._text_values.clear()
        self._deferred_text_indices.clear()
        self.clear_mask_preview()
        self.clear_cleaning()
        pixmap = QPixmap.fromImage(image, Qt.NoFormatConversion)
        self.set_pixmap(pixmap, source_size, preview=True)


    def clear_page(self) -> None:
        self.page_available.emit(False)
        if self._empty_hint is not None:
            self.scene.removeItem(self._empty_hint)
            self._empty_hint = None
        self._text_render_generation += 1
        self._text_batch_active = False
        self._clear_typesetting_caches()
        self._pixmap_item.setPixmap(QPixmap())
        self.set_regions([])
        for item in self._text_items:
            self.scene.removeItem(item)
        self._text_items.clear()
        self._text_values.clear()
        self._deferred_text_indices.clear()
        self.clear_mask_preview()
        self.clear_cleaning()
        self.clear_watermark()
        self.clear_sfx_nodes()
        self.scene.setSceneRect(0, 0, 900, 900)
        self._empty_hint = QGraphicsSimpleTextItem("Abre un capítulo para comenzar")
        self._empty_hint.setBrush(QColor("#667788"))
        self._empty_hint.setFont(QFont("Segoe UI", 12))
        self._empty_hint.setPos(320, 420)
        self.scene.addItem(self._empty_hint)
        self.centerOn(self._empty_hint)

    def clear_watermark(self) -> None:
        for item in self._watermark_items:
            self.scene.removeItem(item)
        self._watermark_items.clear()

    def clear_sfx_nodes(self) -> None:
        self._sfx_preview_timer.stop()
        self._pending_sfx_preview_index = -1
        for item in [*self._sfx_node_items, *self._sfx_guide_items]:
            self.scene.removeItem(item)
        self._sfx_node_items.clear()
        self._sfx_guide_items.clear()
        self._sfx_node_region_index = -1

    def show_sfx_nodes(self, index: int) -> None:
        self.clear_sfx_nodes()
        if not 0 <= index < len(self._regions):
            return
        region = self._regions[index].region
        from core.typography_manager import TypographyManager
        style = TypographyManager.normalized(region.get("style", {}))
        region["style"] = style
        if not style.get("sfx_enabled", False) or not region.get("visible", True):
            return
        self._sfx_node_region_index = index
        x, y = float(region["x"]), float(region["y"])
        width, height = max(1.0, float(region["width"])), max(1.0, float(region["height"]))
        for corner in ("tl", "tr", "bl", "br"):
            handle = SFXNodeHandle(corner, self._sfx_node_moved)
            handle.setPos(
                x + width * float(style.get(f"sfx_quad_{corner}_x", 0)) / 100.0,
                y + height * float(style.get(f"sfx_quad_{corner}_y", 0)) / 100.0,
            )
            handle.set_interactive(self._brush_mode is None and not self._comparison_enabled)
            self.scene.addItem(handle); self._sfx_node_items.append(handle)
        if style.get("sfx_mesh_enabled", False):
            for row, column in MESH_NODES:
                handle = SFXNodeHandle(f"r{row}c{column}", self._sfx_node_moved, "mesh")
                local_x, local_y = mesh_node_position(style, width, height, row, column)
                handle.setPos(x + local_x, y + local_y)
                handle.set_interactive(self._brush_mode is None and not self._comparison_enabled)
                self.scene.addItem(handle); self._sfx_node_items.append(handle)
        if style.get("sfx_bezier_enabled", False):
            for control, default_x in (("c1", 33), ("c2", 67)):
                handle = SFXNodeHandle(control, self._sfx_node_moved, "bezier")
                handle.setPos(
                    x + width * float(style.get(f"sfx_bezier_{control}_x", default_x)) / 100.0,
                    y + height * (0.5 + float(style.get(f"sfx_bezier_{control}_y", 0)) / 100.0),
                )
                handle.set_interactive(self._brush_mode is None and not self._comparison_enabled)
                self.scene.addItem(handle); self._sfx_node_items.append(handle)
        guide_count = 4
        if style.get("sfx_mesh_enabled", False):
            guide_count += 12
        if style.get("sfx_bezier_enabled", False):
            guide_count += 3
        for _ in range(guide_count):
            guide = QGraphicsLineItem(); pen = QPen(QColor(12, 197, 215, 180), 1.2, Qt.DashLine)
            pen.setCosmetic(True); guide.setPen(pen); guide.setZValue(18); guide.setAcceptedMouseButtons(Qt.NoButton)
            self.scene.addItem(guide); self._sfx_guide_items.append(guide)
        self._update_sfx_guides()

    def _update_sfx_guides(self) -> None:
        if len(self._sfx_node_items) < 4 or len(self._sfx_guide_items) < 4:
            return
        guide_index = 0
        for guide, (start, end) in zip(self._sfx_guide_items[:4], ((0, 1), (1, 3), (3, 2), (2, 0))):
            guide.setLine(self._sfx_node_items[start].pos().x(), self._sfx_node_items[start].pos().y(),
                          self._sfx_node_items[end].pos().x(), self._sfx_node_items[end].pos().y())
            guide_index += 1
        handles = {(item.node_type, item.corner): item for item in self._sfx_node_items}
        if any(item.node_type == "mesh" for item in self._sfx_node_items):
            grid = {
                (0, 0): self._sfx_node_items[0], (0, 2): self._sfx_node_items[1],
                (2, 0): self._sfx_node_items[2], (2, 2): self._sfx_node_items[3],
            }
            for row, column in MESH_NODES:
                grid[(row, column)] = handles[("mesh", f"r{row}c{column}")]
            for row in range(3):
                for column in range(2):
                    first, second = grid[(row, column)], grid[(row, column + 1)]
                    self._sfx_guide_items[guide_index].setLine(first.pos().x(), first.pos().y(), second.pos().x(), second.pos().y())
                    guide_index += 1
            for column in range(3):
                for row in range(2):
                    first, second = grid[(row, column)], grid[(row + 1, column)]
                    self._sfx_guide_items[guide_index].setLine(first.pos().x(), first.pos().y(), second.pos().x(), second.pos().y())
                    guide_index += 1
        if any(item.node_type == "bezier" for item in self._sfx_node_items):
            index = self._sfx_node_region_index
            if 0 <= index < len(self._regions):
                region = self._regions[index].region
                start = QPointF(float(region["x"]), float(region["y"]) + float(region["height"]) / 2.0)
                end = QPointF(float(region["x"]) + float(region["width"]), start.y())
                points = [start, handles[("bezier", "c1")].pos(), handles[("bezier", "c2")].pos(), end]
                for first, second in zip(points, points[1:]):
                    self._sfx_guide_items[guide_index].setLine(first.x(), first.y(), second.x(), second.y())
                    guide_index += 1

    def _sfx_node_moved(self, _handle: SFXNodeHandle, final: bool = True) -> None:
        index = self._sfx_node_region_index
        if not 0 <= index < len(self._regions):
            return
        region = self._regions[index].region
        x, y = float(region["x"]), float(region["y"])
        width, height = max(1.0, float(region["width"])), max(1.0, float(region["height"]))
        values: dict[str, int] = {}
        for handle in self._sfx_node_items:
            if handle.node_type != "quad":
                continue
            values[f"sfx_quad_{handle.corner}_x"] = round((handle.pos().x() - x) / width * 100)
            values[f"sfx_quad_{handle.corner}_y"] = round((handle.pos().y() - y) / height * 100)
        values["sfx_quad_version"] = 1
        prospective = {**dict(region.get("style", {})), **values}
        for handle in self._sfx_node_items:
            if handle.node_type == "mesh":
                row, column = int(handle.corner[1]), int(handle.corner[3])
                dx, dy = mesh_offset_for_position(
                    prospective, width, height, row, column,
                    (handle.pos().x() - x, handle.pos().y() - y),
                )
                values[f"sfx_mesh_r{row}c{column}_dx"] = dx
                values[f"sfx_mesh_r{row}c{column}_dy"] = dy
            elif handle.node_type == "bezier":
                values[f"sfx_bezier_{handle.corner}_x"] = round((handle.pos().x() - x) / width * 100)
                values[f"sfx_bezier_{handle.corner}_y"] = round(((handle.pos().y() - y) / height - 0.5) * 100)
        region.setdefault("style", {}).update(values)
        self._update_sfx_guides()
        if final and 0 <= index < len(self._text_values):
            self._sfx_preview_timer.stop()
            self.update_text_layers(
                [index], list(self._text_values), [entry.region for entry in self._regions],
            )
        elif not final:
            self._pending_sfx_preview_index = index
            if not self._sfx_preview_timer.isActive():
                self._sfx_preview_timer.start()
        if final:
            self.sfx_nodes_changed.emit(index, values)

    def _render_pending_sfx_preview(self) -> None:
        index = self._pending_sfx_preview_index
        self._pending_sfx_preview_index = -1
        if 0 <= index < len(self._text_values):
            self.update_text_layers(
                [index], list(self._text_values), [entry.region for entry in self._regions],
            )

    def set_watermark(self, settings: dict | None, visible: bool = True, page_name: str = "") -> None:
        """Render a non-destructive watermark at original page coordinates."""
        self.clear_watermark()
        if not visible or not settings or self._pixmap_item.pixmap().isNull():
            return
        # During loading the pixmap contains only a reduced top crop. All
        # overlays use original scene coordinates, never thumbnail dimensions.
        bounds = self.scene.sceneRect()
        page_size = (round(bounds.width()), round(bounds.height()))
        mark = prepare_watermark(page_size, settings)
        if mark is None:
            return
        rgba = mark.convert("RGBA")
        raw = rgba.tobytes("raw", "RGBA")
        image = QImage(raw, rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888).copy()
        pixmap = QPixmap.fromImage(image)
        config = dict(settings)
        page_positions = settings.get("page_positions", {})
        if not settings.get("chapter_resolved"):
            config["positions"] = page_positions[page_name] if page_name in page_positions else settings.get("positions")
        positions = watermark_positions(page_size, rgba.size, config)
        safe_min_y = 0.0
        safe_max_y = float(max(0, page_size[1] - rgba.height))
        if settings.get("keep_inside", True):
            safe_min_y, safe_max_y = watermark_vertical_bounds(page_size, rgba.size, normalized_watermark(settings))
        # A repeated preview remains interactive even on very long webtoons.
        for x, y in positions[:600]:
            item = WatermarkPixmapItem(
                pixmap, str(settings.get("blend_mode", "normal")), self.scene.sceneRect(),
                bool(settings.get("keep_inside", True)),
                safe_min_y, safe_max_y,
                self._emit_watermark_positions, self._delete_watermark_item,
            )
            item.setPos(x, y)
            self.scene.addItem(item)
            item.set_interactive(self._brush_mode is None and not self._comparison_enabled)
            self._watermark_items.append(item)

    def _emit_watermark_positions(self) -> None:
        self.watermark_positions_changed.emit([
            [int(round(item.pos().x())), int(round(item.pos().y()))]
            for item in self._watermark_items
        ])

    def _delete_watermark_item(self, item: WatermarkPixmapItem) -> None:
        if item not in self._watermark_items:
            return
        self._watermark_items.remove(item)
        self.scene.removeItem(item)
        self._emit_watermark_positions()

    @property
    def brush_mode(self) -> str | None:
        return self._brush_mode

    @property
    def brush_size(self) -> int:
        return self._brush_size

    def original_image(self) -> QImage:
        return self._pixmap_item.pixmap().toImage().copy()

    def set_regions(self, regions: list[dict]) -> None:
        used_ids: set[str] = set()
        for index, region in enumerate(regions):
            region_id = str(region.get("id", "")).strip()
            if not region_id:
                region_id = f"region-{index}"
            if region_id in used_ids:
                region_id = f"region-{uuid.uuid4().hex}"
            region["id"] = region_id
            used_ids.add(region_id)
            region.setdefault("confidence", 1.0)
        self._renumber_regions(regions)
        incoming_ids = [str(region.get("id", "")) for region in regions]
        existing_ids = [str(item.region.get("id", "")) for item in self._regions]
        if self._inline_editor is not None and incoming_ids != existing_ids:
            # A deleted/reordered box can no longer own the current editor.
            # Commit before rebuilding the graphics tree while its index is
            # still valid.
            self.finish_inline_text_edit(commit=True)
        if incoming_ids == existing_ids and len(regions) == len(self._regions):
            # OCR/translation/style refreshes commonly keep the same boxes.
            # Reuse their QGraphicsItems instead of destroying and recreating
            # the complete page layer tree.
            self.scene.blockSignals(True)
            try:
                for item, region in zip(self._regions, regions):
                    selected = item.isSelected()
                    item.region = region
                    item.locked = bool(region.get("locked", False))
                    item.setPos(0, 0)
                    item.setRect(
                        float(region.get("x", 0)), float(region.get("y", 0)),
                        max(1.0, float(region.get("width", 1))),
                        max(1.0, float(region.get("height", 1))),
                    )
                    item.setVisible(bool(region.get("visible", True)))
                    self._set_region_interaction(
                        item, self._brush_mode is None and not self._comparison_enabled,
                    )
                    item.setSelected(selected and item.isVisible())
                    item.update()
            finally:
                self.scene.blockSignals(False)
            return
        # Rebuilding the scene briefly destroys the old selection. Suppress
        # that transient signal: consumers still hold the old layer list at
        # this point and could otherwise select an index that no longer exists.
        self.clear_sfx_nodes()
        self.scene.blockSignals(True)
        try:
            for item in self._regions:
                self.scene.removeItem(item)
            self._regions.clear()
            for index, region in enumerate(regions):
                item = TextRegionItem(region, self._emit_regions, self._delete_region_item, self._preview_region_geometry)
                self._set_region_interaction(item, self._brush_mode is None and not self._comparison_enabled)
                self.scene.addItem(item)
                self._regions.append(item)
        finally:
            self.scene.blockSignals(False)

    @staticmethod
    def _set_region_interaction(item: TextRegionItem, enabled: bool) -> None:
        """Comparison owns the pointer; boxes underneath must remain inert."""
        item.setAcceptedMouseButtons(Qt.AllButtons if enabled else Qt.NoButton)
        item.setFlag(QGraphicsItem.ItemIsSelectable, enabled)
        item.setFlag(QGraphicsItem.ItemIsMovable, enabled and not item.locked)

    def set_region_state(
        self,
        index: int,
        *,
        visible: bool | None = None,
        locked: bool | None = None,
        opacity: int | None = None,
    ) -> None:
        if not 0 <= index < len(self._regions):
            return
        region_item = self._regions[index]
        if visible is not None:
            region_item.region["visible"] = bool(visible)
            region_item.setVisible(bool(visible))
            if index < len(self._text_items):
                self._text_items[index].setVisible(bool(visible))
                if bool(visible) and index < len(self._text_values) and self._text_values[index].strip():
                    item = self._text_items[index]
                    empty_placeholder = isinstance(item, QGraphicsTextItem) and not item.toPlainText()
                    if empty_placeholder:
                        self._deferred_text_indices.add(index)
                        self._schedule_visible_text_render()
        if locked is not None:
            region_item.region["locked"] = bool(locked)
            region_item.locked = bool(locked)
            region_item.setFlag(
                QGraphicsRectItem.ItemIsMovable,
                not bool(locked) and not self._comparison_enabled and self._brush_mode is None,
            )
            region_item.update()
        if opacity is not None:
            value = max(0, min(100, int(opacity)))
            region_item.region["opacity"] = value
            if index < len(self._text_items):
                self._text_items[index].setOpacity(value / 100.0)

    def select_region(self, index: int) -> None:
        self.select_regions([index], index)

    def select_regions(
        self, indices: list[int], current: int | None = None, *, reveal: bool = False,
    ) -> None:
        """Select layers without moving the user's canvas viewport.

        ``reveal`` is reserved for explicit navigation commands. Normal layer
        synchronization and box creation must never jump to another balloon.
        """
        selected = set(indices)
        self.scene.blockSignals(True)
        try:
            for position, item in enumerate(self._regions):
                item.setSelected(position in selected and item.isVisible())
        finally:
            self.scene.blockSignals(False)
        target = current if current is not None else (indices[-1] if indices else -1)
        if reveal and 0 <= target < len(self._regions):
            self.ensureVisible(self._regions[target], 24, 24)

    def clear_cleaning(self) -> None:
        self._clean_render_generation += 1
        self._clean_render_timer.stop()
        self._pending_clean_patches.clear()
        self._clean_source_patches.clear()
        for item in [*self._clean_items, *self._staging_clean_items]:
            self.scene.removeItem(item)
        self._clean_items.clear()
        self._staging_clean_items.clear()
        self._replace_clean_pending = False
        self._clean_render_started = 0.0

    def clear_mask_preview(self) -> None:
        for item in self._mask_preview_items:
            self.scene.removeItem(item)
        self._mask_preview_items.clear()

    def show_mask_preview(self, entries: list[dict]) -> None:
        """Render editable masks as a translucent overlay above the page."""
        self.clear_mask_preview()
        for entry in entries:
            mask = np.ascontiguousarray(entry.get("mask"), dtype=np.uint8)
            if mask.ndim != 2 or not np.any(mask):
                continue
            height, width = mask.shape
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            rgba[:, :, :3] = (255, 64, 104)
            rgba[:, :, 3] = np.clip(mask.astype(np.float32) * 0.58, 0, 150).astype(np.uint8)
            image = QImage(rgba.data, width, height, rgba.strides[0], QImage.Format_RGBA8888).copy()
            item = QGraphicsPixmapItem(QPixmap.fromImage(image))
            item.setPos(int(entry.get("x", 0)), int(entry.get("y", 0)))
            item.setZValue(7)
            item.setAcceptedMouseButtons(Qt.NoButton)
            self.scene.addItem(item)
            self._mask_preview_items.append(item)

    def apply_cleaning(self, patches: list[dict], replace: bool = False) -> None:
        """Show patches incrementally so large chapters never block the UI."""
        if replace:
            self._clean_render_timer.stop()
            self._clean_render_started = 0.0
            self._pending_clean_patches.clear()
            for item in self._staging_clean_items:
                self.scene.removeItem(item)
            self._staging_clean_items.clear()
            self._replace_clean_pending = True
            self._clean_source_patches = list(patches)
        else:
            self._clean_source_patches.extend(patches)
        visible_patches = [
            patch for patch in patches
            if bool(self._retouch_layer_states.get(patch_layer(patch), {}).get("visible", True))
        ]
        self._pending_clean_patches.extend(visible_patches)
        if self._pending_clean_patches and not self._clean_render_timer.isActive():
            self._clean_render_started = perf_counter()
            self._clean_render_timer.start(0)
        elif replace:
            self._finish_clean_replacement()

    def _effective_patch_mask(self, patch: dict, pixels: np.ndarray) -> np.ndarray | None:
        mask = patch.get("mask")
        if mask is not None:
            return np.ascontiguousarray(mask, dtype=np.uint8)
        # Legacy/manual patches could lose their stroke alpha. Recover it by
        # comparing against the immutable original image so their rectangular
        # crop can never cover unrelated artwork.
        base = self._pixmap_item.pixmap().toImage()
        x, y = int(patch.get("x", 0)), int(patch.get("y", 0))
        height, width = pixels.shape[:2]
        if base.isNull() or x < 0 or y < 0 or x + width > base.width() or y + height > base.height():
            return None
        original = self._qimage_rgb_array(base.copy(QRect(x, y, width, height)))
        if original.shape != pixels.shape:
            return None
        return np.where(np.any(original != pixels, axis=2), 255, 0).astype(np.uint8)

    def _render_clean_batch(self) -> None:
        batch = self._pending_clean_patches[:2]
        del self._pending_clean_patches[:len(batch)]
        for patch in batch:
            pixels = np.ascontiguousarray(patch["pixels"], dtype=np.uint8)
            height, width = pixels.shape[:2]
            mask = self._effective_patch_mask(patch, pixels)
            if mask is not None:
                if not np.any(mask):
                    continue
                rgba = np.dstack((pixels, mask)).astype(np.uint8)
                image = QImage(rgba.data, width, height, rgba.strides[0], QImage.Format_RGBA8888).copy()
            else:
                image = QImage(pixels.data, width, height, pixels.strides[0], QImage.Format_RGB888).copy()
            item = QGraphicsPixmapItem(QPixmap.fromImage(image))
            logical_layer = patch_layer(patch)
            layer_state = self._retouch_layer_states.get(logical_layer, {})
            item.setData(20, logical_layer)
            item.setParentItem(self._clean_clip_item)
            item.setPos(patch["x"], patch["y"])
            item.setZValue(0)
            item.setAcceptedMouseButtons(Qt.NoButton)
            item.setOpacity(max(0.0, min(1.0, int(layer_state.get("opacity", 100)) / 100.0)))
            item.setVisible(
                not self._replace_clean_pending and bool(layer_state.get("visible", True))
            )
            (self._staging_clean_items if self._replace_clean_pending else self._clean_items).append(item)
        if self._pending_clean_patches:
            self._clean_render_timer.start(0)
        elif self._replace_clean_pending:
            self._finish_clean_replacement()
        elif self._clean_render_started:
            self.performance_measured.emit(
                "Carga de capas visibles", max(0.0, perf_counter() - self._clean_render_started),
            )
            self._clean_render_started = 0.0

    def _finish_clean_replacement(self) -> None:
        for item in self._clean_items:
            self.scene.removeItem(item)
        self._clean_items = self._staging_clean_items
        self._staging_clean_items = []
        for item in self._clean_items:
            state = self._retouch_layer_states.get(str(item.data(20) or "automatic"), {})
            item.setVisible(bool(state.get("visible", True)))
            item.setOpacity(max(0.0, min(1.0, int(state.get("opacity", 100)) / 100.0)))
        self._replace_clean_pending = False
        if self._clean_render_started:
            self.performance_measured.emit(
                "Carga de capas visibles", max(0.0, perf_counter() - self._clean_render_started),
            )
            self._clean_render_started = 0.0

    def set_image_layer_state(
        self,
        layer: str,
        *,
        visible: bool | None = None,
        opacity: int | None = None,
    ) -> None:
        value = None if opacity is None else max(0.0, min(1.0, int(opacity) / 100.0))
        if layer == "original":
            if visible is not None:
                self._pixmap_item.setVisible(bool(visible))
            if value is not None:
                self._pixmap_item.setOpacity(value)
            return
        if layer == "clean":
            if visible is not None:
                self._clean_layer_visible = bool(visible)
                self._comparison_divider.setVisible(self._comparison_enabled and self._clean_layer_visible)
            if value is not None:
                self._clean_layer_opacity = value
            if visible is not None:
                self._clean_clip_item.setVisible(bool(visible))
            if value is not None:
                self._clean_clip_item.setOpacity(value)

    def set_retouch_layer_state(
        self, layer: str, *, visible: bool | None = None, opacity: int | None = None,
    ) -> None:
        if layer not in self._retouch_layer_states:
            return
        state = self._retouch_layer_states[layer]
        if visible is not None:
            state["visible"] = bool(visible)
            if not bool(visible):
                self._pending_clean_patches = [
                    patch for patch in self._pending_clean_patches if patch_layer(patch) != layer
                ]
                for item in [*self._clean_items, *self._staging_clean_items]:
                    if str(item.data(20) or "automatic") == layer:
                        if item in self._clean_items:
                            self._clean_items.remove(item)
                        if item in self._staging_clean_items:
                            self._staging_clean_items.remove(item)
                        self.scene.removeItem(item)
            elif not any(str(item.data(20) or "automatic") == layer for item in self._clean_items):
                pending = [patch for patch in self._clean_source_patches if patch_layer(patch) == layer]
                if pending:
                    self._pending_clean_patches.extend(pending)
                    if not self._clean_render_timer.isActive():
                        self._clean_render_started = perf_counter()
                        self._clean_render_timer.start(0)
        if opacity is not None:
            state["opacity"] = max(0, min(100, int(opacity)))
        for item in [*self._clean_items, *self._staging_clean_items]:
            if str(item.data(20) or "automatic") != layer:
                continue
            item.setVisible(bool(state.get("visible", True)))
            item.setOpacity(max(0.0, min(1.0, int(state.get("opacity", 100)) / 100.0)))

    def set_comparison_mode(self, enabled: bool) -> None:
        self._comparison_enabled = bool(enabled)
        if self._comparison_enabled:
            self.scene.clearSelection()
        for item in self._regions:
            self._set_region_interaction(item, self._brush_mode is None and not self._comparison_enabled)
        for item in self._watermark_items:
            item.set_interactive(self._brush_mode is None and not self._comparison_enabled)
        for item in self._sfx_node_items:
            item.set_interactive(self._brush_mode is None and not self._comparison_enabled)
        self._comparison_divider.setVisible(self._comparison_enabled and self._clean_layer_visible)
        self._update_comparison_geometry()

    def _comparison_divider_moved(self, x: float) -> None:
        bounds = self._image_scene_rect()
        if bounds.width() > 0:
            self._comparison_ratio = max(0.0, min(1.0, x / bounds.width()))
        if self._comparison_enabled:
            self._clean_clip_item.setRect(x, 0, max(0.0, bounds.width() - x), bounds.height())

    def _update_comparison_geometry(self) -> None:
        bounds = self._image_scene_rect()
        width, height = bounds.width(), bounds.height()
        self._comparison_divider.set_bounds(width, height)
        x = width * self._comparison_ratio
        self._comparison_divider.setPos(x, 0)
        if self._comparison_enabled:
            self._clean_clip_item.setRect(x, 0, max(0.0, width - x), height)
        else:
            self._clean_clip_item.setRect(0, 0, width, height)

    def set_retouch_mode(self, enabled: bool, brush_size: int | None = None) -> None:
        self.set_brush_mode("paint" if enabled else None, brush_size)

    def set_mask_brush_mode(self, enabled: bool, brush_size: int | None = None) -> None:
        self.set_brush_mode("mask" if enabled else None, brush_size)

    def set_brush_mode(self, mode: str | None, brush_size: int | None = None) -> None:
        if mode not in {None, "paint", "clone", "heal", "restore", "mask", "mask_erase"}:
            raise ValueError(f"Modo de pincel desconocido: {mode}")
        if mode != self._brush_mode:
            # Cambiar de herramienta (incluso entre dos modos no-None) debe
            # descartar cualquier trazo a medio dibujar. Dejarlo vivo es lo que
            # generaba la "tira" larga: el próximo _begin_retouch() heredaba
            # puntos y un QGraphicsPathItem de la herramienta anterior.
            if self._stroke_preview is not None:
                self.scene.removeItem(self._stroke_preview)
            self._stroke_preview = None
            self._stroke_points = []
            self._stroke_view_points = []
            self._stroke_last_view_position = None
            self._stroke_last_scroll = None
        self._brush_mode = mode
        interaction_enabled = mode is None and not self._comparison_enabled
        for item in self._regions:
            self._set_region_interaction(item, interaction_enabled)
        for item in self._watermark_items:
            item.set_interactive(interaction_enabled)
        for item in self._sfx_node_items:
            item.set_interactive(interaction_enabled)
        if brush_size is not None:
            self._brush_size = StrokeEngine.clamp_size(brush_size)
        self._picker_once = False
        self._size_adjust_last_x = None
        self._size_adjust_remainder = 0.0
        if mode is None:
            self._brush_cursor.setVisible(False)
        self.setMouseTracking(mode is not None)
        self.viewport().setMouseTracking(mode is not None)
        self.viewport().setCursor(Qt.BlankCursor if mode is not None else Qt.ArrowCursor)
        self._brush_cursor.setBrush(
            QColor(255, 184, 77, 58) if mode == "mask_erase"
            else QColor(255, 89, 116, 52) if mode == "mask"
            else QColor(67, 211, 146, 52) if mode == "restore"
            else QColor(138, 180, 248, 48) if mode == "clone"
            else QColor(186, 104, 200, 48) if mode == "heal"
            else QColor(0, 207, 232, 36)
        )
        self._update_brush_cursor_geometry()

    def set_brush_size(self, size: int) -> None:
        self._brush_size = StrokeEngine.clamp_size(size)
        self._update_brush_cursor_geometry()

    def adjust_brush_size(self, amount: int) -> None:
        size = StrokeEngine.clamp_size(self._brush_size + int(amount))
        if size != self._brush_size:
            self._brush_size = size
            self._update_brush_cursor_geometry()
            self.brush_size_changed.emit(size)

    def request_color_pick(self) -> None:
        self._brush_mode = "paint"
        self._picker_once = True
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._brush_cursor.setVisible(False)
        self.viewport().setCursor(Qt.CrossCursor)

    def set_brush_color(self, color: QColor) -> None:
        if color.isValid():
            self._brush_color = QColor(color)

    def _image_scene_rect(self) -> QRectF:
        return self._pixmap_item.mapRectToScene(self._pixmap_item.boundingRect())

    def _update_brush_cursor_geometry(self, scene_position: QPointF | None = None) -> None:
        diameter = float(self._brush_size)
        self._brush_cursor.setRect(-diameter / 2.0, -diameter / 2.0, diameter, diameter)
        if scene_position is not None:
            self._brush_cursor.setPos(scene_position)
            self._brush_cursor.setVisible(
                self._brush_mode is not None
                and not self._picker_once
                and not self._image_preview_only
                and self._image_scene_rect().contains(scene_position)
            )

    @staticmethod
    def _pixel_from_item(item: QGraphicsPixmapItem, scene_position: QPointF) -> QColor | None:
        local = item.mapFromScene(scene_position)
        x, y = int(local.x()), int(local.y())
        image = item.pixmap().toImage()
        if 0 <= x < image.width() and 0 <= y < image.height():
            return image.pixelColor(x, y)
        return None

    def _visible_color_at(self, scene_position: QPointF) -> QColor | None:
        for item in reversed(self._clean_items):
            color = self._pixel_from_item(item, scene_position)
            if color is not None:
                return color
        return self._pixel_from_item(self._pixmap_item, scene_position)

    def _composed_image(self, rectangle: QRect) -> QImage:
        base = self._pixmap_item.pixmap().toImage().convertToFormat(QImage.Format_RGB888)
        result = base.copy(rectangle)
        painter = QPainter(result)
        for item in self._clean_items:
            item_rect = QRect(int(item.x()), int(item.y()), item.pixmap().width(), item.pixmap().height())
            intersection = rectangle.intersected(item_rect)
            if intersection.isEmpty():
                continue
            source = QRect(
                intersection.x() - item_rect.x(), intersection.y() - item_rect.y(),
                intersection.width(), intersection.height(),
            )
            target = QRect(
                intersection.x() - rectangle.x(), intersection.y() - rectangle.y(),
                intersection.width(), intersection.height(),
            )
            painter.drawPixmap(target, item.pixmap(), source)
        painter.end()
        return result

    @staticmethod
    def _qimage_rgb_array(image: QImage) -> np.ndarray:
        rgb = image.convertToFormat(QImage.Format_RGB888)
        raw = np.frombuffer(rgb.bits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
        # Always own the returned memory. When bytesPerLine has no padding,
        # ascontiguousarray may return a view backed by the temporary QImage;
        # after this function returns OpenCV would then read released Qt data.
        return raw[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3).copy()

    def _begin_retouch(self, scene_position: QPointF, view_position: QPointF | None = None) -> None:
        # Raster data must stay in image-local coordinates. Scene coordinates
        # only happen to match while the pixmap is at (0, 0); mixing both can
        # turn a normal stroke into a connector to the page edge after a
        # viewport or layout change.
        local_position = self._pixmap_item.mapFromScene(scene_position)
        bounds = self._pixmap_item.boundingRect()
        local_position.setX(min(max(local_position.x(), bounds.left()), bounds.right()))
        local_position.setY(min(max(local_position.y(), bounds.top()), bounds.bottom()))
        self._stroke_points = [local_position]
        self._stroke_view_points = [QPointF(view_position)] if view_position is not None else []
        self._stroke_last_view_position = QPointF(view_position) if view_position is not None else None
        self._stroke_last_scroll = (
            self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        )
        if self._brush_mode == "clone":
            self._clone_stroke_start = QPointF(local_position)
            if self._clone_source_point is None:
                self._clone_source_point = QPointF(max(0, local_position.x() - 40), local_position.y())
        preview = QGraphicsPixmapItem()
        preview.setParentItem(self._pixmap_item)
        preview.setZValue(10)
        preview.setAcceptedMouseButtons(Qt.NoButton)
        self._stroke_preview = preview
        self._update_raster_stroke_preview()

    def _stroke_preview_color(self) -> QColor:
        return (
            QColor(255, 184, 77, 190) if self._brush_mode == "mask_erase"
            else QColor(255, 77, 109, 150) if self._brush_mode == "mask"
            else QColor(67, 211, 146, 190) if self._brush_mode == "restore"
            else QColor(138, 180, 248, 175) if self._brush_mode == "clone"
            else QColor(186, 104, 200, 175) if self._brush_mode == "heal"
            else QColor(self._brush_color)
        )

    def _update_raster_stroke_preview(self) -> None:
        """Display the exact compact dab mask; never build a vector connector."""
        if self._stroke_preview is None or not self._stroke_points:
            return
        pixmap = self._pixmap_item.pixmap()
        stroke = StrokeEngine.rasterize(
            [(point.x(), point.y()) if point is not None else None for point in self._stroke_points],
            self._brush_size, pixmap.width(), pixmap.height(), padding=2,
            trusted_continuous=True,
        )
        if stroke is None:
            self._stroke_preview.setPixmap(QPixmap())
            return
        color = self._stroke_preview_color()
        rgba = np.empty((stroke.height, stroke.width, 4), dtype=np.uint8)
        rgba[:, :, 0] = color.red()
        rgba[:, :, 1] = color.green()
        rgba[:, :, 2] = color.blue()
        rgba[:, :, 3] = (
            stroke.mask.astype(np.uint16) * int(color.alpha()) // 255
        ).astype(np.uint8)
        image = QImage(
            rgba.data, stroke.width, stroke.height, int(rgba.strides[0]), QImage.Format_RGBA8888,
        ).copy()
        self._stroke_preview.setPixmap(QPixmap.fromImage(image, Qt.NoFormatConversion))
        self._stroke_preview.setPos(stroke.x, stroke.y)

    def _finish_retouch(self) -> None:
        if not self._stroke_points:
            return
        try:
            pixmap = self._pixmap_item.pixmap()
            local_points = [point for point in self._stroke_points if point is not None]
            coordinate_jump = False
            if local_points and self._stroke_view_points:
                local_x = [point.x() for point in local_points]
                local_y = [point.y() for point in local_points]
                view_x = [point.x() for point in self._stroke_view_points]
                view_y = [point.y() for point in self._stroke_view_points]
                local_extent = max(max(local_x) - min(local_x), max(local_y) - min(local_y), 1.0)
                view_extent = max(max(view_x) - min(view_x), max(view_y) - min(view_y), 1.0)
                scale = max(abs(float(self.transform().m11())), 0.01)
                expected_extent = view_extent / scale + float(self._brush_size * 2 + 12)
                coordinate_jump = local_extent > expected_extent * 1.65 + 24.0
            stroke = None if coordinate_jump else StrokeEngine.rasterize(
                [(point.x(), point.y()) if point is not None else None for point in self._stroke_points],
                self._brush_size, pixmap.width(), pixmap.height(),
                padding=8 if self._brush_mode in {"mask", "mask_erase"} else 2,
                trusted_continuous=True,
            )
            if stroke is not None and self._stroke_view_points:
                xs = [point.x() for point in self._stroke_view_points]
                ys = [point.y() for point in self._stroke_view_points]
                view_extent = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
                scale = max(abs(float(self.transform().m11())), 0.01)
                expected_extent = view_extent / scale + float(self._brush_size * 2 + 12)
                if max(stroke.width, stroke.height) > expected_extent * 1.65 + 24.0:
                    # The raster moved much farther than the physical cursor.
                    # Discard it instead of persisting a page-edge connector.
                    stroke = None
            if stroke is not None:
                if self._brush_mode == "mask":
                    # A mask stroke is a non-destructive draft. Show the exact
                    # mask first so false positives can be erased.
                    self.mask_stroke_committed.emit({
                        "x": stroke.x, "y": stroke.y,
                        "mask": np.ascontiguousarray(stroke.mask),
                    })
                elif self._brush_mode == "mask_erase":
                    self.mask_preview_erase_committed.emit({
                        "x": stroke.x, "y": stroke.y,
                        "mask": np.ascontiguousarray(stroke.mask),
                    })
                elif self._brush_mode == "restore":
                    # A restoration patch is simply the immutable original
                    # pixels plus the exact antialiased brush alpha. Appending
                    # it after cleanup/manual patches restores only the area
                    # touched by the user and remains fully undoable.
                    original = self._pixmap_item.pixmap().toImage().copy(
                        QRect(stroke.x, stroke.y, stroke.width, stroke.height)
                    )
                    pixels = self._qimage_rgb_array(original)
                    self.retouch_committed.emit({
                        "x": stroke.x, "y": stroke.y, "pixels": pixels,
                        "mask": np.ascontiguousarray(stroke.mask),
                        "kind": "restore",
                    })
                elif self._brush_mode == "clone":
                    # Calculate offset from clone source to stroke start
                    src_pt = self._clone_source_point or QPointF(max(0, stroke.x - 40), stroke.y)
                    start_pt = self._clone_stroke_start or QPointF(stroke.x, stroke.y)
                    offset_x = int(src_pt.x() - start_pt.x())
                    offset_y = int(src_pt.y() - start_pt.y())
                    composed = self._composed_image(QRect(0, 0, pixmap.width(), pixmap.height()))
                    src_x = max(0, min(pixmap.width() - stroke.width, stroke.x + offset_x))
                    src_y = max(0, min(pixmap.height() - stroke.height, stroke.y + offset_y))
                    source_crop = composed.copy(QRect(src_x, src_y, stroke.width, stroke.height))
                    pixels = self._qimage_rgb_array(source_crop)
                    self.retouch_committed.emit({
                        "x": stroke.x, "y": stroke.y, "pixels": pixels,
                        "mask": np.ascontiguousarray(stroke.mask),
                        "kind": "clone",
                    })
                elif self._brush_mode == "heal":
                    pad = 12
                    x0 = max(0, stroke.x - pad)
                    y0 = max(0, stroke.y - pad)
                    x1 = min(pixmap.width(), stroke.x + stroke.width + pad)
                    y1 = min(pixmap.height(), stroke.y + stroke.height + pad)
                    composed = self._composed_image(QRect(x0, y0, x1 - x0, y1 - y0))
                    crop_arr = self._qimage_rgb_array(composed)
                    inpaint_mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
                    mask_y0 = stroke.y - y0
                    mask_x0 = stroke.x - x0
                    inpaint_mask[mask_y0:mask_y0 + stroke.height, mask_x0:mask_x0 + stroke.width] = (stroke.mask > 20).astype(np.uint8) * 255
                    import cv2
                    healed = cv2.inpaint(crop_arr, inpaint_mask, 3, cv2.INPAINT_TELEA)
                    healed_patch = healed[mask_y0:mask_y0 + stroke.height, mask_x0:mask_x0 + stroke.width]
                    self.retouch_committed.emit({
                        "x": stroke.x, "y": stroke.y, "pixels": healed_patch,
                        "mask": np.ascontiguousarray(stroke.mask),
                        "kind": "heal",
                    })
                else:
                    # Store the chosen colour plus the antialiased stroke alpha.
                    # Pre-blending the colour here and applying the same alpha
                    # again in the scene caused pale/doubled brush edges.
                    pixels = np.empty((stroke.height, stroke.width, 3), dtype=np.uint8)
                    pixels[:, :] = (
                        self._brush_color.red(), self._brush_color.green(), self._brush_color.blue(),
                    )
                    self.retouch_committed.emit({
                        "x": stroke.x, "y": stroke.y, "pixels": pixels,
                        "mask": np.ascontiguousarray(stroke.mask),
                    })
        finally:
            # A slot failure must never leave a live path attached to later
            # hover events; that produced the persistent white strip.
            if self._stroke_preview is not None:
                self.scene.removeItem(self._stroke_preview)
            self._stroke_preview = None
            self._stroke_points = []
            self._stroke_view_points = []
            self._stroke_last_view_position = None
            self._stroke_last_scroll = None

    def delete_selected_regions(self) -> None:
        indices = [
            index for index, item in enumerate(self._regions)
            if item.isSelected() and not item.locked
        ]
        if not indices:
            return
        self.scene.blockSignals(True)
        try:
            for index in reversed(indices):
                item = self._regions.pop(index)
                self.scene.removeItem(item)
        finally:
            self.scene.blockSignals(False)
        self._emit_regions()

    def delete_last_region(self) -> None:
        for item in reversed(self._regions):
            if not item.locked:
                self._delete_region_item(item)
                break

    def delete_all_regions(self) -> None:
        if not self._regions:
            return
        retained = []
        for item in self._regions:
            if item.locked:
                retained.append(item)
            else:
                self.scene.removeItem(item)
        self._regions = retained
        self._emit_regions()

    def _new_region(self, rectangle: QRectF) -> TextRegionItem:
        region = {
            "id": f"manual-{uuid.uuid4().hex}",
            "x": int(rectangle.x()), "y": int(rectangle.y()),
            "width": int(rectangle.width()), "height": int(rectangle.height()),
            "confidence": 1.0, "label": "manual", "number": len(self._regions) + 1,
        }
        item = TextRegionItem(region, self._emit_regions, self._delete_region_item, self._preview_region_geometry)
        self.scene.addItem(item)
        self._regions.append(item)
        return item

    def _delete_region_item(self, item: TextRegionItem) -> None:
        if item in self._regions and not item.locked:
            self.scene.blockSignals(True)
            try:
                self._regions.remove(item)
                self.scene.removeItem(item)
            finally:
                self.scene.blockSignals(False)
            self._emit_regions()

    def _emit_regions(self, change_kind: str = "structural") -> None:
        self._last_region_change_kind = str(change_kind or "structural")
        regions = [item.region for item in self._regions]
        self._renumber_regions(regions)
        for item in self._regions:
            item.update()
        self.regions_changed.emit([dict(item.region) for item in self._regions])

    def set_manga_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._manga_mode:
            return
        self._manga_mode = enabled
        self._renumber_regions([item.region for item in self._regions])
        for item in self._regions:
            item.update()

    def _renumber_regions(self, regions: list[dict]) -> None:
        if not regions:
            return
        from core.typography_manager import TypographyManager
        ordered = TypographyManager.ordered(regions, self._manga_mode)
        numbers = {str(region.get("id", "")): number for number, region in enumerate(ordered, start=1)}
        for fallback, region in enumerate(regions, start=1):
            region["number"] = numbers.get(str(region.get("id", "")), fallback)

    def _region_at(self, scene_position) -> TextRegionItem | None:
        for item in reversed(self._regions):
            if item.isVisible() and item.contains(item.mapFromScene(scene_position)):
                return item
        return None

    def _preview_region_geometry(
        self,
        region_item: TextRegionItem,
        change_kind: str = "geometry",
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> None:
        """Keep the text attached to its box without recomposing it while moving."""
        try:
            index = self._regions.index(region_item)
        except ValueError:
            return
        region = region_item.region
        if not 0 <= index < len(self._text_items):
            return
        old_item = self._text_items[index]
        if change_kind == "move":
            if delta_x or delta_y:
                old_item.moveBy(float(delta_x), float(delta_y))
            return
        if change_kind == "scale" and region_item._initial_text_scene_origin is not None:
            initial_rect = region_item._initial_scene_rect
            factor = float(region["width"]) / max(1.0, initial_rect.width())
            desired_origin = QPointF(
                float(region["x"]) + (region_item._initial_text_scene_origin.x() - initial_rect.left()) * factor,
                float(region["y"]) + (region_item._initial_text_scene_origin.y() - initial_rect.top()) * factor,
            )
            old_item.setScale(region_item._initial_text_scale * factor)
            current_origin = old_item.mapToScene(QPointF(0, 0))
            old_item.moveBy(desired_origin.x() - current_origin.x(), desired_origin.y() - current_origin.y())
            old_item.setData(0, (int(region["width"]), int(region["height"])))
            old_item.setData(6, True)
            return
        if change_kind == "resize":
            style = region.setdefault("style", {})
            adaptive_balloon = bool(style.get("balloon_fit", False) and style.get("auto_fit", False))
            if not adaptive_balloon and isinstance(old_item, QGraphicsTextItem) and not region.get("font_size"):
                visible_size = int(old_item.font().pointSize())
                if visible_size > 0:
                    style["font_size"] = visible_size
            if not adaptive_balloon:
                style["auto_fit"] = False
        current_size = (int(region["width"]), int(region["height"]))
        previous_size = old_item.data(0)
        if (change_kind in {"resize", "geometry"} or previous_size != current_size) and isinstance(old_item, QGraphicsTextItem):
            if not bool(region.get("style", {}).get("balloon_fit", False)):
                self._preview_text_reflow(index, old_item, region)
            old_item.setData(0, current_size)
            old_item.setData(6, True)
        self._position_text_item(old_item, region)

    def _preview_text_reflow(
        self, index: int, item: QGraphicsTextItem, region: dict,
    ) -> None:
        if item.data(5) == "sfx":
            return
        style = TypographyManager.normalized(region.get("style", {}))
        text = str(
            self._text_values[index]
            if 0 <= index < len(self._text_values)
            else region.get("applied_text", region.get("text", ""))
        )
        if style.get("text_case") == "upper":
            text = text.upper()
        elif style.get("text_case") == "lower":
            text = text.lower()
        if style.get("vertical_text"):
            text = "\n".join(text.replace("\n", ""))

        margin = max(0, int(style.get("text_margin", style.get("margin", 8))))
        available_width = max(1.0, float(region["width"]) - margin * 2)
        available_height = max(1.0, float(region["height"]) - margin * 2)
        # A manual box manipulation must not change the visible font size.
        # Resizing can rewrap the lines, but the size currently on the canvas
        # becomes authoritative until the user explicitly enables auto-fit.
        font = QFont(item.font())
        font_size = int(
            region.get("font_size")
            or (region.get("style", {}).get("font_size") if not region.get("style", {}).get("auto_fit", False) else 0)
            or font.pointSize()
        )
        font.setPointSize(max(6, font_size))
        style["font_size"] = max(6, font_size)
        style["auto_fit"] = False
        region["style"] = style
        item.setFont(font)
        item.document().setDefaultFont(font)
        item.setPlainText(text)
        item.setTextWidth(available_width)
        item.setData(2, None)

        scale_y = max(0.5, min(2.0, int(style.get("scale_y", 100)) / 100.0))
        region["text_overflow"] = bool(
            item.boundingRect().height() * scale_y > available_height + 1
        )
        item.setTransform(QTransform.fromScale(1.0, scale_y))
        item.setData(4, scale_y)
        item.setTransformOriginPoint(item.boundingRect().center())
        item.setRotation(float(style.get("rotation", 0)))

        # Outline/glow clones contain a snapshot of the exact document. Hide
        # them only during the drag so stale line breaks never trail the fill;
        # the final rebuild recreates every effect at full quality.
        for child in getattr(item, "_effect_children", []):
            child.setVisible(False)

    def add_text(self, lines: list[str], regions: list[dict]) -> None:
        self._text_render_generation += 1
        self._text_batch_active = False
        previous = list(self._text_items)
        updated: list[QGraphicsTextItem] = []
        self._text_values = []
        for index, region in enumerate(regions):
            text = str(lines[index] if index < len(lines) else region.get("applied_text", ""))
            self._text_values.append(text)
            if index < len(self._regions):
                self._regions[index].region.update(region)
            signature = self._text_item_signature(text, region)
            reusable = previous[index] if index < len(previous) else None
            if (
                reusable is not None and reusable.data(3) == signature
                and reusable.data(0) == (int(region["width"]), int(region["height"]))
                and not bool(reusable.data(6))
            ):
                self._position_text_item(reusable, region)
                reusable.setVisible(bool(region.get("visible", True)))
                reusable.setOpacity(max(0.0, min(1.0, int(region.get("opacity", 100)) / 100.0)))
                updated.append(reusable)
            else:
                if reusable is not None:
                    self.scene.removeItem(reusable)
                updated.append(self._make_text_item(text, region))
        for item in previous[len(regions):]:
            self.scene.removeItem(item)
        self._text_items = updated
        self._deferred_text_indices.clear()
        self.text_layout_status_changed.emit([
            bool(region.get("text_overflow", False)) for region in regions
        ])

    def _text_render_rect(self, margin_factor: float | None = None) -> QRectF:
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        factor = self._text_viewport_margin if margin_factor is None else max(0.0, margin_factor)
        return visible.adjusted(
            -visible.width() * factor, -visible.height() * factor,
            visible.width() * factor, visible.height() * factor,
        )

    @staticmethod
    def _region_rect(region: dict) -> QRectF:
        return QRectF(
            float(region.get("x", 0)), float(region.get("y", 0)),
            max(1.0, float(region.get("width", 1))),
            max(1.0, float(region.get("height", 1))),
        )

    def _schedule_visible_text_render(self, _value=None) -> None:
        if self._deferred_text_indices:
            self._visible_text_timer.start()

    def _evict_offscreen_text(self, force: bool = False) -> None:
        """Release expensive glyph/effect trees outside the working viewport."""
        if self._retain_offscreen_text and not force:
            return
        keep_rect = self._text_render_rect(0.6 if force else max(0.25, self._text_viewport_margin))
        for index, item in enumerate(list(self._text_items)):
            if not 0 <= index < len(self._regions) or index >= len(self._text_values):
                continue
            if index == self._inline_editor_index or not self._text_values[index].strip():
                continue
            region = self._regions[index].region
            if self._region_rect(region).intersects(keep_rect):
                continue
            if isinstance(item, QGraphicsTextItem) and not item.toPlainText():
                continue
            self.scene.removeItem(item)
            self._text_items[index] = self._make_text_item("", region)
            if bool(region.get("visible", True)):
                self._deferred_text_indices.add(index)

    def _render_visible_deferred_text(self) -> None:
        if self._text_batch_active or not self._deferred_text_indices:
            if self._text_batch_active:
                self._visible_text_timer.start(20)
            return
        self._evict_offscreen_text()
        render_rect = self._text_render_rect()
        candidates = [
            index for index in sorted(self._deferred_text_indices)
            if 0 <= index < len(self._regions)
            and bool(self._regions[index].region.get("visible", True))
            and self._region_rect(self._regions[index].region).intersects(render_rect)
        ]
        if not candidates:
            return
        generation = self._text_render_generation
        self._text_batch_active = True

        def render_next() -> None:
            if generation != self._text_render_generation:
                self._text_batch_active = False
                return
            batch = candidates[:self._text_batch_size]
            del candidates[:len(batch)]
            for index in batch:
                if not 0 <= index < len(self._text_items) or index >= len(self._regions):
                    continue
                self.scene.removeItem(self._text_items[index])
                self._text_items[index] = self._make_text_item(
                    self._text_values[index], self._regions[index].region,
                )
                self._deferred_text_indices.discard(index)
            self.viewport().update()
            if candidates:
                QTimer.singleShot(1, render_next)
            else:
                self._text_batch_active = False
                self.text_layout_status_changed.emit([
                    bool(entry.region.get("text_overflow", False)) for entry in self._regions
                ])

        render_next()

    def add_text_batched(self, lines: list[str], regions: list[dict]) -> None:
        """Render a heavy page progressively while keeping Qt responsive."""
        self._text_render_generation += 1
        generation = self._text_render_generation
        previous = list(self._text_items)
        updated: list[QGraphicsItem] = []
        pending: list[int] = []
        deferred: set[int] = set()
        self._text_values = []
        render_rect = self._text_render_rect()
        for index, region in enumerate(regions):
            text = str(lines[index] if index < len(lines) else region.get("applied_text", ""))
            self._text_values.append(text)
            if index < len(self._regions):
                self._regions[index].region.update(region)
            signature = self._text_item_signature(text, region)
            reusable = previous[index] if index < len(previous) else None
            if (
                reusable is not None and reusable.data(3) == signature
                and reusable.data(0) == (int(region["width"]), int(region["height"]))
                and not bool(reusable.data(6))
            ):
                self._position_text_item(reusable, region)
                reusable.setVisible(bool(region.get("visible", True)))
                reusable.setOpacity(max(0.0, min(1.0, int(region.get("opacity", 100)) / 100.0)))
                updated.append(reusable)
            else:
                if reusable is not None:
                    self.scene.removeItem(reusable)
                updated.append(self._make_text_item("", region))
                if text.strip() and bool(region.get("visible", True)) and self._region_rect(region).intersects(render_rect):
                    pending.append(index)
                elif text.strip() and bool(region.get("visible", True)):
                    deferred.add(index)
        for item in previous[len(regions):]:
            self.scene.removeItem(item)
        self._text_items = updated
        self._deferred_text_indices = deferred
        self._text_batch_active = bool(pending)
        # Prioritize the current viewport on long webtoons. Otherwise a box
        # near the end of the page can remain an empty placeholder until every
        # off-screen box above it has been composed.
        visible_scene = render_rect
        centre = visible_scene.center()
        pending.sort(key=lambda index: (
            0 if QRectF(
                float(regions[index].get("x", 0)), float(regions[index].get("y", 0)),
                float(regions[index].get("width", 1)), float(regions[index].get("height", 1)),
            ).intersects(visible_scene) else 1,
            abs(float(regions[index].get("y", 0)) - centre.y()),
        ))
        first_batch = True
        heavy_layout = any(
            bool(dict(regions[index].get("style", {})).get("sfx_enabled", False))
            or bool(dict(regions[index].get("style", {})).get("glow_enabled", False))
            or bool(dict(regions[index].get("style", {})).get("shadow_enabled", False))
            for index in pending
        )

        def render_next() -> None:
            nonlocal first_batch
            try:
                self.viewport()
            except RuntimeError:
                # A queued batch may outlive a canvas closed by a test, page
                # replacement or application shutdown.
                return
            if generation != self._text_render_generation:
                return
            # Compose one visible layer immediately, then use short batches.
            # Balloon segmentation and effects for six boxes in one event used
            # to freeze the window on long pages.
            batch_limit = 1 if first_batch else (2 if heavy_layout else self._text_batch_size)
            batch = pending[:batch_limit]
            del pending[:len(batch)]
            for index in batch:
                if not 0 <= index < len(self._text_items) or index >= len(regions):
                    continue
                self.scene.removeItem(self._text_items[index])
                self._text_items[index] = self._make_text_item(self._text_values[index], regions[index])
            if pending:
                self.viewport().update()
                first_batch = False
                # Yield to mouse/scroll/paint events between expensive text
                # layers instead of monopolizing the GUI thread.
                QTimer.singleShot(1, render_next)
                return
            self._text_batch_active = False
            self.text_layout_status_changed.emit([
                bool(region.get("text_overflow", False)) for region in regions
            ])
            first_batch = False
            self.viewport().update()
            self._schedule_visible_text_render()

        if pending:
            render_next()
        else:
            self.text_layout_status_changed.emit([
                bool(region.get("text_overflow", False)) for region in regions
            ])

    def update_text_layers(self, indices: list[int], lines: list[str], regions: list[dict]) -> None:
        """Rebuild only changed layers instead of walking the complete chapter."""
        if len(self._text_items) != len(regions) or len(self._text_values) != len(regions):
            self.add_text(lines, regions)
            return
        if self._text_batch_active:
            # Invalidate a queued progressive batch, but do not fall back to a
            # full-page rebuild just because the user edited one visible box.
            self._text_render_generation += 1
            self._text_batch_active = False
        for index in sorted(set(indices)):
            if not 0 <= index < len(regions):
                continue
            region = regions[index]
            text = str(lines[index] if index < len(lines) else region.get("applied_text", ""))
            self._text_values[index] = text
            if index < len(self._regions):
                self._regions[index].region.update(region)
            item = self._text_items[index]
            signature = self._text_item_signature(text, region)
            if (
                item.data(3) == signature
                and item.data(0) == (int(region["width"]), int(region["height"]))
                and not bool(item.data(6))
            ):
                self._position_text_item(item, region)
                item.setVisible(bool(region.get("visible", True)))
                item.setOpacity(max(0.0, min(1.0, int(region.get("opacity", 100)) / 100.0)))
                continue
            self.scene.removeItem(item)
            self._text_items[index] = self._make_text_item(text, region)
            self._deferred_text_indices.discard(index)
        self.text_layout_status_changed.emit([bool(region.get("text_overflow", False)) for region in regions])

    def preview_text_value(self, index: int, text: str, region: dict) -> None:
        """Update the existing item while typing, without replacement or auto-resizing."""
        if not 0 <= index < len(self._text_items):
            return
        # ``MainWindow`` and the canvas deliberately keep separate region
        # dictionaries.  Live typing used to update only the visible glyphs;
        # the next box move emitted the canvas' stale dictionary and replaced
        # the new text with the old/empty value.  Keep the canvas copy current
        # before any move or resize can publish it again.
        if index < len(self._regions):
            self._regions[index].region.update(region)
        item = self._text_items[index]
        if not isinstance(item, QGraphicsTextItem) or bool(region.get("style", {}).get("sfx_enabled", False)):
            values = list(self._text_values)
            if index < len(values):
                values[index] = str(text)
            self.update_text_layers([index], values, [entry.region for entry in self._regions])
            return
        style = dict(region.get("style", {}))
        display = str(text)
        if style.get("text_case") == "upper":
            display = display.upper()
        elif style.get("text_case") == "lower":
            display = display.lower()
        if style.get("vertical_text"):
            display = "\n".join(display.replace("\n", ""))
        item.setPlainText(display)
        margin = int(style.get("text_margin", style.get("margin", 8)))
        item.setTextWidth(max(1, int(region["width"]) - margin * 2))
        self._position_text_item(item, region)
        for child in getattr(item, "_effect_children", []):
            child.setVisible(False)
        if index < len(self._text_values):
            self._text_values[index] = str(text)
        item.setData(0, (int(region["width"]), int(region["height"])))
        self.viewport().update()

    def begin_inline_text_edit(self, index: int, scene_position: QPointF | None = None) -> bool:
        """Edit a layer's final text without leaving the canvas."""
        if not 0 <= index < len(self._regions) or not 0 <= index < len(self._text_items):
            return False
        if self._inline_editor is not None:
            if self._inline_editor_index == index:
                self._inline_editor.setFocus(Qt.MouseFocusReason)
                return True
            self.finish_inline_text_edit(commit=True)
        region = self._regions[index].region
        if not bool(region.get("visible", True)) or bool(region.get("locked", False)):
            return False

        style = TypographyManager.normalized(region.get("style", {}))
        text = str(
            self._text_values[index]
            if index < len(self._text_values)
            else region.get("applied_text") or region.get("translation") or region.get("text", "")
        )
        width = max(12.0, float(region.get("width", 1)))
        height = max(12.0, float(region.get("height", 1)))
        margin = max(2, min(int(style.get("text_margin", 8)), int(min(width, height) // 4)))

        container = QGraphicsRectItem(0, 0, width, height)
        # The container only clips the editor and handles hit testing. Editing
        # happens directly over the page, without a tinted panel or a second
        # outline competing with the text caret.
        container.setPen(Qt.NoPen)
        container.setBrush(Qt.NoBrush)
        container.setPos(float(region.get("x", 0)), float(region.get("y", 0)))
        container.setAcceptedMouseButtons(Qt.NoButton)
        container.setZValue(24)

        source_item = self._text_items[index]
        if isinstance(source_item, QGraphicsTextItem) and text.strip() and not source_item.toPlainText():
            # A progressive page load may still hold an empty placeholder.
            # Materialize the final layout before opening its visual twin.
            self.scene.removeItem(source_item)
            source_item = self._make_text_item(text, region)
            self._text_items[index] = source_item
            self._deferred_text_indices.discard(index)
        editor = InlineTextEditorItem("", container)
        if isinstance(source_item, QGraphicsTextItem):
            # Copy the composed document itself: its hard/soft line layout,
            # per-line height and balloon margins must match the visible text.
            editor.setDocument(source_item.document().clone(editor))
            editor.setFont(source_item.font())
            editor.setDefaultTextColor(source_item.defaultTextColor())
            editor.setTextWidth(source_item.textWidth())
            editor.setTransform(source_item.transform())
            editor.setTransformOriginPoint(source_item.transformOriginPoint())
            editor.setRotation(source_item.rotation())
            editor.setOpacity(source_item.opacity())
            editor.setPos(container.mapFromScene(source_item.scenePos()))
        else:
            font = QFont(str(style.get("font_family", "Segoe UI")), int(style.get("font_size", 36)))
            font.setWeight(QFont.Weight(int(style.get("font_weight", 400))))
            font.setItalic(bool(style.get("italic", False)))
            font.setUnderline(bool(style.get("underline", False)))
            font.setStrikeOut(bool(style.get("strikeout", False)))
            editor.setPlainText(text)
            editor.setFont(font)
            editor.setDefaultTextColor(QColor(str(style.get("text_color", "#111111"))))
            editor.document().setDocumentMargin(0)
            option = editor.document().defaultTextOption()
            option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            option.setAlignment({
                "left": Qt.AlignLeft, "right": Qt.AlignRight,
            }.get(str(style.get("alignment", "center")), Qt.AlignCenter))
            editor.document().setDefaultTextOption(option)
            editor.setTextWidth(max(1.0, width - margin * 2))
            editor.setPos(float(margin), float(margin))
        editor.setToolTip("Editando en el lienzo · Ctrl+Enter guarda · Esc cancela")
        editor.commit_requested.connect(lambda: self.finish_inline_text_edit(commit=True))
        editor.cancel_requested.connect(lambda: self.finish_inline_text_edit(commit=False))
        editor.undo_requested.connect(lambda: self._restore_inline_history(-1))
        editor.redo_requested.connect(lambda: self._restore_inline_history(1))
        editor.document().contentsChanged.connect(self._schedule_inline_reflow)

        translator = getattr(QApplication.instance(), "_kuro_ui_translator", None)
        language = getattr(translator, "language", "es")
        overflow_hint = QGraphicsSimpleTextItem(
            translate_text("⚠ El texto no cabe en el globo", language), container,
        )
        overflow_hint.setBrush(QBrush(QColor("#FFBD54")))
        overflow_hint.setPos(0, height + 4)
        overflow_hint.setZValue(2)
        overflow_hint.setVisible(False)

        self._inline_editor = editor
        self._inline_editor_container = container
        self._inline_editor_index = index
        self._inline_original_display_text = editor.toPlainText()
        self._inline_overflow_hint = overflow_hint
        source_item.setVisible(False)
        self.scene.addItem(container)
        editor.setFocus(Qt.MouseFocusReason)
        cursor = editor.textCursor()
        if scene_position is None:
            cursor.movePosition(QTextCursor.End)
        else:
            local = editor.mapFromScene(scene_position)
            hit = editor.document().documentLayout().hitTest(local, Qt.FuzzyHit)
            cursor.setPosition(max(0, hit if hit >= 0 else len(text)))
        editor.setTextCursor(cursor)
        self._inline_history = [(editor.toPlainText(), cursor.position())]
        self._inline_history_index = 0
        self.viewport().update()
        return True

    @Slot()
    def _schedule_inline_reflow(self) -> None:
        if not self._inline_reflowing and self._inline_editor is not None:
            self._inline_reflow_timer.start()

    def _reflow_inline_text(self, record_history: bool = True) -> None:
        """Recompose the active editor without committing its draft to the page."""
        editor = self._inline_editor
        index = self._inline_editor_index
        container = self._inline_editor_container
        if editor is None or container is None or not 0 <= index < len(self._regions):
            return
        value = editor.toPlainText()
        if record_history and self._inline_history and value != self._inline_history[self._inline_history_index][0]:
            del self._inline_history[self._inline_history_index + 1:]
            self._inline_history.append((value, editor.textCursor().position()))
            self._inline_history_index += 1
        if not value.strip():
            if self._inline_overflow_hint is not None:
                self._inline_overflow_hint.setVisible(False)
            statuses = [bool(entry.region.get("text_overflow", False)) for entry in self._regions]
            statuses[index] = False
            self.text_layout_status_changed.emit(statuses)
            self.viewport().update()
            return
        preview_region = dict(self._regions[index].region)
        preview_region["style"] = dict(preview_region.get("style", {}))
        preview_region["applied_text"] = value
        self._inline_reflowing = True
        try:
            preview = self._make_text_item(value, preview_region)
            try:
                overflow = bool(preview_region.get("text_overflow", False))
                if isinstance(preview, QGraphicsTextItem):
                    cursor_position = editor.textCursor().position()
                    editor.document().contentsChanged.disconnect(self._schedule_inline_reflow)
                    editor.setDocument(preview.document().clone(editor))
                    editor.document().contentsChanged.connect(self._schedule_inline_reflow)
                    editor.setFont(preview.font())
                    editor.setDefaultTextColor(preview.defaultTextColor())
                    editor.setTextWidth(preview.textWidth())
                    editor.setTransform(preview.transform())
                    editor.setTransformOriginPoint(preview.transformOriginPoint())
                    editor.setRotation(preview.rotation())
                    editor.setPos(container.mapFromScene(preview.scenePos()))
                    cursor = editor.textCursor()
                    cursor.setPosition(min(cursor_position, len(editor.toPlainText())))
                    editor.setTextCursor(cursor)
                if self._inline_overflow_hint is not None:
                    self._inline_overflow_hint.setVisible(overflow)
                statuses = [
                    bool(entry.region.get("text_overflow", False)) for entry in self._regions
                ]
                statuses[index] = overflow
                self.text_layout_status_changed.emit(statuses)
            finally:
                if preview.scene() is self.scene:
                    self.scene.removeItem(preview)
        finally:
            self._inline_reflowing = False
        if self._inline_history and self._inline_history_index >= 0:
            self._inline_history[self._inline_history_index] = (
                editor.toPlainText(), editor.textCursor().position(),
            )
        self.viewport().update()

    def _restore_inline_history(self, direction: int) -> None:
        if self._inline_editor is None:
            return
        self._inline_reflow_timer.stop()
        self._reflow_inline_text()
        target = self._inline_history_index + int(direction)
        if not 0 <= target < len(self._inline_history):
            return
        self._inline_history_index = target
        value, position = self._inline_history[target]
        self._inline_reflowing = True
        try:
            self._inline_editor.setPlainText(value)
            cursor = self._inline_editor.textCursor()
            cursor.setPosition(min(position, len(value)))
            self._inline_editor.setTextCursor(cursor)
        finally:
            self._inline_reflowing = False
        self._reflow_inline_text(record_history=False)

    def finish_inline_text_edit(self, commit: bool = True) -> None:
        """Close the canvas editor, optionally applying its text once."""
        editor = self._inline_editor
        container = self._inline_editor_container
        index = self._inline_editor_index
        if editor is None:
            return
        self._inline_reflow_timer.stop()
        value = editor.toPlainText()
        original_display = self._inline_original_display_text
        # Clear state first: removing the focused item emits focusOut and can
        # otherwise schedule a second commit.
        self._inline_editor = None
        self._inline_editor_container = None
        self._inline_editor_index = -1
        self._inline_original_display_text = ""
        self._inline_overflow_hint = None
        self._inline_history = []
        self._inline_history_index = -1
        editor.document().contentsChanged.disconnect(self._schedule_inline_reflow)
        editor.clearFocus()
        if container is not None and container.scene() is self.scene:
            self.scene.removeItem(container)
        if 0 <= index < len(self._text_items):
            visible = index < len(self._regions) and bool(self._regions[index].region.get("visible", True))
            self._text_items[index].setVisible(visible)
        if commit and value != original_display and 0 <= index < len(self._regions):
            region = self._regions[index].region
            region["translation"] = value
            region["applied_text"] = value
            region["typeset_completed"] = bool(value.strip() and region.get("style"))
            values = list(self._text_values)
            if index < len(values):
                values[index] = value
            self.update_text_layers([index], values, [entry.region for entry in self._regions])
            self.inline_text_committed.emit(index, value)
        else:
            self.text_layout_status_changed.emit([
                bool(entry.region.get("text_overflow", False)) for entry in self._regions
            ])
        self.viewport().update()

    def _text_item_signature(self, text: str, region: dict) -> str:
        style = dict(region.get("style", {}))
        style_signature = repr(sorted((str(key), repr(value)) for key, value in style.items()))
        geometry = (
            (round(float(region.get("x", 0)), 3), round(float(region.get("y", 0)), 3))
            if style.get("balloon_fit", False) else None
        )
        return (
            f"{self._pixmap_item.pixmap().cacheKey()}|{region.get('id', '')}|{text}|"
            f"{style_signature}|{geometry}|{bool(region.get('typeset_box_manual', False))}"
        )

    def _make_text_item(self, text: str, region: dict) -> QGraphicsItem:
        style = TypographyManager.normalized(region.get("style", {}))
        region["style"] = style
        display_text = str(text)
        if style.get("text_case") == "upper":
            display_text = display_text.upper()
        elif style.get("text_case") == "lower":
            display_text = display_text.lower()
        if style.get("vertical_text"):
            display_text = "\n".join(display_text.replace("\n", ""))
        if not display_text.strip():
            item = EffectsTextItem("")
            item.setData(0, (int(region["width"]), int(region["height"])))
            item.setData(3, self._text_item_signature(text, region))
            item.setData(4, 1.0)
            item.setAcceptedMouseButtons(Qt.NoButton)
            item.setVisible(bool(region.get("visible", True)))
            item.setZValue(2)
            self.scene.addItem(item)
            return item
        if style.get("sfx_enabled", False):
            return self._make_sfx_item(display_text, text, region, style)
        item = EffectsTextItem(display_text)
        item.setDefaultTextColor(QColor(style.get("text_color", "#111111")))
        primary_family = str(style.get("font_family", "Segoe UI"))
        font = QFont(primary_family, int(style.get("font_size", max(8, min(30, region["height"] // 2)))))
        font.setStretch(max(50, min(200, int(style.get("scale_x", 100)))))
        font.setWeight(QFont.Weight(int(style.get("font_weight", 400))))
        font.setItalic(bool(style.get("italic", False)))
        font.setUnderline(bool(style.get("underline", False)))
        font.setStrikeOut(bool(style.get("strikeout", False)))
        item.setFont(font)
        margin = int(style.get("text_margin", style.get("margin", 8)))
        item.setTextWidth(max(1, region["width"] - margin * 2))
        option = item.document().defaultTextOption()
        option.setAlignment({"left": Qt.AlignLeft, "right": Qt.AlignRight}.get(style.get("alignment"), Qt.AlignCenter))
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        item.document().setDefaultTextOption(option)
        item.document().setDefaultStyleSheet(f"p {{ line-height: {max(50, 100 + int(style.get('line_spacing', 0)))}%; }}")
        available_height = max(1, region["height"] - margin * 2)
        layout = None
        balloon_overflow = False
        if not style.get("vertical_text", False):
            if style.get("balloon_fit", False):
                layout = self._fit_text_to_balloon(item, display_text, region, font, style)
                balloon_overflow = bool(layout is not None and layout.get("overflow"))
            if layout is None or layout["overflow"]:
                item.setData(2, None)
                item.setData(8, None)
                layout = self._fit_text_to_rectangle(item, display_text, region, font, style)
                if balloon_overflow:
                    region["text_overflow"] = True
        layout_scale_y = float(layout.get("scale_y", 1.0)) if layout else 1.0
        effective_scale_y = (
            max(0.5, min(2.0, int(style.get("scale_y", 100)) / 100.0)) * layout_scale_y
        )
        item.setTransform(QTransform.fromScale(1.0, effective_scale_y))
        item.setData(4, effective_scale_y)
        if layout is None:
            region["text_overflow"] = bool(
                item.boundingRect().height() * effective_scale_y > available_height + 1
                or item.boundingRect().width() > max(1, region["width"] - margin * 2) + 1
            )
        region.pop("fallback_font_used", None)
        self._apply_text_effects(item, style)
        item.setData(0, (int(region["width"]), int(region["height"])))
        item.setData(3, self._text_item_signature(text, region))
        self._position_text_item(item, region)
        item.setTransformOriginPoint(item.boundingRect().center())
        item.setRotation(float(style.get("rotation", 0)))
        item.setAcceptedMouseButtons(Qt.NoButton)
        item.setVisible(bool(region.get("visible", True)))
        item.setOpacity(max(0.0, min(1.0, int(region.get("opacity", 100)) / 100.0)))
        item.setZValue(2)
        self.scene.addItem(item)
        return item

    @staticmethod
    def _fit_text_to_rectangle(
        item: QGraphicsTextItem, text: str, region: dict, font: QFont, style: dict,
    ) -> dict:
        """Apply the renderer-independent layout snapshot to a Qt text item."""
        margin = max(0, int(style.get("text_margin", style.get("margin", 8))))
        width = max(1.0, float(region["width"]) - margin * 2)
        height = max(1.0, float(region["height"]) - margin * 2)
        manual_scale_y = max(0.5, min(2.0, int(style.get("scale_y", 100)) / 100.0))
        requested = max(6, int(font.pointSize()))
        item.document().setDocumentMargin(0)
        item.setTextWidth(width)
        spacing = int(style.get("line_spacing", 0))
        metrics_cache: dict[int, QFontMetricsF] = {}

        def metrics(size: int) -> QFontMetricsF:
            key = max(6, int(size))
            if key not in metrics_cache:
                measured_font = QFont(font)
                measured_font.setPointSize(key)
                metrics_cache[key] = QFontMetricsF(measured_font)
            return metrics_cache[key]

        signature = layout_signature(text, region["width"], region["height"], style)
        layout = fit_rectangular_text(
            text, width, height / manual_scale_y, requested, 6,
            bool(style.get("auto_fit", False) or style.get("fit_once", False)),
            lambda size: max(1.0, metrics(size).height() + spacing),
            lambda size, value: metrics(size).horizontalAdvance(value)
            * max(0.5, min(2.0, int(style.get("scale_x", 100)) / 100.0)),
            language=str(style.get("language", "auto")),
            hyphenate=bool(style.get("hyphenation", True)),
            orphan_control=bool(style.get("orphan_control", True)),
            hanging_punctuation=bool(style.get("hanging_punctuation", True)),
            signature=signature,
        )
        selected = int(layout["font_size"])
        font.setPointSize(selected)
        item.setFont(font)
        item.setPlainText(str(layout["text"]))
        block = item.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            block_format = cursor.blockFormat()
            block_format.setLineHeight(
                float(layout["line_height"]), QTextBlockFormat.LineHeightTypes.FixedHeight.value,
            )
            cursor.setBlockFormat(block_format)
            block = block.next()
        if style.get("fit_once", False):
            style["font_size"] = selected
            style["auto_fit"] = False
            style.pop("fit_once", None)
            region["style"] = style
            layout["signature"] = layout_signature(text, region["width"], region["height"], style)
        region["layout_snapshot"] = dict(layout)
        region["text_overflow"] = bool(layout["overflow"])
        return layout

    def _make_sfx_item(self, display_text: str, source_text: str, region: dict, style: dict) -> QGraphicsPathItem:
        """Build editable vector glyph paths along the configured SFX trajectory."""
        primary = str(style.get("font_family", "Segoe UI"))
        font = QFont(primary, max(6, int(style.get("font_size", 64))))
        font.setWeight(QFont.Weight(int(style.get("font_weight", 900))))
        font.setItalic(bool(style.get("italic", False)))
        font.setStretch(max(50, min(200, int(style.get("scale_x", 100)))))
        metrics = QFontMetricsF(font)
        margin = max(0, int(style.get("text_margin", 8)))
        available_width = max(1.0, float(region["width"]) - margin * 2)
        available_height = max(1.0, float(region["height"]) - margin * 2)
        text_lines = (
            automatic_sfx_lines(
                display_text, available_width, available_height,
                metrics.horizontalAdvance, metrics.height() * 1.08,
                str(style.get("language", "auto")),
            )
            if style.get("sfx_auto_layout", True)
            else display_text.split("\n")
        )
        region["sfx_layout_lines"] = list(text_lines)
        region["sfx_layout_source"] = str(display_text)
        glyph_key = (
            tuple(text_lines), primary, int(font.pointSize()), int(font.weight()),
            bool(font.italic()), int(font.stretch()),
        )
        cached_glyphs = self._sfx_glyph_cache.get(glyph_key)
        if cached_glyphs is None:
            line_glyphs: list[list[tuple[str, float, QPainterPath]]] = []
            for line in text_lines:
                glyphs: list[tuple[str, float, QPainterPath]] = []
                for char in line:
                    advance = max(1.0, metrics.horizontalAdvance(char or " "))
                    glyph = QPainterPath(); glyph.addText(QPointF(0, 0), font, char)
                    glyphs.append((char, advance, glyph))
                line_glyphs.append(glyphs)
            cached_glyphs = (line_glyphs, "")
            self._sfx_glyph_cache[glyph_key] = cached_glyphs
            self._trim_typesetting_caches()
        else:
            self._sfx_glyph_cache.move_to_end(glyph_key)
        line_glyphs, _legacy_fallback = cached_glyphs
        region.pop("fallback_font_used", None)
        height = max(1.0, float(region["height"]))
        wave = float(style.get("sfx_wave", 0)) / 100.0 * height * 0.22
        cycles = max(1, int(style.get("sfx_wave_cycles", 2)))
        expansion = float(style.get("sfx_expansion", 0)) / 100.0
        impact = float(style.get("sfx_impact", 0)) / 100.0
        path = QPainterPath()
        line_gap = max(1.0, metrics.height() * 1.08)
        line_count = max(1, len(line_glyphs))
        widest_line = max(
            (sum(advance for _char, advance, _glyph in glyphs) for glyphs in line_glyphs),
            default=1.0,
        )
        for line_index, glyphs in enumerate(line_glyphs):
            total = max(1.0, sum(advance for _char, advance, _glyph in glyphs))
            line_cursor = 0.0
            cursor_x = (widest_line - total) / 2.0
            line_y = (line_index - (line_count - 1) / 2.0) * line_gap
            for _char, advance, glyph in glyphs:
                t = (line_cursor + advance / 2.0) / total
                curve_x, arc_y, tangent_angle = bezier_baseline(style, t, float(region["width"]), height)
                wave_y = wave * math.sin(t * cycles * math.tau)
                char_scale = max(0.35, 1.0 + expansion * (t - 0.5))
                transform = QTransform()
                transform.translate(
                    cursor_x + (curve_x - t) * total,
                    line_y + arc_y + wave_y,
                )
                transform.rotate(tangent_angle + (t - 0.5) * impact * 34.0)
                transform.scale(char_scale, 1.0)
                path.addPath(transform.map(glyph))
                cursor_x += advance
                line_cursor += advance
        skew = math.tan(math.radians(float(style.get("sfx_skew", 0))))
        if abs(skew) > 0.001:
            path = QTransform(1.0, 0.0, skew, 1.0, 0.0, 0.0).map(path)
        source_bounds = path.boundingRect()
        if not source_bounds.isEmpty():
            fit = min(
                1.0,
                available_width / max(1.0, source_bounds.width()),
                available_height / max(1.0, source_bounds.height()),
            )
            path = QTransform.fromScale(fit, fit).map(path)
            fitted = path.boundingRect()
            alignment = str(style.get("alignment", "center")).casefold()
            vertical_alignment = str(style.get("vertical_alignment", "center")).casefold()
            if alignment == "left":
                target_x = float(margin)
            elif alignment == "right":
                target_x = float(margin) + available_width - fitted.width()
            else:
                target_x = float(margin) + (available_width - fitted.width()) / 2.0
            if vertical_alignment == "top":
                target_y = float(margin)
            elif vertical_alignment == "bottom":
                target_y = float(margin) + available_height - fitted.height()
            else:
                target_y = float(margin) + (available_height - fitted.height()) / 2.0
            path = QTransform.fromTranslate(target_x - fitted.x(), target_y - fitted.y()).map(path)

            quad_values = {
                corner: (
                    float(style.get(f"sfx_quad_{corner}_x", default_x)),
                    float(style.get(f"sfx_quad_{corner}_y", default_y)),
                )
                for corner, (default_x, default_y) in {
                    "tl": (0, 0), "tr": (100, 0), "bl": (0, 100), "br": (100, 100),
                }.items()
            }
            custom_quad = any(
                abs(quad_values[corner][axis] - default) > 0.01
                for corner, defaults in {
                    "tl": (0, 0), "tr": (100, 0), "bl": (0, 100), "br": (100, 100),
                }.items()
                for axis, default in enumerate(defaults)
            )
            source_path = QPainterPath(path)
            if custom_quad or style.get("sfx_mesh_enabled", False):
                path = self._warp_sfx_path(path, float(region["width"]), height, style)
        item = QGraphicsPathItem(path)
        bounds = path.boundingRect()
        if style.get("gradient_enabled", False):
            gradient = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
            gradient.setColorAt(0.0, QColor(style.get("gradient_start", "#FFFFFF")))
            gradient.setColorAt(1.0, QColor(style.get("gradient_end", "#00CFE8")))
            item.setBrush(QBrush(gradient))
        else:
            item.setBrush(QBrush(QColor(style.get("text_color", "#111111"))))
        stroke = max(0, int(style.get("stroke_width", 0)))
        if stroke:
            pen = QPen(QColor(style.get("stroke_color", "#FFFFFF")), stroke * 2)
            pen.setJoinStyle(Qt.RoundJoin); item.setPen(pen)
        else:
            item.setPen(Qt.NoPen)
        if style.get("stroke2_enabled", False):
            outer = QGraphicsPathItem(path, item)
            outer.setBrush(Qt.NoBrush)
            pen = QPen(
                QColor(style.get("stroke2_color", "#111111")),
                (stroke + max(1, int(style.get("stroke2_width", 6)))) * 2,
            )
            pen.setJoinStyle(Qt.RoundJoin); outer.setPen(pen); outer.setZValue(-2)
        item.setData(0, (int(region["width"]), int(region["height"])))
        item.setData(3, self._text_item_signature(source_text, region))
        item.setData(5, "sfx")
        item.setData(6, tuple(
            int(style.get(f"sfx_quad_{corner}_{axis}", fallback))
            for corner, fallbacks in (("tl", (0, 0)), ("tr", (100, 0)), ("bl", (0, 100)), ("br", (100, 100)))
            for axis, fallback in zip(("x", "y"), fallbacks)
        ))
        item.setData(7, "free_quad")
        item._sfx_source_path = source_path if 'source_path' in locals() else QPainterPath(path)
        item.setTransformOriginPoint(bounds.center())
        item.setRotation(float(style.get("rotation", 0)))
        item.setAcceptedMouseButtons(Qt.NoButton)
        item.setVisible(bool(region.get("visible", True)))
        item.setOpacity(max(0.0, min(1.0, int(region.get("opacity", 100)) / 100.0)))
        item.setZValue(2); self._position_text_item(item, region); self.scene.addItem(item)
        region["text_overflow"] = bounds.width() > region["width"] + 1 or bounds.height() > region["height"] + 1
        return item

    @staticmethod
    def _warp_sfx_path(path: QPainterPath, width: float, height: float, style: dict) -> QPainterPath:
        """Warp every path vertex through the shared homography/3×3 mesh."""
        warp = build_warp(style, max(1.0, width), max(1.0, height))

        def mapped(element) -> QPointF:
            x, y = warp(float(element.x) / max(1.0, width), float(element.y) / max(1.0, height))
            return QPointF(x, y)

        result = QPainterPath()
        index = 0
        while index < path.elementCount():
            element = path.elementAt(index)
            if element.type == QPainterPath.ElementType.MoveToElement:
                result.moveTo(mapped(element)); index += 1
            elif element.type == QPainterPath.ElementType.LineToElement:
                result.lineTo(mapped(element)); index += 1
            elif element.type == QPainterPath.ElementType.CurveToElement and index + 2 < path.elementCount():
                result.cubicTo(
                    mapped(element), mapped(path.elementAt(index + 1)), mapped(path.elementAt(index + 2)),
                )
                index += 3
            else:
                index += 1
        return result

    def _fit_text_to_balloon(
        self, item: QGraphicsTextItem, text: str, region: dict, font: QFont, style: dict,
    ) -> dict | None:
        base = self._pixmap_item.pixmap().toImage()
        if base.isNull():
            return None
        x = max(0, int(region["x"])); y = max(0, int(region["y"]))
        width = min(base.width() - x, max(1, int(region["width"])))
        height = min(base.height() - y, max(1, int(region["height"])))
        if width < 12 or height < 12:
            return None
        use_balloon = bool(style.get("balloon_fit", False))
        shape_type = str(style.get("balloon_shape", "auto")).lower()
        requested_padding = int(
            style.get("balloon_padding", 10) if use_balloon
            else style.get("text_margin", style.get("margin", 8))
        )
        outline = int(style.get("stroke_width", 0)) + (
            int(style.get("stroke2_width", 0)) if style.get("stroke2_enabled", False) else 0
        )
        padding = effective_balloon_padding(width, height, requested_padding, outline)
        manual_box = bool(region.get("typeset_box_manual", False))
        mask_key = (
            int(self._pixmap_item.pixmap().cacheKey()), x, y, width, height,
            padding, use_balloon, shape_type, manual_box,
        )
        cached_geometry = self._balloon_mask_cache.get(mask_key)
        if cached_geometry is None:
            search_x, search_y, search_width, search_height = balloon_search_rect(
                base.width(), base.height(), (x, y, width, height),
            )
            search = self._qimage_rgb_array(base.copy(QRect(
                search_x, search_y, search_width, search_height,
            )))
            local_rect, mask = text_layout_geometry(
                search, (x - search_x, y - search_y, width, height),
                padding, manual_box,
            )
            layout_rect = (
                search_x + local_rect[0], search_y + local_rect[1],
                local_rect[2], local_rect[3],
            )
            self._balloon_mask_cache[mask_key] = (layout_rect, mask)
            self._balloon_mask_cache_bytes += int(mask.nbytes)
            self._balloon_mask_cache.move_to_end(mask_key)
            self._trim_typesetting_caches()
        else:
            self._balloon_mask_cache.move_to_end(mask_key)
            layout_rect, mask = cached_geometry
        width, height = layout_rect[2], layout_rect[3]
        spacing = int(style.get("line_spacing", 4))

        metrics_cache: dict[int, QFontMetricsF] = {}

        def metrics(size: int) -> QFontMetricsF:
            key = max(6, int(size))
            cached = metrics_cache.get(key)
            if cached is None:
                measured_font = QFont(font)
                measured_font.setPointSize(key)
                cached = QFontMetricsF(measured_font)
                metrics_cache[key] = cached
            return cached

        layout_key = (
            mask_key, text, font.family(), int(font.pointSize()), int(font.weight()),
            bool(font.italic()), spacing,
            bool(style.get("auto_fit", False) or style.get("fit_once", False)),
            style.get("alignment", "center"),
            style.get("language", "auto"), bool(style.get("hyphenation", True)),
            bool(style.get("orphan_control", True)), bool(style.get("hanging_punctuation", True)),
            int(style.get("max_horizontal_compression", 12)), bool(style.get("auto_scale", True)),
            shape_type, padding,
        )
        layout = self._text_layout_cache.get(layout_key)
        if layout is None:
            layout = fit_balanced_text(
                text, mask, int(font.pointSize()), 6,
                bool(style.get("auto_fit", False) or style.get("fit_once", False)),
                lambda size: metrics(size).height() + spacing,
                lambda size, value: metrics(size).horizontalAdvance(value),
                language=str(style.get("language", "auto")),
                hyphenate=bool(style.get("hyphenation", True)),
                orphan_control=bool(style.get("orphan_control", True)),
                hanging_punctuation=bool(style.get("hanging_punctuation", True)),
                max_horizontal_compression=int(style.get("max_horizontal_compression", 12)),
                auto_scale=bool(style.get("auto_scale", True)),
                shape_type=shape_type,
                padding=padding,
            )
            self._text_layout_cache[layout_key] = dict(layout)
            self._text_layout_cache.move_to_end(layout_key)
            self._trim_typesetting_caches()
        else:
            self._text_layout_cache.move_to_end(layout_key)
        layout = dict(layout)
        region["text_overflow"] = bool(layout["overflow"])
        if layout["overflow"]:
            return layout

        layout["layout_rect"] = layout_rect
        layout["center_y"] = float(layout["center_y"]) + layout_rect[1] - int(region["y"])
        font.setPointSize(int(layout["font_size"]))
        font.setStretch(max(50, min(200, round(int(style.get("scale_x", 100)) * float(layout.get("scale_x", 1.0))))))
        item.setFont(font)
        item.setPlainText(str(layout["text"]))
        item.setTextWidth(width)
        item.document().setDocumentMargin(0)
        block = item.document().begin()
        for left, right in layout["intervals"]:
            if not block.isValid():
                break
            cursor = QTextCursor(block)
            block_format = cursor.blockFormat()
            block_format.setLeftMargin(max(0.0, float(left)))
            block_format.setRightMargin(max(0.0, float(width - right)))
            block_format.setAlignment({
                "left": Qt.AlignLeft, "right": Qt.AlignRight,
            }.get(style.get("alignment"), Qt.AlignCenter))
            block_format.setLineHeight(
                float(layout["line_height"]), QTextBlockFormat.LineHeightTypes.FixedHeight.value,
            )
            cursor.setBlockFormat(block_format)
            block = block.next()
        item.setData(2, layout["intervals"])
        item.setData(8, float(layout["center_y"]))
        item.setData(9, layout_rect)
        item.setData(10, (int(region["x"]), int(region["y"])))
        effective_y = max(0.5, min(2.0, int(style.get("scale_y", 100)) / 100.0)) * float(layout.get("scale_y", 1.0))
        # QTextDocument may reserve slightly more glyph leading than
        # QFontMetrics predicts. Close that last gap deterministically instead
        # of allowing the final line to cross the balloon edge.
        _left, top, _right, bottom = usable_mask_bounds(mask)
        usable_height = bottom - top
        for _ in range(3):
            actual_height = float(item.boundingRect().height()) * effective_y
            if actual_height <= usable_height + 1:
                break
            current_size = max(4, int(font.pointSize()))
            next_size = max(4, min(current_size - 1, int(current_size * height / max(1.0, actual_height) * 0.97)))
            if next_size >= current_size:
                break
            font.setPointSize(next_size)
            item.setFont(font)
            adjusted_line_height = max(1.0, metrics(next_size).height() + spacing) * float(layout.get("scale_y", 1.0))
            block = item.document().begin()
            while block.isValid():
                cursor = QTextCursor(block)
                block_format = cursor.blockFormat()
                block_format.setLineHeight(
                    adjusted_line_height, QTextBlockFormat.LineHeightTypes.FixedHeight.value,
                )
                cursor.setBlockFormat(block_format)
                block = block.next()
            layout["font_size"] = next_size
            layout["line_height"] = adjusted_line_height
        region["text_overflow"] = bool(item.boundingRect().height() * effective_y > usable_height + 1)
        if style.get("fit_once", False) and not region["text_overflow"]:
            style["font_size"] = int(font.pointSize())
            style["auto_fit"] = False
            style.pop("fit_once", None)
            region["style"] = style
        if not region["text_overflow"]:
            region["balloon_layout_snapshot"] = {
                **layout,
                "signature": balloon_layout_signature(region, text, style),
            }
        else:
            region.pop("balloon_layout_snapshot", None)
        return layout

    @staticmethod
    def _effect_clone(
        item: EffectsTextItem, color: QColor, outline: QPen | None, z_value: float,
        opacity: float = 1.0, blur_radius: float = 0.0, offset: QPointF | None = None,
    ) -> EffectsTextItem:
        clone = EffectsTextItem("")
        clone.setParentItem(item)
        clone.setDocument(item.document().clone(clone))
        clone.setTextWidth(item.textWidth())
        clone.setFont(item.font())
        clone.setAcceptedMouseButtons(Qt.NoButton)
        clone.setFlag(QGraphicsItem.ItemIsSelectable, False)
        clone.setZValue(z_value)
        clone.setOpacity(max(0.0, min(1.0, opacity)))
        clone.blend_mode = item.blend_mode
        if offset is not None:
            clone.setPos(offset)
        text_format = QTextCharFormat()
        text_format.setForeground(color)
        if outline is not None:
            text_format.setTextOutline(outline)
        cursor = QTextCursor(clone.document())
        cursor.select(QTextCursor.Document)
        cursor.mergeCharFormat(text_format)
        if blur_radius > 0:
            blur = QGraphicsBlurEffect()
            blur.setBlurRadius(float(blur_radius))
            clone.setGraphicsEffect(blur)
        children = getattr(item, "_effect_children", [])
        children.append(clone)
        item._effect_children = children
        return clone

    @staticmethod
    def _apply_text_effects(item: EffectsTextItem, style: dict) -> None:
        """Use Qt's native glyph outline/brush so preview remains vector-sharp."""
        item.blend_mode = str(style.get("blend_mode", "normal"))
        bounds = item.boundingRect()
        text_format = QTextCharFormat()
        if style.get("gradient_enabled", False):
            angle = math.radians(float(style.get("gradient_angle", 90)))
            direction_x, direction_y = math.cos(angle), math.sin(angle)
            center_x, center_y = bounds.width() / 2, bounds.height() / 2
            extent = abs(direction_x) * bounds.width() / 2 + abs(direction_y) * bounds.height() / 2
            gradient = QLinearGradient(
                center_x - direction_x * extent, center_y - direction_y * extent,
                center_x + direction_x * extent, center_y + direction_y * extent,
            )
            gradient.setColorAt(0.0, QColor(style.get("gradient_start", "#FFFFFF")))
            gradient.setColorAt(1.0, QColor(style.get("gradient_end", "#00CFE8")))
            text_format.setForeground(QBrush(gradient))
        else:
            text_format.setForeground(QColor(style.get("text_color", "#111111")))
        stroke_width = max(0, int(style.get("stroke_width", 0)))
        cursor = QTextCursor(item.document())
        cursor.select(QTextCursor.Document)
        cursor.mergeCharFormat(text_format)

        # Draw outlines as independent children behind the fill. QText's
        # native outline is centred on the glyph path and, when kept on the
        # same item, visually consumes part of the letter. The opaque fill on
        # the parent now covers the inner half, leaving a genuinely external
        # ring without reducing the glyph body.
        if stroke_width:
            primary = QPen(QColor(style.get("stroke_color", "#FFFFFF")), stroke_width)
            primary.setJoinStyle(Qt.RoundJoin); primary.setCapStyle(Qt.RoundCap)
            CanvasView._effect_clone(item, QColor(0, 0, 0, 0), primary, -1.0)

        if style.get("stroke2_enabled", False):
            # The second value represents additional width outside the first
            # ring, rather than replacing its total diameter.
            outer_width = stroke_width + max(1, int(style.get("stroke2_width", 6)))
            secondary = QPen(
                QColor(style.get("stroke2_color", "#111111")),
                outer_width,
            )
            secondary.setJoinStyle(Qt.RoundJoin); secondary.setCapStyle(Qt.RoundCap)
            CanvasView._effect_clone(item, QColor(0, 0, 0, 0), secondary, -2.0)

        glow_active = style.get("glow_enabled", False) and int(style.get("glow_opacity", 65)) > 0
        if glow_active and not style.get("shadow_enabled", False) and not style.get("stroke2_enabled", False):
            glow = QGraphicsDropShadowEffect()
            glow.setOffset(0, 0)
            glow.setBlurRadius(max(1, int(style.get("glow_radius", 10))) * 2)
            color = QColor(style.get("glow_color", "#00CFE8"))
            color.setAlpha(round(255 * int(style.get("glow_opacity", 65)) / 100))
            glow.setColor(color)
            item.setGraphicsEffect(glow)
        elif glow_active:
            glow_width = stroke_width + (
                int(style.get("stroke2_width", 0)) if style.get("stroke2_enabled", False) else 0
            )
            glow_outline = QPen(QColor(style.get("glow_color", "#00CFE8")), glow_width) if glow_width else None
            CanvasView._effect_clone(
                item, QColor(style.get("glow_color", "#00CFE8")), glow_outline, -3.0,
                int(style.get("glow_opacity", 65)) / 100.0,
                max(1, int(style.get("glow_radius", 10))) * 1.5,
            )

        if style.get("shadow_enabled", False) and int(style.get("shadow_opacity", 65)) > 0:
            shadow_width = stroke_width + (
                int(style.get("stroke2_width", 0)) if style.get("stroke2_enabled", False) else 0
            )
            shadow_outline = QPen(QColor(style.get("shadow_color", "#000000")), shadow_width) if shadow_width else None
            CanvasView._effect_clone(
                item, QColor(style.get("shadow_color", "#000000")), shadow_outline, -4.0,
                int(style.get("shadow_opacity", 65)) / 100.0,
                max(0, int(style.get("shadow_blur", 8))),
                QPointF(float(style.get("shadow_offset_x", 4)), float(style.get("shadow_offset_y", 6))),
            )

    @staticmethod
    def _position_text_item(item: QGraphicsItem, region: dict) -> None:
        style = dict(region.get("style", {}))
        if item.data(5) == "sfx":
            if item.data(7) == "free_quad":
                item.setPos(float(region["x"]), float(region["y"]))
                return
            bounds = item.boundingRect()
            item.setPos(
                float(region["x"]) + (float(region["width"]) - bounds.width()) / 2.0 - bounds.x(),
                float(region["y"]) + (float(region["height"]) - bounds.height()) / 2.0 - bounds.y(),
            )
            return
        margin = int(style.get("text_margin", style.get("margin", 8)))
        text_height = item.boundingRect().height() * float(item.data(4) or 1.0)
        vertical_alignment = style.get("vertical_alignment", "center")
        if vertical_alignment == "top":
            text_y = region["y"] + margin
        elif vertical_alignment == "bottom":
            text_y = region["y"] + region["height"] - margin - text_height
        else:
            text_y = region["y"] + (region["height"] - text_height) / 2
        # Balloon intervals are measured in full box coordinates, so their
        # document starts at the box edge. Rectangular text keeps its margin.
        layout_rect = item.data(9) if item.data(2) else None
        original_box = item.data(10)
        text_x = (
            float(layout_rect[0]) + float(region["x"]) - float(original_box[0])
            if layout_rect and original_box else region["x"]
        ) if item.data(2) else region["x"] + margin
        if item.data(2) and item.data(8) is not None:
            text_y = region["y"] + float(item.data(8)) - text_height / 2.0
        if style.get("optical_center", False) and isinstance(item, QGraphicsTextItem):
            optical_x, optical_y = TypographyManager.optical_offset(item.font(), item.toPlainText())
            if style.get("alignment", "center") == "center":
                text_x += optical_x
            if vertical_alignment == "center":
                text_y += optical_y
        item.setPos(text_x, text_y)

    def mouseDoubleClickEvent(self, event) -> None:
        """Open an editor inside the box under the pointer."""
        if self._inline_editor is not None:
            clicked_item = self.itemAt(event.position().toPoint())
            if clicked_item is self._inline_editor:
                super().mouseDoubleClickEvent(event)
                return
        if self._brush_mode is None and not self._comparison_enabled:
            scene_position = self.mapToScene(event.position().toPoint())
            region_item = self._region_at(scene_position)
            if region_item is not None:
                try:
                    index = self._regions.index(region_item)
                except ValueError:
                    index = -1
                if index >= 0:
                    self.select_regions([index], index)
                    self.region_edit_requested.emit(index)
                    if self.begin_inline_text_edit(index, scene_position):
                        event.accept()
                        return
        super().mouseDoubleClickEvent(event)

    def zoom_in(self) -> None: self.set_zoom(self._zoom + 10)
    def zoom_out(self) -> None: self.set_zoom(self._zoom - 10)

    def set_zoom(self, value: int) -> None:
        started = perf_counter()
        value = max(10, min(400, value))
        if value == self._zoom:
            return
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self._pixmap_item.setTransformationMode(Qt.FastTransformation)
        current_scale = max(0.001, float(self.transform().m11()))
        target_scale = value / 100.0
        self.viewport().setUpdatesEnabled(False)
        try:
            self.scale(target_scale / current_scale, target_scale / current_scale)
        finally:
            self.viewport().setUpdatesEnabled(True)
        self._zoom = value
        self._pending_wheel_zoom = value
        self.zoom_changed.emit(value)
        self._zoom_settle_timer.start()
        self.viewport().update()
        self._schedule_visible_text_render()
        self.performance_measured.emit("Zoom", max(0.0, perf_counter() - started))

    def _finish_interactive_zoom(self) -> None:
        try:
            self.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self._pixmap_item.setTransformationMode(Qt.SmoothTransformation)
            self.viewport().update()
        except RuntimeError:
            # The coalescing timer can fire while Qt is destroying the view.
            return

    def _apply_pending_wheel_zoom(self) -> None:
        self.set_zoom(self._pending_wheel_zoom)

    def view_state(self) -> dict:
        """Capture navigation state independently from editable document data."""
        center = self.mapToScene(self.viewport().rect().center())
        return {"scale": float(self.transform().m11()), "center_x": center.x(), "center_y": center.y()}

    def restore_view_state(self, state: dict) -> None:
        scale = max(0.01, float(state.get("scale", 1.0)))
        self.resetTransform()
        self.scale(scale, scale)
        self._zoom = max(1, int(round(scale * 100)))
        self.zoom_changed.emit(self._zoom)
        self.centerOn(float(state.get("center_x", 0.0)), float(state.get("center_y", 0.0)))
        self._schedule_visible_text_render()

    def fit_image(self) -> None:
        if not self._pixmap_item.pixmap().isNull():
            self._zoom_settle_timer.stop()
            self.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
            self._zoom = int(self.transform().m11() * 100)
            self.zoom_changed.emit(self._zoom)
            self._schedule_visible_text_render()

    def wheelEvent(self, event) -> None:
        if self._brush_mode is not None and event.modifiers() & Qt.AltModifier:
            self.adjust_brush_size(2 if event.angleDelta().y() > 0 else -2)
            event.accept()
        elif event.modifiers() & Qt.ControlModifier:
            delta = 10 if event.angleDelta().y() > 0 else -10
            self._pending_wheel_zoom = max(10, min(400, self._pending_wheel_zoom + delta))
            self._wheel_zoom_timer.start()
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        """Canvas-local shortcuts avoid stealing keys while editing text."""
        if self._inline_editor is not None:
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            selected_marks = [item for item in self._watermark_items if item.isSelected()]
            if selected_marks:
                for item in selected_marks:
                    if item in self._watermark_items:
                        self._watermark_items.remove(item)
                        self.scene.removeItem(item)
                self._emit_watermark_positions()
                event.accept()
                return
            selected_regions = [item for item in self._regions if item.isSelected() and not item.locked]
            if selected_regions:
                self.delete_selected_regions()
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        scene_position = self.mapToScene(event.position().toPoint())
        clicked_item = self.itemAt(event.position().toPoint())
        if self._inline_editor is not None and event.button() == Qt.LeftButton:
            editing_index = self._inline_editor_index
            if 0 <= editing_index < len(self._regions):
                region_item = self._regions[editing_index]
                corner = region_item._edges_at(region_item.mapFromScene(scene_position))
                if any(corner) and not region_item.locked:
                    # The editor is painted above the handles. Finish typing,
                    # then let this same press start the corner gesture.
                    self.finish_inline_text_edit(commit=True)
                    super().mousePressEvent(event)
                    return
            container = self._inline_editor_container
            inside_editor = bool(
                container is not None
                and container.contains(container.mapFromScene(scene_position))
            )
            if clicked_item is self._inline_editor:
                super().mousePressEvent(event)
                return
            if inside_editor:
                self._inline_editor.setFocus(Qt.MouseFocusReason)
                cursor = self._inline_editor.textCursor()
                local = self._inline_editor.mapFromScene(scene_position)
                hit = self._inline_editor.document().documentLayout().hitTest(local, Qt.FuzzyHit)
                cursor.setPosition(max(0, hit if hit >= 0 else len(self._inline_editor.toPlainText())))
                self._inline_editor.setTextCursor(cursor)
                event.accept()
                return
            # The first outside click finalizes editing but is not reused as a
            # drawing/move gesture. This prevents accidental boxes or jumps.
            self.finish_inline_text_edit(commit=True)
            event.accept()
            return
        if clicked_item is self._comparison_divider or (
            self._brush_mode is None and isinstance(clicked_item, (WatermarkPixmapItem, SFXNodeHandle))
        ):
            super().mousePressEvent(event)
            return
        if self._comparison_enabled:
            # Do not create, select or drag OCR boxes while the before/after
            # tool owns the canvas. Only its divider is interactive.
            event.accept()
            return
        if self._brush_mode is not None and event.button() == Qt.LeftButton:
            if self._image_preview_only:
                event.accept()
                return
            if not self._image_scene_rect().contains(scene_position):
                event.accept()
                return
            if event.modifiers() & Qt.ShiftModifier:
                self._size_adjust_last_x = float(event.position().x())
                self._size_adjust_remainder = 0.0
            elif self._brush_mode == "clone" and (event.modifiers() & Qt.AltModifier):
                local_pos = self._pixmap_item.mapFromScene(scene_position)
                self._clone_source_point = QPointF(local_pos)
                self._update_brush_cursor_geometry(scene_position)
            elif self._picker_once or (event.modifiers() & Qt.AltModifier and self._brush_mode == "paint"):
                color = self._visible_color_at(scene_position)
                if color is not None:
                    self._brush_color = color
                    self.color_picked.emit(color)
                self._picker_once = False
                self.viewport().setCursor(Qt.BlankCursor)
                self._update_brush_cursor_geometry(scene_position)
            else:
                self._begin_retouch(scene_position, QPointF(event.position()))
            event.accept()
            return
        if event.button() == Qt.LeftButton and not (event.modifiers() & Qt.ControlModifier) and not self._region_at(scene_position):
            # A new box is independent from the previously active layer. Keep
            # Qt from carrying that selection into the creation gesture.
            self.scene.clearSelection()
            self._draw_origin = self.mapToScene(event.position().toPoint())
            self._drawing_item = self._new_region(QRectF(self._draw_origin, self._draw_origin))
            self.viewport().setCursor(Qt.CrossCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        scene_position = self.mapToScene(event.position().toPoint())
        if self._brush_mode is not None:
            self._update_brush_cursor_geometry(scene_position)
        if self._brush_mode is not None and event.modifiers() & Qt.ShiftModifier and self._stroke_preview is None:
            current_x = float(event.position().x())
            if self._size_adjust_last_x is None:
                self._size_adjust_last_x = current_x
            else:
                # Three screen pixels equal one diameter pixel; this stays
                # predictable regardless of the canvas zoom level.
                movement = current_x - self._size_adjust_last_x + self._size_adjust_remainder
                self._size_adjust_last_x = current_x
                steps = int(movement / 3.0)
                if steps:
                    self.adjust_brush_size(steps)
                    self._size_adjust_remainder = movement - steps * 3.0
                else:
                    self._size_adjust_remainder = movement
            event.accept()
            return
        if not (event.modifiers() & Qt.ShiftModifier):
            self._size_adjust_last_x = None
            self._size_adjust_remainder = 0.0
        if self._brush_mode is not None and self._stroke_preview is not None and self._stroke_points:
            # Do not extend a stroke from hover events. If the release happened
            # outside the viewport, commit only the valid points collected
            # while the left button was physically pressed.
            if not (event.buttons() & Qt.LeftButton):
                self._finish_retouch()
                event.accept()
                return
            current_scene = self.mapToScene(event.position().toPoint())
            image_scene = self._image_scene_rect()
            # Never clamp a dragged cursor to the page boundary. On a long
            # webtoon this transformed a small move outside the image into a
            # connector from the brush point to y=0. Finish at the last valid
            # point and discard the out-of-image event instead.
            if not image_scene.contains(current_scene):
                self._finish_retouch()
                event.accept()
                return
            current = self._pixmap_item.mapFromScene(current_scene)
            previous = next((point for point in reversed(self._stroke_points) if point is not None), None)
            view_position = QPointF(event.position())
            scroll_position = (
                self.horizontalScrollBar().value(), self.verticalScrollBar().value()
            )
            scroll_changed = (
                self._stroke_last_scroll is not None
                and scroll_position != self._stroke_last_scroll
            )
            if previous is not None and self._stroke_last_view_position is not None:
                screen_delta = math.hypot(
                    view_position.x() - self._stroke_last_view_position.x(),
                    view_position.y() - self._stroke_last_view_position.y(),
                )
                scene_delta = math.hypot(
                    current.x() - previous.x(), current.y() - previous.y(),
                )
                # A scroll/viewport jump can move the mapped scene position by
                # hundreds of pixels while the mouse barely moved. Commit the
                # valid portion immediately and discard the teleported point;
                # even a disconnected endpoint would leave an unwanted dot at
                # the top/bottom of a long webtoon page.
                view_scale = max(abs(float(self.transform().m11())), 0.05)
                expected_scene_delta = screen_delta / view_scale
                jump_limit = max(float(self._brush_size * 2.5), expected_scene_delta * 1.45 + 10.0)
                if scroll_changed or scene_delta > jump_limit:
                    self._finish_retouch()
                    event.accept()
                    return
                # Resample trusted motion into short dabs. Rasterization never
                # receives a long raw connector, even after event coalescing.
                spacing = max(1.0, float(self._brush_size) * 0.18)
                steps = max(1, int(math.ceil(scene_delta / spacing)))
                # At 10-25% zoom a short physical mouse movement legitimately
                # spans hundreds of image pixels. The old limit terminated the
                # active stroke, leaving separated erased dabs while the button
                # remained held. View/scene validation above already rejects
                # teleports, so these interpolation samples are trusted.
                if steps > 2048:
                    self._finish_retouch()
                    event.accept()
                    return
                for step in range(1, steps + 1):
                    ratio = step / steps
                    self._stroke_points.append(QPointF(
                        previous.x() + (current.x() - previous.x()) * ratio,
                        previous.y() + (current.y() - previous.y()) * ratio,
                    ))
            else:
                self._stroke_points.append(current)
            self._stroke_view_points.append(view_position)
            self._update_raster_stroke_preview()
            self._stroke_last_view_position = view_position
            self._stroke_last_scroll = scroll_position
            event.accept()
            return
        if self._drawing_item and self._draw_origin:
            current = self.mapToScene(event.position().toPoint())
            rectangle = QRectF(self._draw_origin, current).normalized()
            self._drawing_item.setRect(rectangle)
            self._drawing_item._commit_geometry(notify=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._brush_mode is not None and event.modifiers() & Qt.ShiftModifier and self._stroke_preview is None:
            self._size_adjust_last_x = None
            self._size_adjust_remainder = 0.0
            event.accept()
            return
        if self._brush_mode is not None and self._stroke_preview is not None and event.button() == Qt.LeftButton:
            self._finish_retouch()
            event.accept()
            return
        if self._drawing_item and event.button() == Qt.LeftButton:
            if self._drawing_item.rect().width() < TextRegionItem.MINIMUM_SIZE or self._drawing_item.rect().height() < TextRegionItem.MINIMUM_SIZE:
                self._delete_region_item(self._drawing_item)
            else:
                # Select before emitting regions so the layers panel can keep
                # this exact new id instead of restoring the old active row.
                self.scene.blockSignals(True)
                try:
                    self.scene.clearSelection()
                    self._drawing_item.setSelected(True)
                finally:
                    self.scene.blockSignals(False)
                self._drawing_item._commit_geometry()
            self._drawing_item = None
            self._draw_origin = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        # A release outside the viewport is not guaranteed to return to Qt.
        # Commit only the valid in-image portion now so re-entering the view
        # cannot continue the old path toward the top of the page.
        if self._stroke_preview is not None:
            self._finish_retouch()
        self._brush_cursor.setVisible(False)
        super().leaveEvent(event)


class CanvasShell(QFrame):
    panel_toggle_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasShell")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)
        self.canvas = CanvasView()
        for label, icon_name, tip, action in (("", "zoom-in", "Acercar", self.canvas.zoom_in), ("", "zoom-out", "Alejar", self.canvas.zoom_out), ("Ajustar", None, "Ajustar imagen", self.canvas.fit_image)):
            button = ModernButton(label, icon_name=icon_name)
            button.setObjectName("CanvasTool")
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setFixedHeight(29)
            if action: button.clicked.connect(action)
            toolbar.addWidget(button)
        self.zoom_label = ModernButton("100%")
        self.zoom_label.setObjectName("CanvasZoom")
        self.zoom_label.setToolTip("Restablecer zoom al 100%")
        self.zoom_label.clicked.connect(lambda: self.canvas.set_zoom(100))
        self.zoom_label.setFixedWidth(68)
        toolbar.addWidget(self.zoom_label)
        self.compare_button = ModernButton("Comparar", icon_name="eye")
        self.compare_button.setObjectName("CanvasCompare")
        self.compare_button.setCheckable(True)
        self.compare_button.setToolTip("Original a la izquierda · limpieza a la derecha · arrastra el divisor")
        self.compare_button.setAccessibleName("Comparar original y limpieza")
        self.compare_button.toggled.connect(self.canvas.set_comparison_mode)
        toolbar.addWidget(self.compare_button)
        toolbar.addStretch()
        self.panel_button = ModernButton("", icon_name="layers")
        self.panel_button.setObjectName("CanvasTool")
        self.panel_button.setFixedSize(34, 29)
        self.panel_button.setAccessibleName("Ocultar o mostrar panel de herramientas")
        self.panel_button.setToolTip("Ocultar panel para ampliar el lienzo")
        self.panel_button.clicked.connect(self.panel_toggle_requested)
        self.panel_button.hide()
        toolbar.addWidget(self.panel_button)
        layout.addLayout(toolbar)
        self.welcome = WelcomePanel()
        self.welcome.path_dropped.connect(self.canvas.folder_dropped)
        stage = QWidget()
        self.stage_layout = QStackedLayout(stage)
        self.stage_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_layout.addWidget(self.welcome)
        self.stage_layout.addWidget(self.canvas)
        self.canvas.page_available.connect(lambda ready: self.stage_layout.setCurrentWidget(self.canvas if ready else self.welcome))
        layout.addWidget(stage, 1)
        navigation = QHBoxLayout()
        navigation.setSpacing(6)
        navigation.addStretch()
        self.previous_button = ModernButton("")
        self.next_button = ModernButton("")
        self.previous_button.setObjectName("CanvasPager")
        self.next_button.setObjectName("CanvasPager")
        self.previous_button.setIcon(icon("chevron-left"))
        self.next_button.setIcon(icon("chevron-right"))
        self.previous_button.setToolTip("Página anterior")
        self.next_button.setToolTip("Página siguiente")
        self.previous_button.setAccessibleName("Página anterior")
        self.next_button.setAccessibleName("Página siguiente")
        navigation.addWidget(self.previous_button)
        self.page_label = QLabel("—  /  —")
        self.page_label.setObjectName("CanvasPageLabel")
        self.page_label.setAlignment(Qt.AlignCenter)
        navigation.addWidget(self.page_label)
        navigation.addWidget(self.next_button)
        navigation.addStretch()
        layout.addLayout(navigation)
        self.canvas.zoom_changed.connect(lambda value: self.zoom_label.setText(f"{value}%"))

    def set_compact_controls(self, compact: bool) -> None:
        self.compare_button.setText("" if compact else "Comparar")
        self.panel_button.setVisible(compact)

    def set_panel_collapsed(self, collapsed: bool) -> None:
        self.panel_button.setToolTip(
            "Mostrar panel de herramientas" if collapsed else "Ocultar panel para ampliar el lienzo"
        )
