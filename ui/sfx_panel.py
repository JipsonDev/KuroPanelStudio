from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QLabel, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from core.typography_manager import TypographyManager
from ui.widgets.controls import ModernButton, SectionTitle


BUILTIN_SFX_PRESETS: dict[str, dict] = {
    "Impacto": {"sfx_curve": -18, "sfx_wave": 0, "sfx_skew": -8, "sfx_expansion": 45, "sfx_impact": 40},
    "Arco": {"sfx_curve": -55, "sfx_wave": 0, "sfx_skew": 0, "sfx_expansion": 0, "sfx_impact": 0},
    "Onda": {"sfx_curve": 0, "sfx_wave": 38, "sfx_wave_cycles": 3, "sfx_skew": 0, "sfx_expansion": 0},
    "Velocidad": {"sfx_curve": 0, "sfx_wave": 0, "sfx_skew": -28, "sfx_expansion": 30, "sfx_impact": 18},
    "Explosión": {"sfx_curve": -24, "sfx_wave": 18, "sfx_wave_cycles": 2, "sfx_skew": 0, "sfx_expansion": 70, "sfx_impact": 75},
}


class SFXPanel(QScrollArea):
    """Vector SFX controls kept separate from regular paragraph typography."""

    sfx_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(250)
        self.setMaximumWidth(330)
        self._loading = False
        self._layer_index = -1

        content = QFrame(); content.setObjectName("AIPanel"); self.setWidget(content)
        root = QVBoxLayout(content); root.setContentsMargins(10, 12, 10, 12); root.setSpacing(9)
        root.addWidget(SectionTitle("SFX y texto especial"))
        self.layer_label = QLabel("Selecciona una capa de texto")
        self.layer_label.setObjectName("Muted"); self.layer_label.setWordWrap(True)
        root.addWidget(self.layer_label)
        self.enabled = QCheckBox("Convertir esta capa en SFX vectorial")
        root.addWidget(self.enabled)
        self.auto_layout = QCheckBox("Organizar automáticamente dentro de la caja")
        self.auto_layout.setToolTip(
            "Conserva saltos manuales; si no existen, equilibra las líneas según la forma de la caja."
        )
        root.addWidget(self.auto_layout)

        root.addWidget(SectionTitle("Preset"))
        self.preset = QComboBox(); self.preset.addItem("Personalizado")
        self.preset.addItems(BUILTIN_SFX_PRESETS)
        root.addWidget(self.preset)

        root.addWidget(SectionTitle("Trayectoria"))
        self.bezier = QCheckBox("Curva Bézier editable en el lienzo")
        self.bezier.setToolTip("Muestra dos controles morados para modificar el recorrido y su tangente.")
        root.addWidget(self.bezier)
        self.curve = self._field(root, "ARCO / CURVA", -100, 100, " %")
        self.wave = self._field(root, "ONDA", 0, 100, " %")
        self.wave_cycles = self._field(root, "CICLOS", 1, 8, "")
        self.skew = self._field(root, "INCLINACIÓN", -60, 60, "°")
        self.expansion = self._field(root, "EXPANSIÓN", -80, 150, " %")
        self.impact = self._field(root, "IMPACTO / ROTACIÓN", -100, 100, " %")

        root.addWidget(SectionTitle("Perspectiva por nodos"))
        hint = QLabel("Arrastra libremente las cuatro esquinas directamente sobre el SFX en el lienzo. Los valores permanecen editables aquí.")
        hint.setObjectName("Muted"); hint.setWordWrap(True); root.addWidget(hint)
        self.mesh = QCheckBox("Activar malla de deformación 3 × 3")
        self.mesh.setToolTip("Añade cinco nodos naranjas para deformar zonas concretas sin bloquear ancho ni alto.")
        root.addWidget(self.mesh)
        # Legacy numeric offsets remain internal for project compatibility;
        # perspective is edited freely from the four canvas corners.
        self.node_tl = QSpinBox(); self.node_tr = QSpinBox()
        self.node_bl = QSpinBox(); self.node_br = QSpinBox()
        for field in (self.node_tl, self.node_tr, self.node_bl, self.node_br):
            field.setRange(-100, 100); field.hide()
        reset_perspective = ModernButton("Restablecer perspectiva")
        reset_perspective.clicked.connect(self._reset_perspective)
        root.addWidget(reset_perspective)
        self.shape = QCheckBox("Conservar como forma editable")
        root.addWidget(self.shape)
        convert = ModernButton("Convertir texto a forma", "Primary")
        convert.setToolTip("Activa SFX vectorial; el texto sigue siendo editable desde la capa")
        convert.clicked.connect(self._convert)
        root.addWidget(convert)
        root.addStretch()

        self._controls = [
            self.enabled, self.auto_layout, self.preset, self.curve, self.wave, self.wave_cycles,
            self.skew, self.expansion, self.impact, self.node_tl, self.node_tr,
            self.node_bl, self.node_br, self.bezier, self.mesh,
            self.shape, reset_perspective, convert,
        ]
        for widget in self._controls:
            widget.setEnabled(False)
        self.preset.currentTextChanged.connect(self._preset_changed)
        for widget in self._controls:
            if widget in (self.preset, reset_perspective, convert):
                continue
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "toggled", None)
            signal.connect(self._emit)

    @staticmethod
    def _field(layout: QVBoxLayout, label: str, low: int, high: int, suffix: str) -> QSpinBox:
        layout.addWidget(QLabel(label))
        field = QSpinBox(); field.setRange(low, high); field.setSuffix(suffix); field.setMinimumHeight(36)
        layout.addWidget(field)
        return field

    def set_layer(self, index: int, style: dict, selection_count: int = 1) -> None:
        self._loading = True; self._layer_index = index
        normalized = TypographyManager.normalized(style)
        self.layer_label.setText(
            f"Editando {selection_count} capas" if selection_count > 1 else f"Editando capa {index + 1:02}"
        )
        for widget in self._controls:
            widget.setEnabled(True)
        self.enabled.setChecked(normalized["sfx_enabled"])
        self.auto_layout.setChecked(normalized["sfx_auto_layout"])
        self.bezier.setChecked(normalized["sfx_bezier_enabled"])
        self.mesh.setChecked(normalized["sfx_mesh_enabled"])
        self.preset.setCurrentText(normalized["sfx_preset"] if normalized["sfx_preset"] in BUILTIN_SFX_PRESETS else "Personalizado")
        for field, key in (
            (self.curve, "sfx_curve"), (self.wave, "sfx_wave"), (self.wave_cycles, "sfx_wave_cycles"),
            (self.skew, "sfx_skew"), (self.expansion, "sfx_expansion"), (self.impact, "sfx_impact"),
            (self.node_tl, "sfx_node_tl"), (self.node_tr, "sfx_node_tr"),
            (self.node_bl, "sfx_node_bl"), (self.node_br, "sfx_node_br"),
        ):
            field.setValue(normalized[key])
        self.shape.setChecked(normalized["sfx_shape"])
        self._loading = False

    def clear_layer(self) -> None:
        self._layer_index = -1; self.layer_label.setText("Selecciona una capa de texto")
        for widget in self._controls:
            widget.setEnabled(False)

    def values(self) -> dict:
        return {
            "sfx_enabled": self.enabled.isChecked(), "sfx_auto_layout": self.auto_layout.isChecked(),
            "sfx_preset": self.preset.currentText(),
            "sfx_curve": self.curve.value(), "sfx_wave": self.wave.value(),
            "sfx_wave_cycles": self.wave_cycles.value(), "sfx_skew": self.skew.value(),
            "sfx_expansion": self.expansion.value(), "sfx_impact": self.impact.value(),
            "sfx_bezier_enabled": self.bezier.isChecked(),
            "sfx_mesh_enabled": self.mesh.isChecked(),
            "sfx_node_tl": self.node_tl.value(), "sfx_node_tr": self.node_tr.value(),
            "sfx_node_bl": self.node_bl.value(), "sfx_node_br": self.node_br.value(),
            "sfx_shape": self.shape.isChecked(),
        }

    def _preset_changed(self, name: str) -> None:
        if self._loading or name not in BUILTIN_SFX_PRESETS:
            return
        self._loading = True
        values = {**self.values(), **BUILTIN_SFX_PRESETS[name], "sfx_enabled": True, "sfx_preset": name}
        self.enabled.setChecked(True)
        for field, key in (
            (self.curve, "sfx_curve"), (self.wave, "sfx_wave"), (self.wave_cycles, "sfx_wave_cycles"),
            (self.skew, "sfx_skew"), (self.expansion, "sfx_expansion"), (self.impact, "sfx_impact"),
        ):
            field.setValue(int(values.get(key, 0)))
        self._loading = False; self.sfx_changed.emit(self.values())

    def _convert(self) -> None:
        self.enabled.setChecked(True); self.shape.setChecked(True); self._emit()

    def _reset_perspective(self) -> None:
        if self._layer_index < 0:
            return
        self.sfx_changed.emit({
            **self.values(), "sfx_quad_version": 1,
            "sfx_quad_tl_x": 0, "sfx_quad_tl_y": 0,
            "sfx_quad_tr_x": 100, "sfx_quad_tr_y": 0,
            "sfx_quad_bl_x": 0, "sfx_quad_bl_y": 100,
            "sfx_quad_br_x": 100, "sfx_quad_br_y": 100,
            "sfx_bezier_c1_x": 33, "sfx_bezier_c1_y": 0,
            "sfx_bezier_c2_x": 67, "sfx_bezier_c2_y": 0,
            **{
                f"sfx_mesh_r{row}c{column}_{axis}": 0
                for row, column in ((0, 1), (1, 0), (1, 1), (1, 2), (2, 1))
                for axis in ("dx", "dy")
            },
        })

    def _emit(self, *_args) -> None:
        if not self._loading and self._layer_index >= 0:
            if self.sender() is not self.preset:
                self.preset.blockSignals(True); self.preset.setCurrentText("Personalizado"); self.preset.blockSignals(False)
            self.sfx_changed.emit(self.values())
