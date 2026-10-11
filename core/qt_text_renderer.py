"""Render saved canvas documents into images without creating GUI widgets."""
import math

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QImage, QLinearGradient,
    QPainter, QPen, QTextCharFormat, QTextCursor, QTextDocument, QTextOption,
)


def render_canvas_text(snapshot: dict, style: dict, pad: int) -> Image.Image | None:
    if QGuiApplication.instance() is None or not snapshot.get("document_html") or not snapshot.get("font"):
        return None
    width, height = float(snapshot["width"]), float(snapshot["height"])
    image = QImage(math.ceil(width + pad * 2), math.ceil(height + pad * 2), QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    dpm = round(float(snapshot.get("font_dpi", 96)) / .0254)
    image.setDotsPerMeterX(dpm)
    image.setDotsPerMeterY(dpm)
    document = QTextDocument()
    font = QFont()
    font.fromString(snapshot["font"])
    document.setDefaultFont(font)
    document.documentLayout().setPaintDevice(image)
    document.setHtml(snapshot["document_html"])
    option = document.defaultTextOption()
    option.setAlignment(Qt.AlignmentFlag(snapshot.get("alignment", Qt.AlignCenter.value)))
    option.setWrapMode(QTextOption.WrapMode(snapshot.get("wrap_mode", QTextOption.WrapAtWordBoundaryOrAnywhere.value)))
    document.setDefaultTextOption(option)
    document.setDocumentMargin(0)
    document.setTextWidth(width)
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.Document)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.translate(pad, pad)

    def paint(brush, pen=None):
        character = QTextCharFormat()
        character.setFont(font)
        character.setForeground(brush)
        character.setTextOutline(pen or QPen(Qt.NoPen))
        cursor.mergeCharFormat(character)
        document.drawContents(painter)

    stroke = max(0, int(style.get("stroke_width", 0)))
    outlines = []
    if style.get("stroke2_enabled", False):
        outlines.append((stroke + max(1, int(style.get("stroke2_width", 6))), style.get("stroke2_color", "#111111")))
    if stroke:
        outlines.append((stroke, style.get("stroke_color", "#FFFFFF")))
    for thickness, colour in outlines:
        pen = QPen(QColor(colour), thickness)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        paint(QBrush(QColor(0, 0, 0, 0)), pen)
    brush = QBrush(QColor(style.get("text_color", "#111111")))
    if style.get("gradient_enabled", False):
        angle = math.radians(float(style.get("gradient_angle", 90)))
        dx, dy = math.cos(angle), math.sin(angle)
        extent = abs(dx) * width / 2 + abs(dy) * height / 2
        gradient = QLinearGradient(width / 2 - dx * extent, height / 2 - dy * extent,
                                   width / 2 + dx * extent, height / 2 + dy * extent)
        gradient.setColorAt(0, QColor(style.get("gradient_start", "#FFFFFF")))
        gradient.setColorAt(1, QColor(style.get("gradient_end", "#00CFE8")))
        brush = QBrush(gradient)
    paint(brush)
    painter.end()
    return Image.frombytes("RGBA", (image.width(), image.height()), bytes(image.bits()))
