from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QTabWidget,
                               QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from ui.profile_settings import ProfileSettingsWidget
from core.performance_manager import resource_policy
from core.ocr_manager import OCRManager, OCRConfigurationError, ALIBABA_COMPATIBLE_URL


class SettingsDialog(QDialog):
    PROVIDERS = ("Alibaba Cloud", "Gemini", "OpenAI", "DeepSeek", "DeepL")

    def __init__(self, settings: dict, credentials, profile_manager=None, profile_locked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuración · Proveedores y seguridad")
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.setElideMode(Qt.ElideRight)
        tabs.tabBar().setUsesScrollButtons(True)
        layout.addWidget(tabs)

        interface = QWidget(); interface_form = QFormLayout(interface)
        self.ui_language = QComboBox()
        self.ui_language.setProperty("kuro_i18n_choices", True)
        self.ui_language.addItem("Español", "es")
        self.ui_language.addItem("English", "en")
        language_index = self.ui_language.findData(
            settings.get("general", {}).get("ui_language", "es")
        )
        self.ui_language.setCurrentIndex(max(0, language_index))
        interface_form.addRow("Idioma de la interfaz", self.ui_language)
        interface_hint = QLabel("Cambia los menús y controles de KuroPanel Studio. No modifica los idiomas de OCR ni de traducción.")
        interface_hint.setWordWrap(True); interface_hint.setObjectName("Muted")
        interface_form.addRow(interface_hint)
        self.auto_check_updates = QCheckBox("Buscar actualizaciones automáticamente al iniciar")
        self.auto_check_updates.setChecked(bool(settings.get("general", {}).get("auto_check_updates", True)))
        interface_form.addRow(self.auto_check_updates)
        tabs.addTab(interface, "Interfaz")

        providers = QWidget(); form = QFormLayout(providers)
        self.key_fields: dict[str, QLineEdit] = {}
        for name in self.PROVIDERS:
            field = QLineEdit()
            field.setEchoMode(QLineEdit.Password)
            stored_key = credentials.get(name)
            field.setText(stored_key)
            field.setPlaceholderText("Sin configurar")
            field.setClearButtonEnabled(True)
            field.setToolTip(
                "Clave guardada y cifrada para este usuario" if stored_key
                else "Pega aquí la clave del proveedor"
            )
            form.addRow(f"{name} API key", field)
            self.key_fields[name] = field
        self.show_keys = QCheckBox("Mostrar claves")
        self.show_keys.toggled.connect(self._set_keys_visible)
        form.addRow("", self.show_keys)
        note = QLabel(
            "Los campos con puntos ya están guardados. Al pulsar Guardar se cifran con Windows "
            "DPAPI; si vacías un campo, esa clave se elimina."
        )
        note.setWordWrap(True); note.setObjectName("Muted"); form.addRow(note)
        tabs.addTab(providers, "Credenciales")

        workflow = QWidget(); workflow_form = QFormLayout(workflow)
        self.ocr_model = QLineEdit(settings.get("ocr", {}).get("model", "qwen-vl-ocr"))
        self.ocr_base_url = QLineEdit(settings.get("ocr", {}).get("base_url", ""))
        self.ocr_base_url.setPlaceholderText(ALIBABA_COMPATIBLE_URL.removesuffix("/chat/completions"))
        workflow_form.addRow("Modelo OCR", self.ocr_model)
        workflow_form.addRow("URL de Alibaba OCR", self.ocr_base_url)
        ocr_hint = QLabel(
            "Copia la URL base de tu región y espacio de trabajo desde Model Studio. "
            "La clave y el modelo deben estar disponibles allí. Vacío usa el servidor internacional."
        )
        ocr_hint.setWordWrap(True)
        ocr_hint.setObjectName("Muted")
        workflow_form.addRow(ocr_hint)
        self.translation_provider = QComboBox(); self.translation_provider.addItems(list(self.PROVIDERS))
        self.translation_provider.setCurrentText(settings["translate"].get("platform", "Gemini"))
        self.translation_model = QLineEdit(settings["translate"].get("model", "gemini-2.5-flash"))
        self.source_language = QLineEdit(settings["translate"].get("source_language", "ZH"))
        self.target_language = QLineEdit(settings["translate"].get("target_language", "ES"))
        workflow_form.addRow("Proveedor de traducción", self.translation_provider)
        workflow_form.addRow("Modelo", self.translation_model)
        workflow_form.addRow("Idioma origen", self.source_language)
        workflow_form.addRow("Idioma destino", self.target_language)
        tabs.addTab(workflow, "OCR y traducción")

        recovery = QWidget(); recovery_form = QFormLayout(recovery)
        self.autosave_interval = QComboBox(); self.autosave_interval.addItems(["1", "2", "5", "10"])
        self.autosave_interval.setCurrentText(str(settings.get("general", {}).get("autosave_minutes", 2)))
        recovery_form.addRow("Autoguardado (minutos)", self.autosave_interval)
        self.psd_auto_sync = QCheckBox("Recargar PSD automáticamente al guardarlo en Photoshop")
        self.psd_auto_sync.setChecked(bool(settings.get("general", {}).get("psd_auto_sync", True)))
        self.psd_auto_sync.setToolTip(
            "Conserva el zoom, las cajas y los ajustes de capa al detectar Ctrl+S en Photoshop"
        )
        recovery_form.addRow("Sincronización PSD", self.psd_auto_sync)
        recovery_form.addRow(QLabel("Las limpiezas permanecen como parches no destructivos; nunca se sobrescriben los originales."))
        tabs.addTab(recovery, "Recuperación")

        performance = QWidget(); performance_form = QFormLayout(performance)
        performance_values = settings.get("performance", {})
        self.resource_profile = QComboBox()
        self.resource_profile.setProperty("kuro_i18n_choices", True)
        self.resource_profile.addItem("Automático según el equipo", "auto")
        self.resource_profile.addItem("Bajo consumo", "low")
        self.resource_profile.addItem("Equilibrado", "balanced")
        self.resource_profile.addItem("Máximo rendimiento", "high")
        profile_index = self.resource_profile.findData(str(performance_values.get("resource_profile", "auto")))
        self.resource_profile.setCurrentIndex(max(0, profile_index))
        self.device_mode = QComboBox()
        self.device_mode.setProperty("kuro_i18n_choices", True)
        self.device_mode.addItem("Automático (recomendado)", "auto")
        self.device_mode.addItem("GPU", "gpu")
        self.device_mode.addItem("CPU", "cpu")
        mode_index = self.device_mode.findData(str(performance_values.get("device_mode", "auto")))
        self.device_mode.setCurrentIndex(max(0, mode_index))
        self.gpu_idle_minutes = QSpinBox(); self.gpu_idle_minutes.setRange(0, 60); self.gpu_idle_minutes.setSuffix(" min")
        self.gpu_idle_minutes.setSpecialValueText("No descargar")
        self.gpu_idle_minutes.setValue(int(performance_values.get("gpu_idle_minutes", 5)))
        self.image_cache_mb = QSpinBox(); self.image_cache_mb.setRange(64, 2048); self.image_cache_mb.setSuffix(" MB")
        self.image_cache_mb.setValue(int(performance_values.get("image_cache_mb", 384)))
        performance_form.addRow("Perfil de recursos", self.resource_profile)
        performance_form.addRow("Procesador de modelos", self.device_mode)
        performance_form.addRow("Liberar GPU sin uso", self.gpu_idle_minutes)
        performance_form.addRow("Caché de imágenes", self.image_cache_mb)
        self.profile_summary = QLabel()
        self.profile_summary.setWordWrap(True); self.profile_summary.setObjectName("Muted")
        performance_form.addRow("Comportamiento", self.profile_summary)
        performance_note = QLabel(
            "Automático usa CUDA cuando está disponible. Los lotes se ajustan a la VRAM libre y "
            "los modelos inactivos se descargan sin borrar OCR ni máscaras."
        )
        performance_note.setWordWrap(True); performance_note.setObjectName("Muted")
        performance_form.addRow(performance_note)
        detected = resource_policy("auto", int(performance_values.get("image_cache_mb", 384)))
        detected_note = QLabel(
            f"Detectado: {detected.ram_mb / 1024:.1f} GB RAM · {detected.physical_cores} núcleos físicos · "
            f"perfil recomendado {detected.name}."
        )
        detected_note.setWordWrap(True); detected_note.setObjectName("Muted")
        performance_form.addRow(detected_note)
        self.resource_profile.currentIndexChanged.connect(self._resource_profile_changed)
        self._update_resource_profile_summary(adjust_cache=False)
        tabs.addTab(performance, "Rendimiento")

        self.profile_settings = None
        if profile_manager is not None:
            self.profile_settings = ProfileSettingsWidget(
                profile_manager,
                settings.get("profiles", {}).get("active_type", ""),
                settings.get("profiles", {}).get("active_project", ""),
                profile_locked,
                self,
            )
            profile_scroll = QScrollArea(); profile_scroll.setObjectName("SettingsScroll")
            profile_scroll.setWidgetResizable(True); profile_scroll.setWidget(self.profile_settings)
            tabs.insertTab(1, profile_scroll, "Perfiles de fuentes")
            tabs.setCurrentIndex(0)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_keys_visible(self, visible: bool) -> None:
        mode = QLineEdit.Normal if visible else QLineEdit.Password
        for field in self.key_fields.values():
            field.setEchoMode(mode)

    def _resource_profile_changed(self, _index: int) -> None:
        self._update_resource_profile_summary(adjust_cache=True)

    def _update_resource_profile_summary(self, *, adjust_cache: bool) -> None:
        requested = str(self.resource_profile.currentData() or "auto")
        defaults = {"low": 128, "balanced": 384, "high": 1024}
        if adjust_cache:
            if requested == "auto":
                detected = resource_policy("auto", 2048)
                self.image_cache_mb.setValue(defaults.get(detected.name, 384))
            else:
                self.image_cache_mb.setValue(defaults[requested])
        policy = resource_policy(requested, self.image_cache_mb.value())
        descriptions = {
            "low": "1 página en RAM · texto solo en el área visible · sin precarga · 1 tarea de modelo.",
            "balanced": "Página actual y vecinas · composición visible progresiva · 2 tareas de E/S.",
            "high": "Hasta 2 páginas vecinas por lado · cachés amplias · precarga y mayor paralelismo.",
        }
        self.profile_summary.setText(descriptions[policy.name])

    def values(self) -> dict:
        values = {
            "ocr_model": self.ocr_model.text().strip(),
            "ocr_base_url": self.ocr_base_url.text().strip(),
            "keys": {name: field.text().strip() for name, field in self.key_fields.items()},
            "provider": self.translation_provider.currentText(),
            "model": self.translation_model.text().strip(),
            "source_language": self.source_language.text().strip(),
            "target_language": self.target_language.text().strip(),
            "autosave_minutes": int(self.autosave_interval.currentText()),
            "ui_language": self.ui_language.currentData(),
            "auto_check_updates": self.auto_check_updates.isChecked(),
            "psd_auto_sync": self.psd_auto_sync.isChecked(),
            "resource_profile": self.resource_profile.currentData(),
            "device_mode": self.device_mode.currentData(),
            "gpu_idle_minutes": self.gpu_idle_minutes.value(),
            "image_cache_mb": self.image_cache_mb.value(),
        }
        if self.profile_settings is not None:
            values["active_type"], values["active_project"] = self.profile_settings.values()
        return values

    def accept(self) -> None:
        try:
            OCRManager.normalize_endpoint(self.ocr_base_url.text())
            if not self.ocr_model.text().strip():
                raise OCRConfigurationError("Escribe el identificador del modelo OCR.")
        except (OCRConfigurationError, ValueError) as error:
            QMessageBox.warning(self, "Configuración OCR", str(error))
            return
        super().accept()
