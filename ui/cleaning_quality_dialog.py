from __future__ import annotations

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout,
)

from core.retouch_layers import patch_layer
from ui.widgets.controls import ModernButton, SectionTitle


def _qimage_rgb(image: QImage) -> np.ndarray:
    rgb = image.convertToFormat(QImage.Format_RGB888)
    raw = np.frombuffer(rgb.bits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
    return raw[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3).copy()


def _numpy_image(rgb: np.ndarray) -> QImage:
    contiguous = np.ascontiguousarray(rgb, dtype=np.uint8)
    height, width = contiguous.shape[:2]
    return QImage(
        contiguous.data, width, height, contiguous.strides[0], QImage.Format_RGB888,
    ).copy()


class CleaningQualityDialog(QDialog):
    """Per-box original/mask/result review without modifying the source image."""

    accepted_patch = Signal(str)
    repeat_requested = Signal(object)
    correct_requested = Signal(object)

    def __init__(self, original: QImage, result: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Control de calidad de limpieza")
        self.setModal(False)
        self.resize(1120, 520)
        self._original = _qimage_rgb(original)
        self._result = result
        self._patches = [
            patch for patch in result.get("patches", []) if patch_layer(patch) == "automatic"
        ]
        self._index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(SectionTitle("Control de calidad por caja"))
        header.addStretch()
        self.counter = QLabel()
        self.counter.setObjectName("LayerCount")
        header.addWidget(self.counter)
        root.addLayout(header)

        views = QHBoxLayout()
        views.setSpacing(10)
        self.original_view = self._preview("Original")
        self.mask_view = self._preview("Máscara protegida")
        self.result_view = self._preview("Resultado + residuos")
        for frame, _label in (self.original_view, self.mask_view, self.result_view):
            views.addWidget(frame, 1)
        root.addLayout(views, 1)

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        controls = QHBoxLayout()
        previous = ModernButton("Anterior", icon_name="arrow-left")
        previous.clicked.connect(lambda: self._move(-1))
        next_button = ModernButton("Siguiente", icon_name="arrow-right")
        next_button.clicked.connect(lambda: self._move(1))
        self.accept_button = ModernButton("Aceptar caja", "Primary", icon_name="check")
        self.accept_button.clicked.connect(self._accept_current)
        repeat = ModernButton("Repetir limpieza", icon_name="refresh")
        repeat.clicked.connect(self._repeat_current)
        correct = ModernButton("Corregir máscara", icon_name="eraser")
        correct.clicked.connect(self._correct_current)
        accept_clean = ModernButton("Aceptar todas sin residuos")
        accept_clean.clicked.connect(self._accept_clean)
        for button in (previous, next_button, self.accept_button, repeat, correct, accept_clean):
            controls.addWidget(button)
        root.addLayout(controls)
        self._refresh()

    @staticmethod
    def _preview(title: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(SectionTitle(title))
        label = QLabel("Sin resultado")
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(260, 260)
        label.setStyleSheet("background:#090B0E; border:1px solid #29323A; border-radius:7px;")
        layout.addWidget(label, 1)
        return frame, label

    def _current(self) -> dict | None:
        return self._patches[self._index] if self._patches else None

    def _move(self, amount: int) -> None:
        if self._patches:
            self._index = (self._index + int(amount)) % len(self._patches)
            self._refresh()

    def _bounds(self, patch: dict) -> tuple[int, int, int, int]:
        target = patch.get("target") or {
            "x": patch.get("x", 0), "y": patch.get("y", 0),
            "width": np.asarray(patch["pixels"]).shape[1],
            "height": np.asarray(patch["pixels"]).shape[0],
        }
        pad = max(12, int(min(int(target.get("width", 1)), int(target.get("height", 1))) * 0.05))
        x1 = max(0, int(target.get("x", 0)) - pad)
        y1 = max(0, int(target.get("y", 0)) - pad)
        x2 = min(self._original.shape[1], int(target.get("x", 0)) + int(target.get("width", 1)) + pad)
        y2 = min(self._original.shape[0], int(target.get("y", 0)) + int(target.get("height", 1)) + pad)
        return x1, y1, x2, y2

    @staticmethod
    def _place_patch(canvas: np.ndarray, bounds, patch: dict) -> None:
        x1, y1, x2, y2 = bounds
        px, py = int(patch.get("x", 0)), int(patch.get("y", 0))
        pixels = np.asarray(patch.get("pixels"), dtype=np.uint8)
        if pixels.ndim != 3:
            return
        ph, pw = pixels.shape[:2]
        ix1, iy1 = max(x1, px), max(y1, py)
        ix2, iy2 = min(x2, px + pw), min(y2, py + ph)
        if ix2 <= ix1 or iy2 <= iy1:
            return
        source = pixels[iy1 - py:iy2 - py, ix1 - px:ix2 - px]
        target = canvas[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1]
        mask = patch.get("mask")
        if mask is None:
            target[:] = source
            return
        alpha = np.asarray(mask, dtype=np.float32)[iy1 - py:iy2 - py, ix1 - px:ix2 - px, None] / 255.0
        target[:] = np.clip(source * alpha + target * (1.0 - alpha), 0, 255).astype(np.uint8)

    def _mask_for_crop(self, patch: dict, bounds) -> np.ndarray:
        x1, y1, x2, y2 = bounds
        output = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        target = patch.get("target", {})
        for entry in self._result.get("review_entries", []):
            if target and entry.get("target", {}) != target:
                continue
            ex, ey = int(entry.get("x", 0)), int(entry.get("y", 0))
            mask = np.asarray(entry.get("mask"), dtype=np.uint8)
            if mask.ndim != 2:
                continue
            eh, ew = mask.shape
            ix1, iy1 = max(x1, ex), max(y1, ey)
            ix2, iy2 = min(x2, ex + ew), min(y2, ey + eh)
            if ix2 > ix1 and iy2 > iy1:
                np.maximum(
                    output[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1],
                    mask[iy1 - ey:iy2 - ey, ix1 - ex:ix2 - ex],
                    out=output[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1],
                )
        if not np.any(output):
            px, py = int(patch.get("x", 0)), int(patch.get("y", 0))
            source = np.asarray(patch.get("mask"), dtype=np.uint8)
            if source.ndim == 2:
                ph, pw = source.shape
                ix1, iy1 = max(x1, px), max(y1, py)
                ix2, iy2 = min(x2, px + pw), min(y2, py + ph)
                if ix2 > ix1 and iy2 > iy1:
                    output[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1] = source[
                        iy1 - py:iy2 - py, ix1 - px:ix2 - px
                    ]
        return output

    @staticmethod
    def _set_pixmap(label: QLabel, rgb: np.ndarray) -> None:
        pixmap = QPixmap.fromImage(_numpy_image(rgb))
        label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _refresh(self) -> None:
        patch = self._current()
        enabled = patch is not None
        self.accept_button.setEnabled(enabled)
        if patch is None:
            self.counter.setText("0 / 0")
            self.status.setText("No hay cajas automáticas que revisar.")
            return
        bounds = self._bounds(patch)
        x1, y1, x2, y2 = bounds
        original = self._original[y1:y2, x1:x2].copy()
        result = original.copy()
        self._place_patch(result, bounds, patch)
        mask = self._mask_for_crop(patch, bounds)
        masked = original.copy()
        alpha = (mask.astype(np.float32) / 255.0 * 0.62)[:, :, None]
        red = np.full_like(masked, (255, 45, 92))
        masked = np.clip(red * alpha + masked * (1.0 - alpha), 0, 255).astype(np.uint8)
        residual = np.asarray(patch.get("residual_mask", []), dtype=np.uint8)
        if residual.ndim == 2 and np.any(residual):
            residual_patch = {**patch, "pixels": np.full((*residual.shape, 3), (255, 190, 0), dtype=np.uint8), "mask": residual}
            self._place_patch(result, bounds, residual_patch)
        self._set_pixmap(self.original_view[1], original)
        self._set_pixmap(self.mask_view[1], masked)
        self._set_pixmap(self.result_view[1], result)
        self.counter.setText(f"{self._index + 1} / {len(self._patches)}")
        residue = int(patch.get("residual_pixels", 0))
        status = str(patch.get("qc_status", "pending"))
        self.status.setText(
            (f"⚠ Se detectaron {residue:,} píxeles de posible texto residual. " if residue else "Sin residuos medibles. ")
            + ("Caja aceptada." if status == "accepted" else "Pendiente de revisión.")
            + " La máscara roja excluye el contorno y las puntas protegidas."
        )

    def _accept_current(self) -> None:
        patch = self._current()
        if patch is None:
            return
        patch["qc_status"] = "accepted"
        self.accepted_patch.emit(str(patch.get("id", "")))
        self._refresh()

    def _accept_clean(self) -> None:
        for patch in self._patches:
            if int(patch.get("residual_pixels", 0)) < 4:
                patch["qc_status"] = "accepted"
                self.accepted_patch.emit(str(patch.get("id", "")))
        self._refresh()

    def _repeat_current(self) -> None:
        patch = self._current()
        if patch is not None:
            self.repeat_requested.emit(dict(patch.get("target", {})))

    def _correct_current(self) -> None:
        patch = self._current()
        if patch is None:
            return
        target = dict(patch.get("target", {}))
        entries = [
            entry for entry in self._result.get("review_entries", [])
            if not target or entry.get("target", {}) == target
        ]
        self.correct_requested.emit({"target": target, "entries": entries})
