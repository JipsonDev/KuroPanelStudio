"""Options for «Listo para subir»: slice height, format, quality and .zip."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from core.upload_export import UploadOptions


class UploadExportDialog(QDialog):
    def __init__(self, settings: dict, destination: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exportar listo para subir")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Une las páginas del capítulo y las corta a la misma altura, sin cortar por las cajas de texto, "
            "con nombres 001, 002… y un .zip para subir. Las imágenes numeradas de una exportación "
            "anterior en esa carpeta se reemplazan."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)
        form = QFormLayout()
        self.slice_height = QSpinBox()
        self.slice_height.setRange(400, 20000)
        self.slice_height.setSingleStep(100)
        self.slice_height.setSuffix(" px")
        self.slice_height.setValue(int(settings.get("slice_height", 2000)))
        self.slice_height.setToolTip("Altura de cada imagen; el corte se mueve hasta un 20 % para caer en una zona lisa")
        form.addRow("Altura de cada imagen", self.slice_height)
        self.width = QSpinBox()
        self.width.setRange(0, 4000)
        self.width.setSpecialValueText("Original")
        self.width.setSuffix(" px")
        self.width.setValue(int(settings.get("width", 0)))
        form.addRow("Ancho", self.width)
        self.format_name = QComboBox()
        self.format_name.addItems(["JPG", "WEBP", "PNG"])
        self.format_name.setCurrentText(str(settings.get("format", "JPG")))
        form.addRow("Formato", self.format_name)
        self.quality = QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setValue(int(settings.get("quality", 90)))
        form.addRow("Calidad", self.quality)
        self.make_zip = QCheckBox("Crear también un .zip con las imágenes")
        self.make_zip.setChecked(bool(settings.get("zip", True)))
        form.addRow("", self.make_zip)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(str(destination))
        browse = QPushButton("Cambiar…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        form.addRow("Carpeta", folder_row)
        layout.addLayout(form)
        self.format_name.currentTextChanged.connect(lambda name: self.quality.setEnabled(name != "PNG"))
        self.quality.setEnabled(self.format_name.currentText() != "PNG")
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        export = buttons.addButton("Exportar", QDialogButtonBox.AcceptRole)
        export.setObjectName("Primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        current = Path(self.folder.text())
        chosen = QFileDialog.getExistingDirectory(self, "Carpeta donde crear la exportación", str(current.parent))
        if chosen:
            self.folder.setText(str(Path(chosen) / current.name))

    def accept(self) -> None:
        text = self.folder.text().strip()
        if not text or not Path(text).is_absolute():
            self.folder.setFocus()
            self.folder.setPlaceholderText("Elige una carpeta para la exportación")
            return
        super().accept()

    def options(self) -> UploadOptions:
        return UploadOptions(
            slice_height=self.slice_height.value(), format_name=self.format_name.currentText(),
            quality=self.quality.value(), width=self.width.value(), make_zip=self.make_zip.isChecked(),
        )

    def settings(self) -> dict:
        return {"slice_height": self.slice_height.value(), "width": self.width.value(),
                "format": self.format_name.currentText(), "quality": self.quality.value(),
                "zip": self.make_zip.isChecked()}

    def destination(self) -> Path:
        return Path(self.folder.text().strip())
