"""A tiled, continuous chapter viewport sharing the project's page geometry."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from core.chapter_strip import ChapterStrip
from core.watermark_manager import prepare_watermark, watermark_bytes, watermark_positions, watermark_vertical_bounds, normalized_watermark
from core.workers import ModelTask
from ui.canvas_view import WatermarkPixmapItem


class StripView(QGraphicsView):
    edit_page_requested = Signal(int)
    visible_page_changed = Signal(int)
    watermark_positions_changed = Signal(str, object)
    load_failed = Signal(str)
    zoom_changed = Signal(int)

    TILE_WIDTH = 1024
    TILE_HEIGHT = 2048

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#111519"))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.pixel_budget = 48 * 1024 * 1024
        self.source: ChapterStrip | None = None
        self._generation = 0
        self._tiles = {}
        self._watermarks = {}
        self._mark_parents = []
        self._task = None
        self._failed = set()
        self._visible_index = -1
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._update_tiles)
        # One bounded batch at a time; decoding cannot queue the whole chapter.
        self._pool = QThreadPool.globalInstance()
        self.verticalScrollBar().valueChanged.connect(self._schedule)
        self.horizontalScrollBar().valueChanged.connect(self._schedule)

    def set_source(self, source: ChapterStrip) -> None:
        self.dispose()
        self.source = source
        self.setSceneRect(0, 0, *source.size)
        self.fit_width()
        self.jump_to_page(0)
        self._schedule()

    def dispose(self) -> None:
        self._generation += 1
        self._timer.stop()
        if self._task is not None:
            self._task.cancel()
        self._tiles.clear()
        self._watermarks.clear()
        self._mark_parents.clear()
        self._failed.clear()
        self._visible_index = -1
        self.scene().clear()
        # An in-flight task owns its old source until completion. Do not wait
        # for its decoding lock on the GUI thread when changing chapters.
        self.source = None

    def _schedule(self, *_args):
        if self.source is not None and self.isVisible():
            self._timer.start()

    def visible_page(self) -> int:
        if self.source is None:
            return 0
        return self.source.page_at(self.mapToScene(self.viewport().rect().center()).y())

    def jump_to_page(self, index: int) -> None:
        if self.source is None:
            return
        index = max(0, min(index, len(self.source.spans) - 1))
        span = self.source.spans[index]
        half_view = self.viewport().height() / max(.01, self.transform().m11()) / 2
        self.centerOn(self.source.width / 2, span.y + min(span.height / 2, half_view))
        self._schedule()

    def fit_width(self):
        if self.source is not None:
            self.set_zoom(min(100, max(10, int((self.viewport().width() - 24) * 100 / self.source.width))))

    def fit_image(self):
        self.fit_width()

    def zoom_in(self):
        self.set_zoom(round(self.transform().m11() * 100) + 10)

    def zoom_out(self):
        self.set_zoom(round(self.transform().m11() * 100) - 10)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if self.source is not None:
            for span in self.source.intersecting(rect.top(), rect.bottom()):
                painter.fillRect(QRectF(span.x, span.y, span.width, span.height), QColor("#30353b"))

    def drawForeground(self, painter, rect):
        if self.source is not None:
            painter.save()
            pen = QPen(QColor("#16c6d7"), 1, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for span in self.source.intersecting(rect.top(), rect.bottom()):
                if span.index:
                    painter.drawLine(0, span.y, self.source.width, span.y)
            painter.restore()

    def set_zoom(self, percent: int):
        percent = min(400, max(10, percent))
        self.resetTransform()
        self.scale(percent / 100, percent / 100)
        self.zoom_changed.emit(percent)
        self._schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.set_zoom(round(self.transform().m11() * 100) + (10 if event.angleDelta().y() > 0 else -10))
            event.accept()
        else:
            super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.source is not None:
            y = self.mapToScene(event.position().toPoint()).y()
            self.edit_page_requested.emit(self.source.page_at(y))
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected_watermarks()
            event.accept()
        else:
            super().keyPressEvent(event)

    def delete_selected_watermarks(self):
        for name, items in list(self._watermarks.items()):
            for item in list(items):
                if item.isSelected():
                    self._delete_watermark(name, item)

    def cache_bytes(self):
        return sum(item.pixmap().width() * item.pixmap().height() * 4 for item in self._tiles.values())

    def _wanted(self):
        if self.source is None:
            return []
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        # Keep two source samples per physical display pixel. The old path
        # reduced to logical zoom, ignoring display density, then Qt filtered
        # the already reduced tile again. Reading zooms now retain native
        # pixels; distant views use a small set of oversampled detail levels.
        physical_zoom = self.transform().m11() * self.devicePixelRatioF()
        target = min(1., max(.25, physical_zoom * 2))
        scale = 1. if target > .5 else .5 if target > .25 else .25
        top = max(0, int(rect.top() - rect.height() * .5))
        bottom = min(self.source.height, math.ceil(rect.bottom() + rect.height() * .5))
        left = max(0, int(rect.left() - 128))
        right = min(self.source.width, math.ceil(rect.right() + 128))
        positions = [(x, y)
                for y in range(top // self.TILE_HEIGHT * self.TILE_HEIGHT, bottom, self.TILE_HEIGHT)
                for x in range(left // self.TILE_WIDTH * self.TILE_WIDTH, right, self.TILE_WIDTH)]
        def visible(position):
            x, y = position
            return QRectF(x, y, min(self.TILE_WIDTH, self.source.width - x),
                          min(self.TILE_HEIGHT, self.source.height - y)).intersects(rect)

        # Prefer lower detail across the entire visible area to sharp tiles
        # surrounded by blank corners on large/high-density displays.
        visible_count = sum(visible(position) for position in positions)
        while scale > .125 and visible_count * self.TILE_WIDTH * self.TILE_HEIGHT * scale ** 2 * 4 > self.pixel_budget:
            scale /= 2
        positions.sort(key=lambda position: (
            not visible(position),
            abs(position[1] + self.TILE_HEIGHT / 2 - rect.center().y())
            + abs(position[0] + self.TILE_WIDTH / 2 - rect.center().x())))
        keys = [(x, y, scale) for x, y in positions]
        cost = max(1, int(self.TILE_WIDTH * self.TILE_HEIGHT * scale * scale * 4))
        return keys[:max(1, self.pixel_budget // cost)]

    @Slot()
    def _update_tiles(self):
        if self.source is None or not self.isVisible():
            return
        wanted = self._wanted()
        needed = set(wanted)
        for key in list(self._tiles):
            if key not in needed:
                self.scene().removeItem(self._tiles.pop(key))
        index = self.visible_page()
        if index != self._visible_index:
            self._visible_index = index
            self.visible_page_changed.emit(index)
        if self._task is not None:
            return
        missing = [key for key in wanted if key not in self._tiles and key not in self._failed][:4]
        if not missing:
            return
        source, generation = self.source, self._generation

        def load(_progress, cancelled):
            results = []
            for x, y, scale in missing:
                if cancelled():
                    break
                crop = source.crop((x, y, min(source.width, x + self.TILE_WIDTH),
                                    min(source.height, y + self.TILE_HEIGHT)))
                if scale < 1:
                    from PIL import Image
                    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                                       Image.Resampling.LANCZOS)
                raw = crop.tobytes()
                image = QImage(raw, crop.width, crop.height, crop.width * 3, QImage.Format_RGB888).copy()
                results.append(((x, y, scale), image))
            return generation, results

        task = ModelTask(load)
        self._task = task
        self._loading_keys = missing
        task.signals.completed.connect(self._tiles_loaded)
        task.signals.failed.connect(self._tiles_failed)
        self._pool.start(task)

    @Slot(object)
    def _tiles_loaded(self, result):
        self._task = None
        generation, results = result
        if generation != self._generation or self.source is None:
            self._schedule()
            return
        wanted = set(self._wanted())
        for key, image in results:
            if key not in wanted:
                continue
            item = QGraphicsPixmapItem(QPixmap.fromImage(image))
            item.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
            item.setAcceptedMouseButtons(Qt.NoButton)
            item.setPos(key[0], key[1])
            width = min(self.TILE_WIDTH, self.source.width - key[0])
            height = min(self.TILE_HEIGHT, self.source.height - key[1])
            item.setTransform(QTransform.fromScale(width / image.width(), height / image.height()))
            self.scene().addItem(item)
            self._tiles[key] = item
        self._schedule()

    @Slot(str)
    def _tiles_failed(self, message):
        self._task = None
        if message != "Operación cancelada":
            self._failed.update(self._loading_keys)
            self.load_failed.emit(message)
        self._schedule()

    def set_watermarks(self, plan: dict):
        for parent in self._mark_parents:
            self.scene().removeItem(parent)
        self._mark_parents.clear()
        self._watermarks.clear()
        if self.source is None:
            return
        prepared = {}
        for span, page in zip(self.source.spans, self.source.pages):
            settings = plan.get(page.name)
            if settings is None:
                continue
            appearance = normalized_watermark(settings)
            cache_key = (span.width, settings.get("chapter_fit_height", span.height),
                         watermark_bytes(appearance), *(appearance[name] for name in (
                             "size_mode", "scale_percent", "width_px", "rotation", "opacity",
                             "trim_transparent", "keep_inside", "margin_x", "margin_y")))
            if cache_key not in prepared:
                mark = prepare_watermark((span.width, span.height), settings)
                if mark is None:
                    prepared[cache_key] = None
                else:
                    raw = mark.tobytes("raw", "RGBA")
                    pixmap = QPixmap.fromImage(QImage(raw, mark.width, mark.height, mark.width * 4, QImage.Format_RGBA8888).copy())
                    prepared[cache_key] = pixmap, mark.size
            if prepared[cache_key] is None:
                continue
            pixmap, mark_size = prepared[cache_key]
            parent = QGraphicsRectItem(0, 0, span.width, span.height)
            parent.setPen(QPen(Qt.NoPen))
            parent.setPos(span.x, span.y)
            parent.setZValue(9)
            parent.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
            self.scene().addItem(parent)
            self._mark_parents.append(parent)
            config = dict(settings)
            if not config.get("chapter_resolved"):
                config["positions"] = config.get("page_positions", {}).get(page.name, config.get("positions"))
            low, high = watermark_vertical_bounds((span.width, span.height), mark_size, normalized_watermark(config))
            items = self._watermarks.setdefault(page.name, [])
            for x, y in watermark_positions((span.width, span.height), mark_size, config):
                item = WatermarkPixmapItem(
                    pixmap, config.get("blend_mode", "normal"), QRectF(0, 0, span.width, span.height),
                    config.get("keep_inside", True), low, high,
                    lambda name=page.name: self._emit_watermark_positions(name),
                    lambda item, name=page.name: self._delete_watermark(name, item),
                )
                item.setParentItem(parent)
                item.setPos(x, y)
                items.append(item)

    def _emit_watermark_positions(self, name):
        self.watermark_positions_changed.emit(name, [
            [round(item.x()), round(item.y())] for item in self._watermarks.get(name, [])
        ])

    def _delete_watermark(self, name, item):
        items = self._watermarks.get(name, [])
        if item in items:
            items.remove(item)
            self.scene().removeItem(item)
            self._emit_watermark_positions(name)
