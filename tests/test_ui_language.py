"""The interface locale must never alter document or provider settings."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from core.image_manager import ImageManager
from core.project_manager import Page
from core.settings_manager import SettingsManager
from ui.i18n import UiTranslator, translate_text
from ui.images_panel import ImagesPanel
from ui.settings_dialog import SettingsDialog
from ui.text_panel import TextOptionsPanel


class EmptyCredentials:
    def get(self, _provider):
        return ""


def _translator(app):
    if not hasattr(app, "_kuro_ui_translator"):
        app._kuro_ui_translator = UiTranslator(app)
    return app._kuro_ui_translator


def test_language_setting_persists_without_changing_translation_languages(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    assert manager.data["general"]["ui_language"] == "es"
    manager.update_workflow({
        "provider": "Gemini", "model": "gemini-2.5-flash",
        "source_language": "ZH", "target_language": "ES",
        "autosave_minutes": 2, "ui_language": "en", "auto_check_updates": False,
    })
    restored = SettingsManager(manager.path)
    assert restored.data["general"]["ui_language"] == "en"
    assert restored.data["general"]["auto_check_updates"] is False
    assert restored.data["translate"]["target_language"] == "ES"
    assert translate_text("●  Configura Gemini y su modelo", "en") == "●  Configure Gemini and its model"


def test_runtime_language_switch_keeps_stable_filter_ids_and_user_text():
    app = QApplication.instance() or QApplication([])
    translator = _translator(app)
    root = QWidget()
    layout = QVBoxLayout(root)
    label = QLabel("Capítulo cargado")
    layout.addWidget(label)
    document_label = QLabel("Capítulo cargado")
    document_label.setProperty("kuro_i18n_ignore", True)
    layout.addWidget(document_label)
    field = QLineEdit("Páginas")
    field.setPlaceholderText("Buscar página…")
    layout.addWidget(field)
    choices = QComboBox()
    choices.setProperty("kuro_i18n_choices", True)
    choices.addItem("Pendiente traducción", "pending_translation")
    choices.addItem("Todas", "all")
    layout.addWidget(choices)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Herramientas")
    layout.addWidget(tabs)
    try:
        translator.set_language("en")
        translator.refresh(root)
        assert label.text() == "Chapter loaded"
        assert document_label.text() == "Capítulo cargado"
        assert field.text() == "Páginas"  # user content is not UI copy
        assert field.placeholderText() == "Search pages…"
        assert choices.itemText(0) == "Translation pending"
        assert choices.itemData(0) == "pending_translation"
        assert tabs.tabText(0) == "Tools"
        label.setText("Páginas (12)")
        root.show()
        app.processEvents()
        assert label.text() == "Pages (12)"
        translator.set_language("es")
        translator.refresh(root)
        assert label.text() == "Páginas (12)"
        assert choices.itemText(0) == "Pendiente traducción"
        assert tabs.tabText(0) == "Herramientas"
    finally:
        translator.set_language("es")
        root.close()


def test_text_tools_keep_dynamic_guidance_translated():
    app = QApplication.instance() or QApplication([])
    translator = _translator(app)
    panel = TextOptionsPanel()
    try:
        panel.set_layer(0, {"balloon_fit": True})
        translator.set_language("en")
        translator.refresh(panel)
        assert panel.composition_section.header.text() == "Balloon fit"
        assert panel.btn_dialogue.text() == "Dialogue"
        assert panel.layout_hint.text() == "Text is centered with clearance from the detected outline."
        panel.balloon_fit.setChecked(False)
        panel.show()
        app.processEvents()
        assert panel.layout_hint.text() == "Text uses the rectangular box and its inner margin."
    finally:
        translator.set_language("es")
        panel.close()


def test_settings_exposes_interface_language_separately_from_ocr(tmp_path):
    app = QApplication.instance() or QApplication([])
    translator = _translator(app)
    manager = SettingsManager(tmp_path / "settings.json")
    manager.data["general"]["ui_language"] = "en"
    dialog = SettingsDialog(manager.data, EmptyCredentials())
    try:
        translator.set_language("en")
        translator.refresh(dialog)
        assert dialog.windowTitle() == "Settings · Providers and security"
        assert dialog.ui_language.currentData() == "en"
        assert dialog.ui_language.itemText(0) == "Español"
        assert dialog.values()["target_language"] == "ES"
        assert dialog.values()["ui_language"] == "en"
        assert dialog.findChild(QTabWidget).tabText(0) == "Interface"
    finally:
        translator.set_language("es")
        dialog.close()


def test_english_page_filter_keeps_workflow_behavior(tmp_path):
    app = QApplication.instance() or QApplication([])
    translator = _translator(app)
    pages = []
    for number in range(2):
        path = tmp_path / f"page_{number}.png"
        Image.new("RGB", (20, 30)).save(path)
        pages.append(Page(path.name, path, 20, 30))
    panel = ImagesPanel(pages, ImageManager())
    try:
        translator.set_language("en")
        translator.refresh(panel)
        panel.set_statuses([
            {"detected": True, "ocr": True, "translated": False},
            {"detected": True, "ocr": True, "translated": True},
        ])
        panel.filter.setCurrentIndex(3)
        assert panel.filter.currentText() == "Translation pending"
        assert panel.filter.currentData() == "Pendiente traducción"
        assert panel.list.item(0).isHidden() is False
        assert panel.list.item(1).isHidden() is True
    finally:
        translator.set_language("es")
        panel.close()


def test_main_window_reads_saved_english_language(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = SettingsManager(tmp_path / "ManhuaSuiteEditor" / "settings.json")
    settings.data["general"]["ui_language"] = "en"
    settings.save()
    from ui.main_window import MainWindow

    window = MainWindow()
    try:
        assert window.topbar.subtitle.text() == "Manhua editing workspace"
        assert window.sidebar.buttons[1].tool_name == "Páginas"
        assert window.sidebar.buttons[1].text() == "Pages"
        assert window.images_panel.filter.itemText(3) == "Translation pending"
        assert window.images_panel.filter.itemData(3) == "Pendiente traducción"
        assert window.ai_panel.ocr_scope.itemText(1) == "Current page"
        assert window.ai_panel.ocr_scope.itemData(1) == 1
    finally:
        window.ui_translator.set_language("es")
        window.close()


def test_settings_switches_language_live_and_saves_it(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from ui.main_window import MainWindow

    window = MainWindow()

    def choose(language):
        dialog = next(widget for widget in app.topLevelWidgets()
                      if isinstance(widget, SettingsDialog)
                      and widget.parent() is window and widget.isVisible())
        dialog.ui_language.setCurrentIndex(dialog.ui_language.findData(language))
        dialog.accept()

    try:
        for language, expected in (("en", "Pages"), ("es", "Páginas")):
            QTimer.singleShot(0, lambda value=language: choose(value))
            window.open_settings()
            assert window.sidebar.buttons[1].text() == expected
            assert window.settings.data["general"]["ui_language"] == language
            assert SettingsManager(window.settings.path).data["general"]["ui_language"] == language
            assert window.settings.data["translate"]["target_language"] == "ES"
    finally:
        window.ui_translator.set_language("es")
        window.close()
