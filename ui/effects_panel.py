from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core.typography_manager import TypographyManager
from ui.widgets.controls import ModernButton, SectionTitle


BUILTIN_EFFECT_PRESETS = {
    "Diálogo": {
        "font_family": "Segoe UI", "font_weight": 600, "stroke_width": 2,
        "stroke_color": "#FFFFFF", "gradient_enabled": False, "glow_enabled": False,
    },
    "Grito": {
        "font_family": "Arial", "font_weight": 900, "font_size": 48,
        "stroke_width": 3, "stroke_color": "#111111", "stroke2_enabled": True,
        "stroke2_width": 7, "stroke2_color": "#FFFFFF", "gradient_enabled": True,
        "gradient_start": "#FFD23F", "gradient_end": "#FF4D4D", "gradient_angle": 90,
    },
    "Pensamiento": {
        "font_family": "Georgia", "font_weight": 400, "italic": True,
        "stroke_width": 1, "stroke_color": "#FFFFFF", "gradient_enabled": False,
        "glow_enabled": True, "glow_color": "#FFFFFF", "glow_radius": 6, "glow_opacity": 45,
    },
    "Narrador": {
        "font_family": "Segoe UI", "font_weight": 700, "stroke_width": 0,
        "gradient_enabled": False, "shadow_enabled": True, "shadow_color": "#000000",
        "shadow_blur": 3, "shadow_offset_x": 2, "shadow_offset_y": 3, "shadow_opacity": 45,
    },
    "SFX": {
        "font_family": "Arial", "font_weight": 900, "font_size": 64,
        "stroke_width": 4, "stroke_color": "#111111", "stroke2_enabled": True,
        "stroke2_width": 9, "stroke2_color": "#FFFFFF", "gradient_enabled": True,
        "gradient_start": "#FF6A00", "gradient_end": "#FF1493", "gradient_angle": 0,
        "glow_enabled": True, "glow_color": "#00CFE8", "glow_radius": 12,
        "glow_opacity": 70, "shadow_enabled": True, "shadow_blur": 6,
        "shadow_offset_x": 6, "shadow_offset_y": 8, "shadow_opacity": 65,
    },
}


class TextEffectsPanel(QScrollArea):
    """Layer effects kept separate from typography and applied live."""

    effects_changed = Signal(dict)
    preset_save_requested = Signal(str)
    preset_apply_requested = Signal(str, object)
    copy_requested = Signal()
    paste_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(250)
        self.setMaximumWidth(330)
        self._loading = False
        self._layer_index = -1
        self._color_buttons: dict[str, ModernButton] = {}
        self._colors = {
            "stroke_color": "#FFFFFF", "gradient_start": "#FFFFFF",
            "gradient_end": "#00CFE8", "glow_color": "#00CFE8",
            "stroke2_color": "#111111", "shadow_color": "#000000",
        }
        self._presets: dict[str, dict] = {}

        content = QFrame(); content.setObjectName("AIPanel"); self.setWidget(content)
        root = QVBoxLayout(content); root.setContentsMargins(10, 12, 10, 12); root.setSpacing(10)
        root.addWidget(SectionTitle("Efectos de texto"))
        self.layer_label = QLabel("Selecciona una capa de texto")
        self.layer_label.setObjectName("Muted"); self.layer_label.setWordWrap(True); root.addWidget(self.layer_label)

        root.addWidget(SectionTitle("Preset profesional"))
        self.preset = QComboBox(); self.preset.addItem("Personalizado")
        self.preset.currentTextChanged.connect(self._preset_selected)
        root.addWidget(self.preset)
        preset_row = QHBoxLayout()
        self.preset_name = QLineEdit(); self.preset_name.setPlaceholderText("Nombre del preset")
        save_preset = ModernButton("Guardar", "Primary")
        save_preset.clicked.connect(self._save_preset)
        preset_row.addWidget(self.preset_name, 1); preset_row.addWidget(save_preset)
        root.addLayout(preset_row)
        transfer_row = QHBoxLayout()
        copy_button = ModernButton("Copiar", icon_name="copy")
        paste_button = ModernButton("Pegar", icon_name="clipboard")
        copy_button.clicked.connect(self.copy_requested.emit); paste_button.clicked.connect(self.paste_requested.emit)
        transfer_row.addWidget(copy_button); transfer_row.addWidget(paste_button)
        root.addLayout(transfer_row)

        root.addWidget(SectionTitle("Trazo"))
        self.stroke_enabled = QCheckBox("Activar trazo exterior")
        self.stroke_width = self._spin(1, 20, " px")
        root.addWidget(self.stroke_enabled); root.addWidget(QLabel("GROSOR")); root.addWidget(self.stroke_width)
        root.addWidget(self._color_button("stroke_color", "Color del trazo"))
        self.stroke2_enabled = QCheckBox("Segundo trazo exterior")
        self.stroke2_width = self._spin(1, 40, " px")
        self.stroke2_width.setToolTip("Grosor adicional que se extiende fuera del primer trazo")
        root.addWidget(self.stroke2_enabled); root.addWidget(QLabel("GROSOR ADICIONAL EXTERIOR")); root.addWidget(self.stroke2_width)
        root.addWidget(self._color_button("stroke2_color", "Color del segundo trazo"))

        root.addWidget(SectionTitle("Degradado"))
        self.gradient_enabled = QCheckBox("Relleno degradado")
        root.addWidget(self.gradient_enabled)
        root.addWidget(self._color_button("gradient_start", "Color inicial"))
        root.addWidget(self._color_button("gradient_end", "Color final"))
        self.gradient_angle = self._spin(0, 359, "°")
        root.addWidget(QLabel("ÁNGULO")); root.addWidget(self.gradient_angle)

        root.addWidget(SectionTitle("Resplandor"))
        self.glow_enabled = QCheckBox("Activar resplandor")
        root.addWidget(self.glow_enabled)
        root.addWidget(self._color_button("glow_color", "Color del resplandor"))
        self.glow_radius = self._spin(1, 80, " px")
        root.addWidget(QLabel("RADIO")); root.addWidget(self.glow_radius)
        root.addWidget(QLabel("OPACIDAD"))
        self.glow_opacity = QSlider(Qt.Horizontal); self.glow_opacity.setRange(0, 100)
        root.addWidget(self.glow_opacity)

        root.addWidget(SectionTitle("Sombra paralela"))
        self.shadow_enabled = QCheckBox("Activar sombra")
        root.addWidget(self.shadow_enabled)
        root.addWidget(self._color_button("shadow_color", "Color de la sombra"))
        self.shadow_blur = self._spin(0, 80, " px")
        self.shadow_x = self._spin(-100, 100, " px")
        self.shadow_y = self._spin(-100, 100, " px")
        root.addWidget(QLabel("DESENFOQUE")); root.addWidget(self.shadow_blur)
        root.addWidget(QLabel("DESPLAZAMIENTO X")); root.addWidget(self.shadow_x)
        root.addWidget(QLabel("DESPLAZAMIENTO Y")); root.addWidget(self.shadow_y)
        root.addWidget(QLabel("OPACIDAD DE SOMBRA"))
        self.shadow_opacity = QSlider(Qt.Horizontal); self.shadow_opacity.setRange(0, 100)
        root.addWidget(self.shadow_opacity)

        root.addWidget(SectionTitle("Fusión"))
        self.blend_mode = QComboBox()
        self.blend_mode.addItem("Normal", "normal")
        self.blend_mode.addItem("Multiplicar", "multiply")
        self.blend_mode.addItem("Trama", "screen")
        self.blend_mode.addItem("Superponer", "overlay")
        root.addWidget(self.blend_mode)
        reset = ModernButton("Restablecer efectos", icon_name="undo")
        reset.clicked.connect(self._reset); root.addWidget(reset)
        root.addStretch()

        self._controls = [
            self.preset, self.preset_name, save_preset, copy_button, paste_button,
            self.stroke_enabled, self.stroke_width, self.stroke2_enabled, self.stroke2_width,
            self.gradient_enabled, self.gradient_angle,
            self.glow_enabled, self.glow_radius, self.glow_opacity,
            self.shadow_enabled, self.shadow_blur, self.shadow_x, self.shadow_y,
            self.shadow_opacity, self.blend_mode, *self._color_buttons.values(), reset,
        ]
        for control in (
            self.stroke_enabled, self.stroke_width, self.stroke2_enabled, self.stroke2_width,
            self.gradient_enabled, self.gradient_angle,
            self.glow_enabled, self.glow_radius, self.glow_opacity,
            self.shadow_enabled, self.shadow_blur, self.shadow_x, self.shadow_y,
            self.shadow_opacity, self.blend_mode,
        ):
            signal = (
                getattr(control, "toggled", None) or getattr(control, "valueChanged", None)
                or getattr(control, "currentIndexChanged", None)
            )
            signal.connect(self._emit)
        self.clear_layer()

    @staticmethod
    def _spin(low: int, high: int, suffix: str) -> QSpinBox:
        field = QSpinBox(); field.setRange(low, high); field.setSuffix(suffix); field.setMinimumHeight(36)
        return field

    def _color_button(self, key: str, label: str) -> ModernButton:
        button = ModernButton(label)
        button.clicked.connect(lambda: self._pick_color(key))
        self._color_buttons[key] = button
        self._refresh_color_button(key)
        return button

    def _pick_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(self._colors[key]), self, self._color_buttons[key].text())
        if color.isValid():
            self._colors[key] = color.name().upper()
            self._refresh_color_button(key)
            self._emit()

    def _refresh_color_button(self, key: str) -> None:
        button = self._color_buttons[key]
        label = {
            "stroke_color": "Trazo", "gradient_start": "Inicio",
            "gradient_end": "Final", "glow_color": "Resplandor",
            "stroke2_color": "Trazo exterior", "shadow_color": "Sombra",
        }[key]
        swatch = QPixmap(16, 16); swatch.fill(QColor(self._colors[key])); button.setIcon(QIcon(swatch))
        button.setText(f"{label}  {self._colors[key]}")

    def set_layer(self, index: int, style: dict, selected_count: int = 1) -> None:
        self._loading = True; self._layer_index = index
        normalized = TypographyManager.normalized(style)
        self.layer_label.setText(
            f"Editando {selected_count} capas" if selected_count > 1 else f"Editando capa {index + 1:02}"
        )
        self.stroke_enabled.setChecked(normalized["stroke_width"] > 0)
        self.stroke_width.setValue(max(1, normalized["stroke_width"] or 2))
        self.stroke2_enabled.setChecked(normalized["stroke2_enabled"])
        self.stroke2_width.setValue(normalized["stroke2_width"])
        self.gradient_enabled.setChecked(normalized["gradient_enabled"])
        self.gradient_angle.setValue(normalized["gradient_angle"])
        self.glow_enabled.setChecked(normalized["glow_enabled"])
        self.glow_radius.setValue(normalized["glow_radius"])
        self.glow_opacity.setValue(normalized["glow_opacity"])
        self.shadow_enabled.setChecked(normalized["shadow_enabled"])
        self.shadow_blur.setValue(normalized["shadow_blur"])
        self.shadow_x.setValue(normalized["shadow_offset_x"])
        self.shadow_y.setValue(normalized["shadow_offset_y"])
        self.shadow_opacity.setValue(normalized["shadow_opacity"])
        self.blend_mode.setCurrentIndex(max(0, self.blend_mode.findData(normalized["blend_mode"])))
        for key in self._colors:
            self._colors[key] = normalized[key]
            self._refresh_color_button(key)
        for control in self._controls: control.setEnabled(True)
        self._loading = False

    def clear_layer(self) -> None:
        self._layer_index = -1; self.layer_label.setText("Selecciona una capa de texto")
        for control in getattr(self, "_controls", []): control.setEnabled(False)

    def values(self) -> dict:
        return {
            "stroke_width": self.stroke_width.value() if self.stroke_enabled.isChecked() else 0,
            "stroke_color": self._colors["stroke_color"],
            "stroke2_enabled": self.stroke2_enabled.isChecked(),
            "stroke2_width": self.stroke2_width.value(),
            "stroke2_color": self._colors["stroke2_color"],
            "gradient_enabled": self.gradient_enabled.isChecked(),
            "gradient_start": self._colors["gradient_start"],
            "gradient_end": self._colors["gradient_end"],
            "gradient_angle": self.gradient_angle.value(),
            "glow_enabled": self.glow_enabled.isChecked(),
            "glow_color": self._colors["glow_color"],
            "glow_radius": self.glow_radius.value(),
            "glow_opacity": self.glow_opacity.value(),
            "shadow_enabled": self.shadow_enabled.isChecked(),
            "shadow_color": self._colors["shadow_color"],
            "shadow_blur": self.shadow_blur.value(),
            "shadow_offset_x": self.shadow_x.value(),
            "shadow_offset_y": self.shadow_y.value(),
            "shadow_opacity": self.shadow_opacity.value(),
            "blend_mode": str(self.blend_mode.currentData() or "normal"),
        }

    def _emit(self, *_args) -> None:
        if not self._loading and self._layer_index >= 0:
            self.preset.blockSignals(True); self.preset.setCurrentText("Personalizado"); self.preset.blockSignals(False)
            self.effects_changed.emit(self.values())

    def set_presets(self, presets: dict[str, dict]) -> None:
        current = self.preset.currentText()
        self._presets = {name: TypographyManager.normalized(style) for name, style in presets.items()}
        self.preset.blockSignals(True); self.preset.clear(); self.preset.addItem("Personalizado")
        self.preset.addItems(self._presets)
        self.preset.setCurrentText(current if current in self._presets else "Personalizado")
        self.preset.blockSignals(False)

    def _preset_selected(self, name: str) -> None:
        if self._loading or name not in self._presets:
            return
        preset = self._presets[name]
        self.set_layer(max(0, self._layer_index), preset)
        self.preset.blockSignals(True); self.preset.setCurrentText(name); self.preset.blockSignals(False)
        self.preset_apply_requested.emit(name, dict(preset))

    def _save_preset(self) -> None:
        name = self.preset_name.text().strip()
        if name:
            self.preset_save_requested.emit(name)

    def _reset(self) -> None:
        if self._layer_index < 0: return
        self._loading = True
        self.stroke_enabled.setChecked(False); self.stroke_width.setValue(2)
        self.stroke2_enabled.setChecked(False); self.stroke2_width.setValue(6)
        self.gradient_enabled.setChecked(False); self.gradient_angle.setValue(90)
        self.glow_enabled.setChecked(False); self.glow_radius.setValue(10); self.glow_opacity.setValue(65)
        self.shadow_enabled.setChecked(False); self.shadow_blur.setValue(8)
        self.shadow_x.setValue(4); self.shadow_y.setValue(6); self.shadow_opacity.setValue(65)
        self.blend_mode.setCurrentIndex(0)
        self._colors.update({
            "stroke_color": "#FFFFFF", "gradient_start": "#FFFFFF",
            "gradient_end": "#00CFE8", "glow_color": "#00CFE8",
            "stroke2_color": "#111111", "shadow_color": "#000000",
        })
        for key in self._colors: self._refresh_color_button(key)
        self._loading = False; self._emit()
