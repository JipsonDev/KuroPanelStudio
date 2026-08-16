from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout)

from core.typography_manager import TypographyManager
from ui.font_utils import commit_font_combo_text, configure_searchable_font_combo, select_font_family


class TypographyDialog(QDialog):
    def __init__(self, style: dict, presets: dict[str, dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tipografía profesional")
        self.setMinimumWidth(480)
        self.presets = presets
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form)
        self.preset = QComboBox(); self.preset.addItem("Personalizado"); self.preset.addItems(sorted(presets))
        self.preset.currentTextChanged.connect(self._load_preset); form.addRow("Estilo reutilizable", self.preset)
        self.family = QComboBox(); configure_searchable_font_combo(self.family); form.addRow("Fuente", self.family)
        self.weight = QComboBox()
        for label, value in (("Normal", 400), ("Medium", 500), ("Seminegrita", 600),
                             ("Negrita (Bold)", 700), ("Extra negrita", 800), ("Black", 900)):
            self.weight.addItem(label, value)
        form.addRow("Peso", self.weight)
        emphasis = QHBoxLayout()
        self.italic = QCheckBox("Cursiva"); self.underline = QCheckBox("Subrayado"); self.strikeout = QCheckBox("Tachado")
        emphasis.addWidget(self.italic); emphasis.addWidget(self.underline); emphasis.addWidget(self.strikeout)
        form.addRow("Variantes", emphasis)
        self.text_case = QComboBox()
        self.text_case.addItem("Como fue escrito", "original")
        self.text_case.addItem("TODO MAYÚSCULAS", "upper")
        self.text_case.addItem("todo minúsculas", "lower")
        form.addRow("Mayúsculas/minúsculas", self.text_case)
        self.size = self._spin(6, 300); form.addRow("Tamaño", self.size)
        # Hidden compatibility values for old projects. Composition now uses
        # only the explicit rectangular-box controls.
        self.auto_fit = QCheckBox(); self.auto_fit.hide()
        self.optical_center = QCheckBox(); self.optical_center.hide()
        self.spacing = self._spin(-10, 100); form.addRow("Interlineado", self.spacing)
        self.alignment = QComboBox(); self.alignment.addItems(["left", "center", "right"]); form.addRow("Alineación", self.alignment)
        self.vertical_alignment = QComboBox(); self.vertical_alignment.addItems(["top", "center", "bottom"]); form.addRow("Alineación vertical", self.vertical_alignment)
        self.stroke = self._spin(0, 20)
        self.rotation = self._spin(-180, 180); form.addRow("Rotación", self.rotation)
        self.vertical = QCheckBox("Texto vertical"); form.addRow("Dirección", self.vertical)
        self.margin = self._spin(0, 200); form.addRow("Margen interior", self.margin)
        self.text_color = QLineEdit(); self.stroke_color = QLineEdit()
        form.addRow("Color del texto", self._color_row(self.text_color))
        self.preset_name = QLineEdit(); self.preset_name.setPlaceholderText("Ej. Narrador, protagonista…"); form.addRow("Guardar como", self.preset_name)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Aplicar")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.set_style(TypographyManager.normalized(style))

    @staticmethod
    def _spin(low: int, high: int) -> QSpinBox:
        field = QSpinBox(); field.setRange(low, high); return field

    def _color_row(self, field: QLineEdit):
        container = QHBoxLayout(); container.addWidget(field)
        button = QPushButton("Elegir")
        button.clicked.connect(lambda: self._pick_color(field)); container.addWidget(button)
        return container

    def _pick_color(self, field: QLineEdit) -> None:
        color = QColorDialog.getColor(QColor(field.text() or "#FFFFFF"), self)
        if color.isValid(): field.setText(color.name().upper())

    def _load_preset(self, name: str) -> None:
        if name in self.presets: self.set_style(TypographyManager.normalized(self.presets[name]))

    def set_style(self, style: dict) -> None:
        select_font_family(self.family, style["font_family"]); self.size.setValue(style["font_size"])
        self.weight.setCurrentIndex(max(0, self.weight.findData(style["font_weight"])))
        self.italic.setChecked(style["italic"]); self.underline.setChecked(style["underline"]); self.strikeout.setChecked(style["strikeout"])
        self.text_case.setCurrentIndex(max(0, self.text_case.findData(style["text_case"])))
        self.auto_fit.setChecked(style["auto_fit"]); self.spacing.setValue(style["line_spacing"])
        self.optical_center.setChecked(style["optical_center"])
        self.alignment.setCurrentText(style["alignment"]); self.vertical_alignment.setCurrentText(style["vertical_alignment"])
        self.stroke.setValue(style["stroke_width"]); self.rotation.setValue(style["rotation"])
        self.vertical.setChecked(style["vertical_text"]); self.margin.setValue(style["text_margin"])
        self.text_color.setText(style["text_color"]); self.stroke_color.setText(style["stroke_color"])

    def values(self) -> tuple[dict, str]:
        family = commit_font_combo_text(self.family)
        return TypographyManager.normalized({
            "font_family": family, "font_size": self.size.value(), "auto_fit": False,
            "optical_center": False, "balloon_fit": False, "auto_scale": True,
            "font_weight": int(self.weight.currentData() or 400), "italic": self.italic.isChecked(),
            "underline": self.underline.isChecked(), "strikeout": self.strikeout.isChecked(),
            "text_case": str(self.text_case.currentData() or "original"),
            "line_spacing": self.spacing.value(), "alignment": self.alignment.currentText(), "vertical_alignment": self.vertical_alignment.currentText(),
            "stroke_width": self.stroke.value(), "rotation": self.rotation.value(), "vertical_text": self.vertical.isChecked(),
            "text_margin": self.margin.value(), "text_color": self.text_color.text(), "stroke_color": self.stroke_color.text(),
        }), self.preset_name.text().strip()
