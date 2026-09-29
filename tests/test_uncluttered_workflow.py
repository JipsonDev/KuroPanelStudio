"""Primary actions remain usable while secondary controls are disclosed."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QLabel
from ui.ai_panel import AIOptionsPanel


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    panel = AIOptionsPanel()
    panel.resize(310, 650)
    panel.show()
    app.processEvents()
    yield panel
    panel.close()


def test_ocr_keeps_main_action_visible_and_usage_in_provider_card(panel):
    assert not panel.ocr_settings.is_expanded()
    assert not panel.ocr_model.isVisible()
    assert panel._primary_buttons["ocr"].isVisible()
    assert not panel.ocr_usage.isVisible()
    panel.ocr_settings.header.click()
    assert panel.ocr_model.isVisible()
    assert panel.ocr_usage.isVisible()
    panel.ocr_model.setCurrentText("custom-model")
    panel.ocr_settings.header.click()
    panel.mode_buttons["clean"].setChecked(True)
    panel.mode_buttons["ocr"].setChecked(True)
    assert panel.ocr_configuration()[1] == "custom-model"


def test_process_modes_are_visible_and_card_text_is_separated_from_icon(panel):
    assert all(button.isVisible() for button in panel.mode_buttons.values())
    card = panel.ocr_settings
    glyph = card.findChild(QLabel, "ActionCardIcon")
    caption = card.findChild(QLabel, "ActionCardSubtitle")
    assert card.header.geometry().left() - glyph.geometry().right() >= 8
    assert caption.geometry().left() == card.header.geometry().left()
    panel.mode_buttons["translation"].click()
    assert panel.current_mode == "translation"
    assert panel.translate_provider.isVisible()
    assert panel._primary_buttons["translation"].isVisible()
    assert not panel.translation_settings.is_expanded()
    assert panel.translation_settings.findChild(QLabel, "ActionCardSubtitle").isVisible()
    panel.mode_buttons["clean"].click()
    assert panel.current_mode == "clean"
    assert panel.clean_provider.isVisible()
    assert panel._primary_buttons["clean"].isVisible()
    assert not panel.clean_settings.is_expanded()
    assert panel.retouch_section.findChild(QLabel, "ActionCardSubtitle").isVisible()


def test_cleaning_shows_review_actions_only_when_mask_is_ready(panel):
    panel.mode_buttons["clean"].setChecked(True)
    assert not panel.apply_mask_button.isVisible()
    assert not panel.cancel_mask_button.isVisible()
    assert not panel.retouch_section.is_expanded()
    panel.set_mask_preview_active(True)
    assert panel.apply_mask_button.isVisible()
    emitted = []
    panel.action_requested.connect(emitted.append)
    panel.apply_mask_button.click()
    assert emitted == ["apply_clean_mask"]
    panel.set_mask_preview_active(False)
    assert not panel.apply_mask_button.isVisible()


def test_manual_tools_can_be_revealed_by_shortcut_sync(panel):
    panel.mode_buttons["clean"].setChecked(True)
    panel.sync_brush_mode("clone")
    assert panel.retouch_section.is_expanded()
    assert panel.clone_button.isChecked()
    assert panel.clone_button.isVisible()


def test_chapter_action_describes_automatic_cleaning(panel):
    panel.mode_buttons["clean"].setChecked(True)
    panel.clean_scope.setCurrentIndex(2)
    assert panel._primary_buttons["clean"].text() == "Limpiar capítulo"
    panel.clean_scope.setCurrentIndex(1)
    assert panel._primary_buttons["clean"].text() == "Preparar limpieza"


def test_scope_and_primary_action_stay_visible_in_short_panels(panel):
    app = QApplication.instance()
    panel.resize(300, 370)
    app.processEvents()
    for mode in ("ocr", "translation", "clean"):
        panel.mode_buttons[mode].setChecked(True)
        app.processEvents()
        button = panel._primary_buttons[mode]
        scope = getattr(panel, f"{mode}_scope")
        assert button.isVisibleTo(panel)
        assert scope.isVisibleTo(panel)
        assert button.mapTo(panel, button.rect().bottomLeft()).y() < panel.height()
        assert panel.action_footer.geometry().top() >= panel.viewport().geometry().bottom()
