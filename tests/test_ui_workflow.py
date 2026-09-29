from __future__ import annotations

import copy
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGraphicsDropShadowEffect, QGraphicsItem,
    QGraphicsPixmapItem, QLineEdit, QPushButton, QWidget,
)

from ui.canvas_view import CanvasView
from ui.main_window import MainWindow
from ui.effects_panel import TextEffectsPanel
from ui.ai_panel import AIOptionsPanel
from ui.layers_panel import LayersPanel
from ui.project_selection_dialog import ProjectSelectionDialog
from ui.settings_dialog import SettingsDialog
from ui.profile_settings import ProfileSettingsWidget
from ui.font_utils import (
    FontFamilyModel, commit_font_combo_text, configure_searchable_font_combo,
    select_font_family,
)
from ui.text_panel import TextOptionsPanel
from ui.sfx_panel import SFXPanel
from ui.widgets.controls import ToastNotification
from core.credential_store import CredentialStore
from core.font_profile_manager import FontProfileManager
from core.settings_manager import DEFAULT_SETTINGS
from core.stroke_engine import StrokeEngine
from core.typography_manager import TypographyManager
from core.linguistic_composer import font_supports_text


class UIWorkflowTests(unittest.TestCase):
    def test_completed_ocr_promotes_empty_overlay_fields_immediately(self) -> None:
        region = {"text": "原文", "translation": "", "applied_text": ""}

        MainWindow._promote_ocr_overlay(region)

        self.assertEqual(region["translation"], "原文")
        self.assertEqual(region["applied_text"], "原文")
        self.assertTrue(region["ocr_completed"])

    def test_ocr_request_tokens_keep_duplicate_layer_ids_separate(self) -> None:
        upper = {"id": "region-1", "text": "", "translation": "", "applied_text": ""}
        lower = {"id": "region-1", "text": "", "translation": "", "applied_text": ""}
        targets = {"upper-token": upper, "lower-token": lower}
        results = [
            {"id": "region-1", "_ocr_request_token": "lower-token", "text": "ABAJO"},
            {"id": "region-1", "_ocr_request_token": "upper-token", "text": "ARRIBA"},
        ]

        MainWindow._merge_ocr_results(None, results, targets)

        self.assertEqual(upper["text"], "ARRIBA")
        self.assertEqual(upper["applied_text"], "ARRIBA")
        self.assertEqual(lower["text"], "ABAJO")
        self.assertEqual(lower["applied_text"], "ABAJO")
        self.assertNotIn("_ocr_request_token", upper)
        self.assertNotIn("_ocr_request_token", lower)

    def test_page_ocr_does_not_force_paid_rereading(self) -> None:
        calls = []

        class WindowStub:
            regions = [{"id": "56", "text": "OCR anterior"}]

            @staticmethod
            def _run_ocr_for_regions(regions, force=False):
                calls.append((regions, force))

        MainWindow.run_ocr_api(WindowStub())
        self.assertEqual(calls, [(WindowStub.regions, False)])

    def test_moved_box_marks_its_ocr_crop_as_stale(self) -> None:
        previous = [{
            "id": "56", "x": 10, "y": 20, "width": 120, "height": 50,
            "text": "黑！真他妈黑！",
        }]
        moved = [dict(previous[0], y=220)]
        unchanged = [dict(previous[0], style={"font_size": 30})]

        self.assertEqual(MainWindow._mark_moved_regions_ocr_stale(previous, moved), 1)
        self.assertTrue(moved[0]["ocr_stale"])
        self.assertEqual(MainWindow._mark_moved_regions_ocr_stale(previous, unchanged), 0)
        self.assertNotIn("ocr_stale", unchanged[0])

    def test_canvas_repairs_duplicate_persistent_region_ids(self) -> None:
        canvas = CanvasView()
        regions = [
            {"id": "duplicate", "x": 10, "y": 10, "width": 80, "height": 40},
            {"id": "duplicate", "x": 10, "y": 70, "width": 80, "height": 40},
        ]

        canvas.set_regions(regions)

        self.assertEqual(regions[0]["id"], "duplicate")
        self.assertNotEqual(regions[1]["id"], "duplicate")
        self.assertEqual(len({region["id"] for region in regions}), 2)

    def test_existing_project_transport_markers_are_repaired_on_load(self) -> None:
        regions = [{
            "text": "MSE_BOX_002\r\nTexto original",
            "translation": "<<<MSE_BOX_0002>>>\r\nTexto traducido",
            "applied_text": "MSE-BOX-2\r\nTexto traducido",
            "translation_completed": True,
        }]

        changed = MainWindow._sanitize_transport_markers(regions)

        self.assertTrue(changed)
        self.assertEqual(regions[0]["text"], "Texto original")
        self.assertEqual(regions[0]["translation"], "Texto traducido")
        self.assertEqual(regions[0]["applied_text"], "Texto traducido")

    def test_batched_text_prioritizes_the_visible_webtoon_area(self) -> None:
        canvas = CanvasView()
        canvas.resize(500, 500)
        image = QImage(400, 4000, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        canvas.set_zoom(100)
        canvas.centerOn(200, 3750)
        regions = [
            {"id": str(index), "x": 30, "y": 30 + index * 90, "width": 300, "height": 70}
            for index in range(7)
        ]
        regions.append({"id": "visible", "x": 30, "y": 3700, "width": 300, "height": 100})
        texts = [f"Texto {index}" for index in range(7)] + ["TRADUCCIÓN VISIBLE"]
        canvas._text_batch_size = 2
        canvas.set_regions(regions)

        canvas.add_text_batched(texts, regions)

        self.assertEqual(canvas._text_values[-1], "TRADUCCIÓN VISIBLE")
        self.assertIn("TRADU", canvas._text_items[-1].toPlainText())
        self.assertTrue(any(not item.toPlainText() for item in canvas._text_items[:-1]))

    def test_batched_text_leaves_offscreen_layers_deferred(self) -> None:
        canvas = CanvasView()
        canvas.resize(500, 500)
        image = QImage(400, 6000, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        canvas.set_zoom(100)
        canvas.centerOn(200, 250)
        regions = [
            {"id": "top", "x": 20, "y": 40, "width": 340, "height": 100},
            {"id": "far", "x": 20, "y": 5600, "width": 340, "height": 100},
        ]
        canvas.set_regions(regions)

        canvas.add_text_batched(["VISIBLE", "FUERA DE PANTALLA"], regions)

        self.assertIn("VISIBLE", canvas._text_items[0].toPlainText())
        self.assertFalse(canvas._text_items[1].toPlainText())
        self.assertIn(1, canvas._deferred_text_indices)

    def test_manga_reading_direction_is_shared_by_ocr_and_translation(self) -> None:
        panel = AIOptionsPanel()
        ocr_toggle = panel._style_widgets["ocr"]["manga"]
        translation_toggle = panel._style_widgets["translation"]["manga"]

        ocr_toggle.setChecked(True, emit=True)

        self.assertTrue(ocr_toggle.isChecked())
        self.assertTrue(translation_toggle.isChecked())
        self.assertTrue(panel.style_options("ocr")["manga_mode"])
        self.assertTrue(panel.style_options("translation")["manga_mode"])

    def test_canvas_numbers_manga_boxes_right_to_left(self) -> None:
        canvas = CanvasView()
        regions = [
            {"id": "left", "x": 20, "y": 20, "width": 80, "height": 40},
            {"id": "right", "x": 240, "y": 20, "width": 80, "height": 40},
            {"id": "bottom", "x": 30, "y": 150, "width": 80, "height": 40},
        ]

        canvas.set_manga_mode(True)
        canvas.set_regions(regions)
        self.assertEqual(
            [item.region["number"] for item in canvas._regions],
            [2, 1, 3],
        )

        canvas.set_manga_mode(False)
        self.assertEqual(
            [item.region["number"] for item in canvas._regions],
            [1, 2, 3],
        )

    def test_qimage_numpy_conversion_owns_its_pixel_buffer(self) -> None:
        image = QImage(640, 110, QImage.Format_RGB888)
        image.fill(255)

        pixels = CanvasView._qimage_rgb_array(image)

        self.assertTrue(pixels.flags.owndata)
        self.assertTrue(pixels.flags.c_contiguous)
        self.assertEqual(pixels.shape, (110, 640, 3))

    def test_translation_text_preview_updates_canvas_immediately(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {"id": "dialogue", "x": 40, "y": 50, "width": 220, "height": 100}
        canvas.set_regions([region])
        canvas.add_text(["Texto anterior"], [region])
        original_item = canvas._text_items[0]
        original_size = original_item.font().pointSize()

        canvas.preview_text_value(0, "Traducción nueva", region)

        self.assertEqual(canvas._text_values[0], "Traducción nueva")
        self.assertEqual(canvas._text_items[0].toPlainText(), "Traducción nueva")
        self.assertIs(canvas._text_items[0], original_item)
        self.assertEqual(canvas._text_items[0].font().pointSize(), original_size)

    def test_double_click_edits_text_inside_the_canvas_box(self) -> None:
        canvas = CanvasView()
        canvas.resize(520, 360)
        image = QImage(500, 320, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "inline", "x": 80, "y": 70, "width": 300, "height": 130,
            "translation": "Texto anterior", "applied_text": "Texto anterior",
            "style": {"font_family": "Arial", "font_size": 28, "text_margin": 10},
        }
        canvas.set_regions([region])
        canvas.add_text(["Texto anterior"], [region])
        committed = []
        canvas.inline_text_committed.connect(lambda index, text: committed.append((index, text)))
        canvas.show()
        self.app.processEvents()

        point = canvas.mapFromScene(QPointF(180, 120))
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        self.app.processEvents()

        self.assertIsNotNone(canvas._inline_editor)
        self.assertTrue(canvas._inline_editor.hasFocus())
        self.assertFalse(canvas._text_items[0].isVisible())
        canvas._inline_editor.setPlainText("Editado dentro de la caja")
        canvas.finish_inline_text_edit(commit=True)

        self.assertEqual(committed, [(0, "Editado dentro de la caja")])
        self.assertEqual(canvas._regions[0].region["applied_text"], "Editado dentro de la caja")
        self.assertEqual(canvas._text_values[0], "Editado dentro de la caja")
        self.assertTrue(canvas._text_items[0].isVisible())
        canvas.close()

    def test_inline_canvas_edit_can_be_cancelled_without_changing_layer(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "cancel-inline", "x": 40, "y": 50, "width": 220, "height": 100,
            "translation": "Conservar", "applied_text": "Conservar",
        }
        canvas.set_regions([region])
        canvas.add_text(["Conservar"], [region])
        committed = []
        canvas.inline_text_committed.connect(lambda index, text: committed.append((index, text)))

        self.assertTrue(canvas.begin_inline_text_edit(0))
        canvas._inline_editor.setPlainText("Descartar")
        canvas.finish_inline_text_edit(commit=False)

        self.assertEqual(committed, [])
        self.assertEqual(canvas._regions[0].region["applied_text"], "Conservar")
        self.assertEqual(canvas._text_values[0], "Conservar")

    def test_live_text_survives_the_next_box_geometry_commit(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {"id": "manual", "x": 40, "y": 50, "width": 220, "height": 100}
        canvas.set_regions([region])
        canvas.add_text([""], [region])
        edited = {**region, "translation": "Texto nuevo", "applied_text": "Texto nuevo"}
        canvas.preview_text_value(0, "Texto nuevo", edited)
        emitted = []
        canvas.regions_changed.connect(lambda regions: emitted.append(regions))

        canvas._regions[0].region["x"] = 75
        canvas._emit_regions()

        self.assertEqual(emitted[-1][0]["applied_text"], "Texto nuevo")
        self.assertEqual(emitted[-1][0]["translation"], "Texto nuevo")

    def test_moving_box_preserves_exact_text_composition(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 360, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "dialogue", "x": 40, "y": 50, "width": 300, "height": 145,
            "applied_text": "Una traducción que conserva exactamente su composición.",
            "style": {"font_family": "Arial", "font_size": 34, "auto_fit": True},
        }
        canvas.set_regions([region])
        canvas.add_text([region["applied_text"]], [region])
        text_item = canvas._text_items[0]
        before = (
            text_item.toPlainText(), text_item.font().pointSize(),
            text_item.transform().m11(), text_item.transform().m22(),
            text_item.boundingRect().size(),
        )

        canvas._regions[0].setPos(75, 35)
        canvas._regions[0]._commit_geometry(change_kind="move")

        after = (
            text_item.toPlainText(), text_item.font().pointSize(),
            text_item.transform().m11(), text_item.transform().m22(),
            text_item.boundingRect().size(),
        )
        self.assertEqual(canvas._last_region_change_kind, "move")
        self.assertIs(canvas._text_items[0], text_item)
        self.assertEqual(after, before)

    def test_only_visible_corner_handles_resize_a_text_box(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 360, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        canvas.set_regions([{"id": "box", "x": 40, "y": 50, "width": 300, "height": 145}])
        item = canvas._regions[0]
        rect = item.rect()

        self.assertEqual(
            item._edges_at(QPointF(rect.center().x(), rect.top())),
            (False, False, False, False),
        )
        self.assertEqual(item._edges_at(rect.topLeft()), (True, False, True, False))
        self.assertEqual(item._edges_at(rect.bottomRight()), (False, True, False, True))

    def test_ctrl_left_click_deletes_the_clicked_text_box(self) -> None:
        canvas = CanvasView()
        canvas.resize(640, 480)
        image = QImage(500, 360, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        canvas.set_regions([
            {"id": "keep", "x": 40, "y": 40, "width": 120, "height": 70},
            {"id": "delete", "x": 240, "y": 150, "width": 150, "height": 90},
        ])
        emitted = []
        canvas.regions_changed.connect(lambda regions: emitted.append(regions))
        canvas.show()
        self.app.processEvents()

        click = canvas.mapFromScene(QPointF(300, 190))
        QTest.mouseClick(
            canvas.viewport(), Qt.LeftButton, Qt.ControlModifier, click,
        )
        self.app.processEvents()

        self.assertEqual([item.region["id"] for item in canvas._regions], ["keep"])
        self.assertEqual([region["id"] for region in emitted[-1]], ["keep"])
        canvas.close()

    def test_manual_box_resize_keeps_current_font_size(self) -> None:
        canvas = CanvasView()
        image = QImage(600, 400, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "fixed-size", "x": 60, "y": 70, "width": 390, "height": 180,
            "applied_text": "El tamaÃ±o visible no cambia al manipular la caja.",
            "style": {"font_family": "Arial", "font_size": 46, "auto_fit": True},
        }
        canvas.set_regions([region])
        canvas.add_text([region["applied_text"]], [region])
        item = canvas._text_items[0]
        visible_size = item.font().pointSize()

        canvas._regions[0].setRect(60, 70, 250, 110)
        canvas._regions[0]._commit_geometry(notify=False)

        self.assertEqual(item.font().pointSize(), visible_size)
        self.assertEqual(canvas._regions[0].region["style"]["font_size"], visible_size)
        self.assertFalse(canvas._regions[0].region["style"]["auto_fit"])

    def test_real_corner_drag_disables_auto_fit_and_never_changes_font_size(self) -> None:
        canvas = CanvasView()
        canvas.resize(700, 500)
        image = QImage(600, 400, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "corner-fixed", "x": 80, "y": 90, "width": 320, "height": 170,
            "applied_text": "La esquina cambia la caja, no el tamaÃ±o tipogrÃ¡fico.",
            "style": {"font_family": "Arial", "font_size": 42, "auto_fit": True},
        }
        canvas.set_regions([region])
        canvas.add_text([region["applied_text"]], [region])
        canvas.show()
        self.app.processEvents()
        original_size = canvas._text_items[0].font().pointSize()

        def synchronize(regions):
            canvas.set_regions(regions)
            values = [entry.get("applied_text", "") for entry in regions]
            canvas.update_text_layers([0], values, regions)

        canvas.regions_changed.connect(synchronize)
        start = canvas.mapFromScene(QPointF(400, 90))
        end = canvas.mapFromScene(QPointF(310, 135))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end, delay=10)

        self.assertEqual(canvas._text_items[0].font().pointSize(), original_size)
        self.assertFalse(canvas._regions[0].region["style"]["auto_fit"])

        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(canvas._text_items[0].font().pointSize(), original_size)
        self.assertFalse(canvas._regions[0].region["style"]["auto_fit"])
        canvas.close()

    def test_real_border_drag_moves_box_without_resizing_or_reflowing_text(self) -> None:
        canvas = CanvasView()
        canvas.resize(700, 500)
        image = QImage(600, 400, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "drag", "x": 80, "y": 90, "width": 320, "height": 150,
            "applied_text": "Composición estable durante el movimiento.",
            "style": {"font_size": 34, "auto_fit": True},
        }
        canvas.set_regions([region])
        canvas.add_text([region["applied_text"]], [region])
        canvas.show()
        self.app.processEvents()
        text_item = canvas._text_items[0]
        before_text = (text_item.toPlainText(), text_item.font().pointSize(), text_item.textWidth())
        start = canvas.mapFromScene(QPointF(240, 90))
        end = start + canvas.mapFromScene(QPointF(55, 35)) - canvas.mapFromScene(QPointF(0, 0))

        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end, delay=10)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()

        moved = canvas._regions[0].region
        self.assertEqual((moved["width"], moved["height"]), (320, 150))
        self.assertGreater(moved["x"], 80)
        self.assertGreater(moved["y"], 90)
        self.assertEqual(
            (text_item.toPlainText(), text_item.font().pointSize(), text_item.textWidth()),
            before_text,
        )
        canvas.close()

    def test_real_center_drag_moves_the_whole_box_without_deformation(self) -> None:
        canvas = CanvasView()
        canvas.resize(700, 500)
        image = QImage(600, 400, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "center-drag", "x": 80, "y": 90, "width": 320, "height": 150,
            "applied_text": "El centro mueve toda la caja sin cambiar el texto.",
            "style": {"font_size": 34, "auto_fit": True},
        }
        canvas.set_regions([region])
        canvas.add_text([region["applied_text"]], [region])
        canvas.show()
        self.app.processEvents()
        text_item = canvas._text_items[0]
        before = (
            text_item.toPlainText(), text_item.font().pointSize(), text_item.textWidth(),
            text_item.boundingRect().size(),
        )
        start = canvas.mapFromScene(QPointF(240, 165))
        delta = canvas.mapFromScene(QPointF(70, 45)) - canvas.mapFromScene(QPointF(0, 0))
        end = start + delta

        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end, delay=10)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()

        moved = canvas._regions[0].region
        after = (
            text_item.toPlainText(), text_item.font().pointSize(), text_item.textWidth(),
            text_item.boundingRect().size(),
        )
        self.assertEqual((moved["width"], moved["height"]), (320, 150))
        self.assertGreaterEqual(moved["x"], 148)
        self.assertGreaterEqual(moved["y"], 133)
        self.assertEqual(after, before)
        canvas.close()

    def test_continuous_box_move_only_translates_existing_text_item(self) -> None:
        canvas = CanvasView()
        image = QImage(800, 1200, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        region = {
            "id": "fast-move", "x": 40, "y": 50, "width": 420, "height": 180,
            "applied_text": "Texto compuesto que no debe recalcularse mientras se mueve.",
            "style": {"font_size": 36, "auto_fit": True, "balloon_fit": True},
        }
        canvas.set_regions([region])
        canvas.add_text([region["applied_text"]], [region])
        text_item = canvas._text_items[0]
        original_position = QPointF(text_item.pos())

        with (
            patch.object(canvas, "_preview_text_reflow") as reflow,
            patch.object(canvas, "_position_text_item") as reposition,
        ):
            for offset in range(1, 101):
                canvas._regions[0].setPos(offset, offset * 2)
                canvas._regions[0]._commit_geometry(notify=False)

        reflow.assert_not_called()
        reposition.assert_not_called()
        self.assertIs(canvas._text_items[0], text_item)
        self.assertEqual(text_item.pos(), original_position + QPointF(100, 200))

    def test_optical_center_uses_real_glyph_bounds(self) -> None:
        dx, dy = TypographyManager.optical_offset(QFont("Arial", 36), "ABCgj")
        self.assertIsInstance(dx, float)
        self.assertIsInstance(dy, float)
        self.assertLessEqual(abs(dx), 8.0)
        self.assertLessEqual(abs(dy), 8.0)
        style = TypographyManager.normalized({
            "auto_fit": True, "balloon_fit": False,
            "auto_scale": True, "optical_center": True,
        })
        self.assertTrue(style["auto_fit"])
        self.assertFalse(style["balloon_fit"])
        self.assertTrue(style["auto_scale"])
        self.assertTrue(style["optical_center"])

    def test_dialogue_auto_fits_inside_a_small_box(self) -> None:
        canvas = CanvasView()
        image = QImage(420, 260, QImage.Format_RGB888)
        image.fill(255)
        region = {
            "id": "dialogue", "x": 30, "y": 30, "width": 300, "height": 150,
            "style": {"font_size": 48, "auto_fit": True, "balloon_fit": False},
        }
        canvas.set_image(image)
        canvas.set_regions([region])
        canvas.add_text([
            "UN PEQUEÑO PREMIO POR MI HABILIDAD CULINARIA NO TE PONGAS A PENSAR DEMASIADO"
        ], [region])

        item = canvas._text_items[0]
        self.assertLess(item.font().pointSize(), 48)
        self.assertFalse(region.get("text_overflow", True))
        self.assertLessEqual(item.boundingRect().height() * float(item.data(4)), region["height"] + 1)

    def test_normal_dialogue_never_follows_balloon_shape_even_for_legacy_styles(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 300, QImage.Format_RGB888)
        image.fill(255)
        region = {
            "id": "legacy-balloon", "x": 30, "y": 30, "width": 360, "height": 170,
            "style": {"font_size": 42, "auto_fit": True, "balloon_fit": False},
        }
        canvas.set_image(image)
        canvas.set_regions([region])
        with patch("ui.canvas_view.detect_balloon_interior") as detector:
            canvas.add_text([
                "ESTE TEXTO DEBE USAR LÍNEAS RECTANGULARES SIN SEGUIR EL CONTORNO DEL GLOBO"
            ], [region])

        detector.assert_not_called()
        self.assertFalse(region["style"]["balloon_fit"])
        block = canvas._text_items[0].document().begin()
        while block.isValid():
            formatting = block.blockFormat()
            self.assertEqual(formatting.leftMargin(), 0.0)
            self.assertEqual(formatting.rightMargin(), 0.0)
            block = block.next()

    def test_layers_panel_displays_psd_hierarchy(self) -> None:
        panel = LayersPanel()
        panel.set_image_layers(True, True)
        self.assertTrue(panel.clean_layer.isVisibleTo(panel))
        panel.set_source_layers([
            {"id": "0", "name": "Grupo", "group": True, "depth": 0, "visible": True, "opacity": 100},
            {"id": "0/0", "name": "Texto", "kind": "type", "depth": 1, "visible": True, "opacity": 75},
        ], {})
        self.assertTrue(panel.psd_layers.isVisibleTo(panel))
        self.assertEqual(panel.psd_layers_layout.count(), 2)
        panel.set_psd_sync_state(
            available=True, auto=True, text="Sincronizado · 0.4 s", syncing=False,
        )
        self.assertTrue(panel.psd_auto_sync.isChecked())
        self.assertEqual(panel.psd_sync_status.text(), "Sincronizado · 0.4 s")
        self.assertTrue(panel.psd_sync_button.isEnabled())

    def test_retouch_layers_are_grouped_in_a_collapsible_menu(self) -> None:
        panel = LayersPanel()
        self.assertFalse(panel.retouch_section.is_expanded())
        panel.set_image_layers(
            True, True, {},
            {"automatic": 3, "paint": 1, "restore": 0},
            {},
        )
        self.assertIn("4 parches", panel.retouch_section.header.text())
        panel.retouch_section.set_expanded(True)
        self.assertTrue(panel.retouch_section.is_expanded())
        self.assertFalse(panel.retouch_rows["automatic"].isHidden())

    def test_effects_are_configured_in_their_own_tool_and_previewed_live(self) -> None:
        panel = TextEffectsPanel()
        panel.set_layer(0, {
            "stroke_width": 3, "stroke_color": "#112233",
            "gradient_enabled": True, "gradient_start": "#FF0000", "gradient_end": "#0000FF",
            "glow_enabled": True, "glow_color": "#00FF00", "glow_radius": 12, "glow_opacity": 70,
        })
        values = panel.values()
        self.assertEqual(values["stroke_width"], 3)
        self.assertTrue(values["gradient_enabled"])
        self.assertTrue(values["glow_enabled"])

        canvas = CanvasView()
        image = QImage(420, 220, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "x": 30, "y": 30, "width": 360, "height": 150, "applied_text": "EFECTOS",
            "style": {**values, "font_family": "Arial", "font_size": 54, "auto_fit": False},
        }
        canvas.set_regions([region]); canvas.add_text(["EFECTOS"], [region])
        self.assertIsInstance(canvas._text_items[0].graphicsEffect(), QGraphicsDropShadowEffect)

    def test_sfx_tool_creates_vector_paths_and_keeps_nodes(self) -> None:
        panel = SFXPanel()
        panel.set_layer(0, {"sfx_enabled": True, "sfx_curve": -45, "sfx_node_tr": 25})
        values = panel.values()
        self.assertTrue(values["sfx_enabled"])
        self.assertEqual(values["sfx_curve"], -45)
        canvas = CanvasView()
        image = QImage(420, 220, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "sfx", "x": 30, "y": 30, "width": 360, "height": 150,
            "applied_text": "BOOM", "style": {
                **TypographyManager.normalized({}), **values,
                "font_family": "Arial", "font_size": 70, "font_weight": 900,
            },
        }
        canvas.set_regions([region]); canvas.add_text(["BOOM"], [region])
        self.assertEqual(canvas._text_items[0].data(5), "sfx")
        self.assertFalse(canvas._text_items[0].path().isEmpty())
        self.assertEqual(len(canvas._sfx_glyph_cache), 1)
        canvas.show_sfx_nodes(0)
        self.assertEqual(len(canvas._sfx_node_items), 4)
        changed = []
        canvas.sfx_nodes_changed.connect(lambda index, node_values: changed.append((index, node_values)))
        top_left = canvas._sfx_node_items[0]
        top_left.setPos(top_left.pos() + QPointF(20, 15))
        canvas._sfx_node_moved(top_left)
        self.assertEqual(changed[-1][0], 0)
        self.assertNotEqual(changed[-1][1]["sfx_quad_tl_x"], 0)
        self.assertNotEqual(changed[-1][1]["sfx_quad_tl_y"], 0)

    def test_project_font_keeps_latin_accents_and_punctuation(self) -> None:
        font = QFont("Arial", 36)
        self.assertTrue(font_supports_text(font, "áéíóú ñ ¿¡!"))

    def test_sfx_preserves_manual_line_breaks(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 300, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "multiline-sfx", "x": 40, "y": 40, "width": 400, "height": 220,
            "applied_text": "BOOM\nCRASH",
            "style": {**TypographyManager.normalized({}), "sfx_enabled": True, "font_size": 64},
        }
        canvas.set_regions([region]); canvas.add_text([region["applied_text"]], [region])
        cached_lines, _fallback = next(iter(canvas._sfx_glyph_cache.values()))
        self.assertEqual(len(cached_lines), 2)
        self.assertEqual("".join(char for char, _advance, _path in cached_lines[0]), "BOOM")
        self.assertEqual("".join(char for char, _advance, _path in cached_lines[1]), "CRASH")

    def test_sfx_automatically_balances_text_inside_its_box(self) -> None:
        canvas = CanvasView()
        image = QImage(360, 360, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "auto-sfx", "x": 60, "y": 60, "width": 240, "height": 240,
            "applied_text": "UNO DOS TRES CUATRO CINCO",
            "style": {
                **TypographyManager.normalized({}), "sfx_enabled": True,
                "sfx_auto_layout": True, "font_family": "Arial", "font_size": 54,
            },
        }
        canvas.set_regions([region]); canvas.add_text([region["applied_text"]], [region])
        bounds = canvas._text_items[0].path().boundingRect()

        self.assertGreater(len(region["sfx_layout_lines"]), 1)
        self.assertLessEqual(bounds.width(), region["width"] + 1)
        self.assertLessEqual(bounds.height(), region["height"] + 1)

    def test_sfx_respects_horizontal_and_vertical_box_alignment(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 320, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        base = {
            "id": "aligned-sfx", "x": 40, "y": 30, "width": 400, "height": 240,
            "applied_text": "BOOM", "style": {
                **TypographyManager.normalized({}), "sfx_enabled": True,
                "sfx_auto_layout": False, "font_family": "Arial", "font_size": 58,
                "text_margin": 12,
            },
        }
        left_top = copy.deepcopy(base)
        left_top["style"].update({"alignment": "left", "vertical_alignment": "top"})
        canvas.set_regions([left_top]); canvas.add_text(["BOOM"], [left_top])
        first = canvas._text_items[0].path().boundingRect()

        right_bottom = copy.deepcopy(base)
        right_bottom["style"].update({"alignment": "right", "vertical_alignment": "bottom"})
        canvas.set_regions([right_bottom]); canvas.add_text(["BOOM"], [right_bottom])
        second = canvas._text_items[0].path().boundingRect()

        self.assertLessEqual(first.left(), 13.5)
        self.assertLessEqual(first.top(), 13.5)
        self.assertGreaterEqual(second.right(), right_bottom["width"] - 13.5)
        self.assertGreaterEqual(second.bottom(), right_bottom["height"] - 13.5)

    def test_text_panel_no_longer_exposes_alternative_fonts(self) -> None:
        panel = TextOptionsPanel()

        self.assertFalse(hasattr(panel, "fallback_fonts"))

    def test_sfx_perspective_corners_are_free_and_clear_when_disabled(self) -> None:
        canvas = CanvasView()
        image = QImage(600, 400, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "free-sfx", "x": 100, "y": 80, "width": 300, "height": 180,
            "applied_text": "FX",
            "style": {**TypographyManager.normalized({}), "sfx_enabled": True},
        }
        canvas.set_regions([region]); canvas.add_text(["FX"], [region]); canvas.show_sfx_nodes(0)
        top_right = canvas._sfx_node_items[1]
        top_right.setPos(850, -120)
        canvas._sfx_node_moved(top_right)
        self.assertGreater(canvas._regions[0].region["style"]["sfx_quad_tr_x"], 200)
        self.assertLess(canvas._regions[0].region["style"]["sfx_quad_tr_y"], 0)

        canvas._regions[0].region["style"]["sfx_enabled"] = False
        canvas.show_sfx_nodes(0)
        self.assertEqual(canvas._sfx_node_items, [])
        self.assertEqual(canvas._sfx_guide_items, [])

    def test_sfx_bezier_and_mesh_nodes_are_edited_directly_on_canvas(self) -> None:
        canvas = CanvasView()
        image = QImage(600, 400, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "mesh-sfx", "x": 100, "y": 80, "width": 320, "height": 180,
            "applied_text": "BOOM",
            "style": {
                **TypographyManager.normalized({}), "sfx_enabled": True,
                "sfx_bezier_enabled": True, "sfx_mesh_enabled": True,
            },
        }
        canvas.set_regions([region]); canvas.add_text(["BOOM"], [region]); canvas.show_sfx_nodes(0)
        self.assertEqual(len(canvas._sfx_node_items), 11)  # 4 corners + 5 mesh + 2 Bézier.
        centre = next(item for item in canvas._sfx_node_items if item.node_type == "mesh" and item.corner == "r1c1")
        original_path = QPainterPath(canvas._text_items[0].path())
        centre.setPos(centre.pos() + QPointF(35, -20))
        canvas._sfx_node_moved(centre, True)
        style = canvas._regions[0].region["style"]
        self.assertNotEqual(style["sfx_mesh_r1c1_dx"], 0)
        self.assertNotEqual(style["sfx_mesh_r1c1_dy"], 0)
        self.assertNotEqual(canvas._text_items[0].path(), original_path)

    def test_fit_once_returns_layer_to_manual_mode_and_keeps_snapshot(self) -> None:
        canvas = CanvasView()
        image = QImage(360, 220, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "fit-once", "x": 30, "y": 30, "width": 180, "height": 80,
            "applied_text": "UN TEXTO DEMASIADO LARGO PARA SU TAMAÑO ACTUAL",
            "style": {**TypographyManager.normalized({}), "font_size": 64, "fit_once": True},
        }
        canvas.set_regions([region]); canvas.add_text([region["applied_text"]], [region])
        resulting = canvas._regions[0].region
        self.assertFalse(resulting["style"]["auto_fit"])
        self.assertNotIn("fit_once", resulting["style"])
        self.assertLess(resulting["style"]["font_size"], 64)
        self.assertEqual(resulting["layout_snapshot"]["font_size"], resulting["style"]["font_size"])

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_project_selector_groups_searches_and_restores_current_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_type("Mangas")
            manager.create_project("Manhwas", "Valiente")
            manager.create_project("Manhwas", "Solo Leveling")
            manager.create_project("Mangas", "Berserk")
            manager.set_font("Manhwas", "Valiente", "Diálogo", "Arial")

            dialog = ProjectSelectionDialog(manager, "Manhwas", "Valiente")
            self.assertEqual(dialog.categories.count(), 3)  # Todos + dos categorías.
            self.assertEqual(dialog.selected_project(), ("Manhwas", "Valiente"))
            dialog.search.setText("Solo")
            self.assertEqual(dialog.projects.count(), 1)
            self.assertEqual(dialog.selected_project(), ("Manhwas", "Solo Leveling"))

    def test_watermarks_can_be_moved_and_deleted_independently_on_canvas(self) -> None:
        payload = BytesIO()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(payload, "PNG")
        canvas = CanvasView()
        page = QImage(400, 900, QImage.Format_RGB888)
        page.fill(255)
        canvas.set_image(page)
        settings = {
            "png_bytes": payload.getvalue(), "size_mode": "pixels", "width_px": 40,
            "repeat": True, "auto_count": False, "repeat_count": 3, "anchor": "top-center",
            "seam_safe": False,
        }
        canvas.set_watermark(settings, True, "01.png")
        self.assertGreater(len(canvas._watermark_items), 1)
        self.assertEqual(len({round(item.pos().x()) for item in canvas._watermark_items}), 1)
        emitted = []
        canvas.watermark_positions_changed.connect(emitted.append)
        first = canvas._watermark_items[0]
        first.setPos(25, 35)
        canvas._emit_watermark_positions()
        self.assertEqual(emitted[-1][0], [25, 35])
        original_count = len(canvas._watermark_items)
        canvas._delete_watermark_item(first)
        self.assertEqual(len(canvas._watermark_items), original_count - 1)

    def test_brush_mode_locks_boxes_and_watermarks_until_disabled(self) -> None:
        payload = BytesIO()
        Image.new("RGBA", (30, 15), (255, 255, 255, 255)).save(payload, "PNG")
        canvas = CanvasView()
        page = QImage(300, 300, QImage.Format_RGB888); page.fill(0)
        canvas.set_image(page)
        canvas.set_regions([{"x": 20, "y": 20, "width": 80, "height": 50}])
        canvas.set_watermark({"png_bytes": payload.getvalue()}, True, "01.png")

        canvas.set_brush_mode("mask", 20)
        self.assertFalse(bool(canvas._regions[0].flags() & QGraphicsItem.ItemIsMovable))
        self.assertFalse(bool(canvas._watermark_items[0].flags() & QGraphicsItem.ItemIsMovable))
        self.assertEqual(canvas._watermark_items[0].acceptedMouseButtons(), Qt.NoButton)

        canvas.set_brush_mode(None, 20)
        self.assertTrue(bool(canvas._regions[0].flags() & QGraphicsItem.ItemIsMovable))
        self.assertTrue(bool(canvas._watermark_items[0].flags() & QGraphicsItem.ItemIsMovable))

    def test_multiple_layer_selection_is_preserved(self) -> None:
        panel = LayersPanel()
        regions = [
            {"id": str(index), "x": 0, "y": 0, "width": 40, "height": 20}
            for index in range(4)
        ]
        panel.set_regions(regions)
        panel.set_selected_indices([0, 2, 3], 3)
        self.assertEqual(panel.selected_indices(), [0, 2, 3])
        panel.set_regions(regions)
        self.assertEqual(panel.selected_indices(), [0, 2, 3])

    def test_layer_selection_does_not_move_long_page_viewport(self) -> None:
        canvas = CanvasView()
        canvas.resize(500, 320)
        image = QImage(420, 4000, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        canvas.set_zoom(100)
        canvas.set_regions([
            {"id": "top", "x": 30, "y": 30, "width": 300, "height": 120},
            {"id": "bottom", "x": 30, "y": 3700, "width": 300, "height": 120},
        ])
        canvas.show()
        self.app.processEvents()
        scrollbar = canvas.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.app.processEvents()
        bottom_position = scrollbar.value()

        canvas.select_regions([0], 0)
        self.app.processEvents()

        self.assertGreater(bottom_position, 0)
        self.assertEqual(scrollbar.value(), bottom_position)
        canvas.close()

    def test_ai_tools_remain_visible_without_horizontal_clipping(self) -> None:
        panel = AIOptionsPanel()
        panel.resize(280, 700)
        panel.show()
        self.app.processEvents()

        for mode in ("ocr", "translation", "clean"):
            panel.mode_buttons[mode].setChecked(True)
            self.app.processEvents()
            self.assertEqual(panel.horizontalScrollBar().maximum(), 0)

        panel.verticalScrollBar().setValue(panel.verticalScrollBar().maximum())
        panel.mode_buttons["ocr"].setChecked(True)
        self.app.processEvents()
        self.assertEqual(panel.verticalScrollBar().value(), 0)

        for button in (
            panel._primary_buttons["clean"], panel.mask_erase_button,
            panel.apply_mask_button, panel.cancel_mask_button,
            panel.retouch_button, panel.restore_button, panel.mask_brush_button,
        ):
            self.assertGreaterEqual(button.width() + 1, button.minimumSizeHint().width())
        panel.close()

    def test_ai_modes_offer_single_page_and_chapter_scopes(self) -> None:
        panel = AIOptionsPanel()
        emitted = []
        panel.action_requested.connect(emitted.append)
        expected = {
            "ocr": {"run_ocr_api_one", "run_ocr_api", "run_ocr_api_all"},
            "translation": {"run_translation_one", "run_translation", "run_translation_all"},
            "clean": {"run_clean_one", "run_clean", "run_clean_all"},
        }
        for mode, actions in expected.items():
            panel.mode_buttons[mode].setChecked(True)
            scope = getattr(panel, f"{mode}_scope")
            for index in range(3):
                scope.setCurrentIndex(index)
                panel._primary_buttons[mode].click()
            self.assertTrue(actions.issubset(set(emitted)))

    def test_font_picker_lists_every_family_and_resolves_typed_search(self) -> None:
        families = tuple(f"Family {index:04}" for index in range(420))
        combo = QComboBox()
        with patch("ui.font_utils.font_families", return_value=families):
            configure_searchable_font_combo(combo, "Family 0000")

        model = combo.model()
        self.assertIsInstance(model, FontFamilyModel)
        self.assertEqual(model.total_count, len(families))
        self.assertLess(model.rowCount(), model.total_count)

        resets: list[int] = []
        model.modelReset.connect(lambda: resets.append(1))
        select_font_family(combo, "Family 0200")
        self.assertEqual(resets, [], "Selecting from the full catalogue must not rebuild 2,000+ rows")
        while model.rowCount() <= 300:
            model.fetchMore()
        rendered_font = model.data(model.index(300, 0), Qt.FontRole)
        self.assertIsInstance(rendered_font, QFont)
        self.assertEqual(rendered_font.family(), "Family 0300")

        combo.lineEdit().setText("Family 031")
        combo.lineEdit().textEdited.emit("Family 031")
        self.assertLess(model.rowCount(), len(families))
        self.assertEqual(commit_font_combo_text(combo), "Family 0310")
        self.assertEqual(combo.currentText(), "Family 0310")
        self.assertEqual(model.total_count, len(families))

    def test_profile_assignment_uses_explicit_ids_and_updates_font_source(self) -> None:
        panel = TextOptionsPanel()
        panel.set_profiles(
            ["Manhwas"], "Manhwas", ["Valiente"], "Valiente", True,
            {"Diálogo": {"family": "Segoe UI", "file": ""}},
        )
        panel.set_layer(0, {"font_family": "Segoe UI"})
        panel.set_font_role("Diálogo")

        self.assertEqual(panel.project_type.currentData(), "Manhwas")
        self.assertEqual(panel.profile.currentData(), "Valiente")
        self.assertIn("Manhwas", panel.profile_summary.text())
        self.assertIn("Diálogo", panel.font_source_label.text())
        self.assertFalse(panel.project_type.isEnabled())

    def test_profile_font_search_accepts_typing_and_selects_first_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_project("Manhwas", "Valiente")
            widget = ProfileSettingsWidget(manager, "Manhwas", "Valiente", False)
            with patch("ui.font_utils.font_families", return_value=("Alpha Sans", "Beta Display", "Gamma Serif")):
                widget._load_font_choices()

            widget.font_search.setText("Beta")
            widget.font_search.textEdited.emit("Beta")
            self.assertEqual(widget.family.model().rowCount(), 1)
            widget._choose_first_filtered_font()
            self.assertEqual(widget.family.currentText(), "Beta Display")
            self.assertIn("Beta Display", widget.font_status.text())

    def test_profile_font_picker_handles_real_typing_and_enter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_project("Manhwas", "Valiente")
            families = ("Alpha Sans", "Beta Display", "Gamma Serif", "Gamma Text")
            with (
                patch("ui.font_utils.font_families", return_value=families),
                patch("ui.profile_settings.font_families", return_value=families),
            ):
                widget = ProfileSettingsWidget(manager, "Manhwas", "Valiente", False)
                widget._load_font_choices()

            search = widget.family.lineEdit()
            search.selectAll()
            QTest.keyClicks(search, "Gamma Ser")
            self.app.processEvents()
            model = widget.family.model()
            self.assertEqual(model.total_count, 1)
            self.assertEqual(model.data(model.index(0, 0), Qt.DisplayRole), "Gamma Serif")
            self.assertFalse(widget.family.view().isVisible())

            QTest.keyClick(search, Qt.Key_Return)
            self.app.processEvents()
            self.assertEqual(widget.family.currentText(), "Gamma Serif")
            widget.close()

    def test_profile_font_picker_accepts_a_real_result_click(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Mangas")
            manager.create_project("Mangas", "Prueba")
            families = ("Alpha Sans", "Beta Display", "Gamma Serif")
            with (
                patch("ui.font_utils.font_families", return_value=families),
                patch("ui.profile_settings.font_families", return_value=families),
            ):
                widget = ProfileSettingsWidget(manager, "Mangas", "Prueba", False)
                widget._load_font_choices()
            widget.show()
            search = widget.family.lineEdit()
            search.selectAll()
            QTest.keyClicks(search, "Beta")
            self.app.processEvents()
            view = widget.family.view()
            target = widget.family.model().index(0, 0)
            widget.family.showPopup()
            self.app.processEvents()

            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(target).center())
            self.app.processEvents()

            self.assertEqual(widget.family.currentText(), "Beta Display")
            widget.close()

    def test_profile_save_resets_to_new_mode_and_preserves_multiple_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_project("Manhwas", "Valiente")
            families = ("Alpha Sans", "Beta Display", "Gamma Serif")
            with (
                patch("ui.font_utils.font_families", return_value=families),
                patch("ui.profile_settings.font_families", return_value=families),
            ):
                widget = ProfileSettingsWidget(manager, "Manhwas", "Valiente", False)
                widget._load_font_choices()

                widget.alias.setText("Diálogo")
                select_font_family(widget.family, "Alpha Sans")
                widget.font_weight.setCurrentIndex(widget.font_weight.findData(700))
                widget.font_italic.setChecked(True)
                widget.font_case.setCurrentIndex(widget.font_case.findData("upper"))
                widget.font_size.setValue(42)
                widget.font_color.setText("#C13255")
                widget._save_font()

                self.assertEqual(widget._previous_alias, "")
                self.assertEqual(widget.fonts.currentRow(), -1)
                widget.alias.setText("Gritos")
                select_font_family(widget.family, "Beta Display")
                widget._save_font()

            entries = manager.font_entries("Manhwas", "Valiente")
            self.assertEqual(set(entries), {"Diálogo", "Gritos"})
            self.assertEqual(entries["Diálogo"]["family"], "Alpha Sans")
            self.assertEqual(entries["Diálogo"]["style"]["font_weight"], 700)
            self.assertTrue(entries["Diálogo"]["style"]["italic"])
            self.assertEqual(entries["Diálogo"]["style"]["text_case"], "upper")
            self.assertEqual(entries["Diálogo"]["style"]["font_size"], 42)
            self.assertEqual(entries["Diálogo"]["style"]["text_color"], "#C13255")
            self.assertEqual(entries["Gritos"]["family"], "Beta Display")

    def test_profile_role_search_and_preset_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_project("Manhwas", "SoloLeveling")
            manager.set_font("Manhwas", "SoloLeveling", "Diálogo", "Arial")
            manager.set_font("Manhwas", "SoloLeveling", "Gritos de Batalla", "Impact")
            widget = ProfileSettingsWidget(manager, "Manhwas", "SoloLeveling", False)

            # Test role preset
            widget._apply_role_preset("thought")
            self.assertEqual(widget.alias.text(), "Pensamiento")
            self.assertEqual(widget.font_size.value(), 32)
            self.assertTrue(widget.font_italic.isChecked())

            # Test role search filter
            self.assertEqual(widget.fonts.count(), 2)
            widget.role_search.setText("Gritos")
            self.assertEqual(widget.fonts.count(), 1)
            self.assertIn("Gritos de Batalla", widget.fonts.item(0).text())

            widget.role_search.setText("no-match")
            self.assertEqual(widget.fonts.count(), 0)

            widget.role_search.clear()
            self.assertEqual(widget.fonts.count(), 2)

    def test_profile_role_applies_saved_size_and_color_to_text_panel(self) -> None:
        panel = TextOptionsPanel()
        panel.set_profiles(
            ["Manhwas"], "Manhwas", ["Valiente"], "Valiente", True,
            {"Diálogo": {
                "family": "Segoe UI", "file": "",
                "style": {"font_size": 44, "text_color": "#D24A62"},
            }},
        )
        panel.set_layer(0, {"font_family": "Segoe UI"})
        emitted: list[tuple] = []
        panel.font_role_selected.connect(lambda *args: emitted.append(args))

        panel.font_role.setCurrentIndex(1)

        self.assertEqual(panel.size.value(), 44)
        self.assertEqual(panel.text_color.text(), "#D24A62")
        self.assertEqual(emitted[-1][3]["font_size"], 44)

    def test_toast_exposes_state_close_action_and_compact_width(self) -> None:
        parent = QWidget()
        parent.resize(900, 600)
        toast = ToastNotification("No se pudo guardar", "Revisa el destino.", parent, "error")
        dismissed: list[bool] = []
        toast.dismissed.connect(lambda: dismissed.append(True))

        self.assertEqual(toast.property("kind"), "error")
        self.assertLessEqual(toast.width(), 340)
        self.assertEqual(toast.message_key, ("No se pudo guardar", "Revisa el destino.", "error"))
        close_button = toast.findChild(QWidget, "ToastClose")
        self.assertIsNotNone(close_button)
        close_button.click()
        self.assertEqual(dismissed, [True])

    def test_text_panel_previews_and_applies_the_resolved_font(self) -> None:
        families = ("Alpha Sans", "Beta Display", "Gamma Serif")
        panel = TextOptionsPanel()
        with patch("ui.font_utils.font_families", return_value=families):
            panel.ensure_fonts_loaded()
        panel.set_layer(0, {"font_family": "Alpha Sans"})
        changed: list[dict] = []
        panel.style_changed.connect(changed.append)

        panel.family.lineEdit().setText("Beta")
        panel.family.lineEdit().textEdited.emit("Beta")
        self.assertEqual(panel.font_preview.font().family(), "Beta Display")
        panel.family.lineEdit().editingFinished.emit()

        self.assertEqual(panel.family.currentText(), "Beta Display")
        self.assertEqual(changed[-1]["font_family"], "Beta Display")

        gamma_index = panel.family.model().index_for("Gamma Serif")
        panel.family.highlighted.emit(gamma_index)
        self.assertEqual(panel.font_preview.font().family(), "Gamma Serif")
        self.assertEqual(changed[-1]["font_family"], "Gamma Serif")
        panel.family.setCurrentIndex(gamma_index)
        panel.family.activated.emit(gamma_index)
        self.assertEqual(changed[-1]["font_family"], "Gamma Serif")
        self.assertIn("Fuente manual", panel.font_source_label.text())

    def test_ocr_model_selector_accepts_saved_custom_name(self) -> None:
        panel = AIOptionsPanel()
        panel.set_configuration({
            "ocr": {"platform": "Alibaba Cloud", "model": "modelo-ocr-personalizado"},
            "translate": {"platform": "Gemini", "model": "gemini-2.5-flash"},
            "clean": {"platform": "Local (AI)", "model": "LaMa · lama.onnx"},
        })

        self.assertTrue(panel.ocr_model.isEditable())
        self.assertEqual(panel.ocr_configuration()[1], "modelo-ocr-personalizado")

    def test_saved_api_key_is_loaded_back_into_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CredentialStore(Path(temp) / "credentials.dat")
            store.set("Alibaba Cloud", "sk-test-saved-key")
            store.set("DeepSeek", "sk-deepseek-saved-key")
            dialog = SettingsDialog(DEFAULT_SETTINGS, store)

            field = dialog.key_fields["Alibaba Cloud"]
            self.assertEqual(field.text(), "sk-test-saved-key")
            self.assertEqual(field.echoMode(), QLineEdit.Password)
            self.assertEqual(dialog.values()["keys"]["Alibaba Cloud"], "sk-test-saved-key")
            self.assertEqual(dialog.key_fields["DeepSeek"].text(), "sk-deepseek-saved-key")
            self.assertEqual(dialog.values()["keys"]["DeepSeek"], "sk-deepseek-saved-key")
            self.assertGreaterEqual(dialog.translation_provider.findText("Alibaba Cloud"), 0)
            self.assertIn("DeepSeek", dialog.key_fields)
            self.assertGreaterEqual(dialog.translation_provider.findText("DeepSeek"), 0)
            self.assertTrue(dialog.values()["psd_auto_sync"])

    def test_alibaba_translation_models_are_selectable(self) -> None:
        panel = AIOptionsPanel()
        panel.set_configuration({
            "ocr": {"platform": "Alibaba Cloud", "model": "qwen-vl-ocr"},
            "translate": {"platform": "Alibaba Cloud", "model": "qwen-mt-plus"},
            "clean": {"platform": "Local (AI)", "model": "LaMa · lama.onnx"},
        })
        self.assertEqual(panel.translate_configuration(), ("Alibaba Cloud", "qwen-mt-plus"))
        self.assertTrue(panel.translate_model.isEditable())

    def test_deepseek_and_only_production_cleaning_controls_are_exposed(self) -> None:
        panel = AIOptionsPanel()
        panel.translate_provider.setCurrentText("DeepSeek")
        self.assertEqual(panel.translate_configuration(), ("DeepSeek", "deepseek-chat"))
        self.assertEqual(panel.clean_provider.count(), 1)
        self.assertEqual(panel.clean_provider.currentText(), "Local (AI)")

        ocr_page = panel.pages.widget(panel._mode_index["ocr"])
        labels = {button.text() for button in ocr_page.findChildren(QPushButton)}
        self.assertIn("Abrir imágenes", labels)
        self.assertIn("Detectar cajas (YOLO)", labels)
        self.assertNotIn("Pincel máscara", labels)

    def test_comparison_divider_clips_clean_result_to_the_right(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        canvas.set_comparison_mode(True)

        clip = canvas._clean_clip_item.rect()
        self.assertAlmostEqual(clip.x(), 200.0, delta=1.0)
        self.assertAlmostEqual(clip.width(), 200.0, delta=1.0)
        self.assertTrue(canvas._comparison_divider.isVisible())

    def test_comparison_mode_disables_box_selection_and_movement(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        canvas.set_regions([{"id": "one", "x": 40, "y": 50, "width": 120, "height": 70}])
        region = canvas._regions[0]
        self.assertTrue(bool(region.flags() & QGraphicsItem.ItemIsMovable))

        canvas.set_comparison_mode(True)
        self.assertEqual(region.acceptedMouseButtons().value, 0)
        self.assertFalse(bool(region.flags() & QGraphicsItem.ItemIsMovable))
        self.assertFalse(bool(region.flags() & QGraphicsItem.ItemIsSelectable))

        canvas.set_comparison_mode(False)
        self.assertTrue(bool(region.flags() & QGraphicsItem.ItemIsMovable))
        self.assertTrue(bool(region.flags() & QGraphicsItem.ItemIsSelectable))

    def test_clean_layer_replacement_keeps_old_frame_until_new_is_ready(self) -> None:
        canvas = CanvasView()
        image = QImage(200, 120, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        old_patch = {
            "x": 10, "y": 10,
            "pixels": np.full((20, 20, 3), 80, dtype=np.uint8),
            "mask": np.full((20, 20), 255, dtype=np.uint8),
        }
        canvas.apply_cleaning([old_patch])
        self.app.processEvents()
        self.assertEqual(len(canvas._clean_items), 1)
        old_item = canvas._clean_items[0]

        new_patch = {
            "x": 40, "y": 30,
            "pixels": np.full((20, 20, 3), 180, dtype=np.uint8),
            "mask": np.full((20, 20), 255, dtype=np.uint8),
        }
        canvas.apply_cleaning([new_patch], replace=True)
        self.assertIs(canvas._clean_items[0], old_item)
        self.assertTrue(old_item.isVisible())

        self.app.processEvents()
        self.assertEqual(len(canvas._clean_items), 1)
        self.assertIsNot(canvas._clean_items[0], old_item)
        self.assertTrue(canvas._clean_items[0].isVisible())

    def test_editable_mask_preview_can_be_replaced_and_cleared(self) -> None:
        canvas = CanvasView()
        image = QImage(200, 120, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        mask = np.zeros((30, 40), dtype=np.uint8)
        mask[8:22, 10:30] = 255

        canvas.show_mask_preview([{"x": 25, "y": 35, "mask": mask}])
        self.assertEqual(len(canvas._mask_preview_items), 1)
        self.assertEqual(int(canvas._mask_preview_items[0].x()), 25)
        self.assertEqual(int(canvas._mask_preview_items[0].y()), 35)

        canvas.show_mask_preview([])
        self.assertEqual(canvas._mask_preview_items, [])

    def test_interactive_zoom_uses_fast_rendering_then_restores_quality(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)

        canvas.set_zoom(120)
        self.assertEqual(canvas._zoom, 120)
        self.assertFalse(bool(canvas.renderHints() & QPainter.SmoothPixmapTransform))
        self.assertEqual(canvas._pixmap_item.shapeMode(), QGraphicsPixmapItem.BoundingRectShape)
        self.assertEqual(canvas._pixmap_item.transformationMode(), Qt.FastTransformation)
        canvas._finish_interactive_zoom()
        self.assertTrue(bool(canvas.renderHints() & QPainter.SmoothPixmapTransform))
        self.assertEqual(canvas._pixmap_item.transformationMode(), Qt.SmoothTransformation)

    def test_canvas_view_state_restores_zoom_and_page_center(self) -> None:
        canvas = CanvasView()
        canvas.resize(640, 480)
        image = QImage(1000, 3000, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        expected = {"scale": 0.8, "center_x": 500.0, "center_y": 1800.0}

        canvas.restore_view_state(expected)
        self.app.processEvents()
        restored = canvas.view_state()

        self.assertAlmostEqual(restored["scale"], expected["scale"], places=4)
        # Scroll bars use integer pixels, so mapping back through a fractional
        # transform can differ by a few scene pixels.
        self.assertAlmostEqual(restored["center_x"], expected["center_x"], delta=12.0)
        self.assertAlmostEqual(restored["center_y"], expected["center_y"], delta=12.0)

    def test_moving_region_reuses_existing_text_document(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        region = {"id": "one", "x": 40, "y": 50, "width": 120, "height": 70, "applied_text": "Texto"}
        canvas.set_regions([region])
        canvas.add_text(["Texto"], [region])
        original_item = canvas._text_items[0]

        canvas._regions[0].region.update({"x": 90, "y": 100})
        canvas._preview_region_geometry(canvas._regions[0])

        self.assertIs(canvas._text_items[0], original_item)
        self.assertGreaterEqual(canvas._text_items[0].x(), 90)

    def test_resizing_region_defers_expensive_text_rebuild_until_release(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 400, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {
            "id": "heavy", "x": 30, "y": 30, "width": 220, "height": 100,
            "applied_text": "Texto con trazo y resplandor",
            "style": {"stroke_width": 4, "glow_enabled": True, "glow_radius": 20},
        }
        canvas.set_regions([region]); canvas.add_text([region["applied_text"]], [region])
        original = canvas._text_items[0]
        canvas._regions[0].region.update({"width": 340, "height": 180})
        with patch("ui.canvas_view.detect_balloon_interior") as detector:
            for _ in range(100):
                canvas._preview_region_geometry(canvas._regions[0])
        self.assertIs(canvas._text_items[0], original)
        self.assertAlmostEqual(canvas._text_items[0].textWidth(), 324.0)
        detector.assert_not_called()

    def test_empty_region_skips_balloon_detection_and_effect_layout(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 400, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        region = {"id": "empty", "x": 20, "y": 20, "width": 300, "height": 200, "applied_text": ""}
        canvas.set_regions([region])
        with patch("ui.canvas_view.detect_balloon_interior") as detector:
            canvas.add_text([""], [region])
        detector.assert_not_called()

    def test_unchanged_text_layers_are_reused_instead_of_rebuilt(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 300, QImage.Format_RGB888); image.fill(255); canvas.set_image(image)
        regions = [
            {"id": "one", "x": 20, "y": 30, "width": 210, "height": 90, "applied_text": "Uno"},
            {"id": "two", "x": 260, "y": 30, "width": 210, "height": 90, "applied_text": "Dos"},
        ]
        canvas.set_regions(regions); canvas.add_text(["Uno", "Dos"], regions)
        first, second = canvas._text_items
        canvas.add_text(["Uno", "Dos"], regions)
        self.assertIs(canvas._text_items[0], first)
        self.assertIs(canvas._text_items[1], second)
        regions[1]["applied_text"] = "Dos modificado"
        canvas.add_text(["Uno", "Dos modificado"], regions)
        self.assertIs(canvas._text_items[0], first)
        self.assertIsNot(canvas._text_items[1], second)
        rebuilt_second = canvas._text_items[1]
        regions[0]["applied_text"] = "Uno modificado"
        canvas.update_text_layers([0], ["Uno modificado", "Dos modificado"], regions)
        self.assertIsNot(canvas._text_items[0], first)
        self.assertIs(canvas._text_items[1], rebuilt_second)

    def test_mask_brush_emits_image_local_compact_draft(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        canvas._pixmap_item.setPos(50, 70)
        canvas.set_brush_mode("mask", 20)
        received: list[dict] = []
        canvas.mask_stroke_committed.connect(received.append)

        canvas._begin_retouch(QPointF(150, 150), QPointF(10, 10))
        canvas._stroke_points.append(QPointF(145, 95))
        canvas._finish_retouch()

        self.assertEqual(len(received), 1)
        draft = received[0]
        self.assertNotIn("pixels", draft)
        self.assertLess(draft["x"], 100)
        self.assertLess(draft["y"], 80)
        self.assertLess(draft["mask"].shape[0], 80)
        self.assertLess(draft["mask"].shape[1], 90)

    def test_brush_preview_is_a_compact_raster_not_a_vector_path(self) -> None:
        canvas = CanvasView()
        image = QImage(500, 1800, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        canvas.set_brush_mode("paint", 32)

        canvas._begin_retouch(QPointF(220, 1200), QPointF(200, 300))
        canvas._stroke_points.extend([QPointF(225, 1204), QPointF(232, 1210)])
        canvas._update_raster_stroke_preview()

        preview = canvas._stroke_preview
        self.assertIsNotNone(preview)
        self.assertFalse(preview.pixmap().isNull())
        self.assertLess(preview.pixmap().height(), 80)
        self.assertGreater(preview.y(), 1150)
        canvas._finish_retouch()

    def test_stroke_engine_uses_only_circular_dabs(self) -> None:
        with patch("core.stroke_engine.cv2.line") as line:
            stroke = StrokeEngine.rasterize(
                [(20.0, 30.0), (80.0, 70.0)], 24, 200, 160,
            )
        self.assertIsNotNone(stroke)
        self.assertTrue(np.any(stroke.mask))
        line.assert_not_called()

    def test_hover_without_left_button_cannot_extend_active_brush_stroke(self) -> None:
        class HoverMove:
            accepted = False

            @staticmethod
            def position() -> QPointF:
                return QPointF(200, 5)

            @staticmethod
            def modifiers():
                return Qt.NoModifier

            @staticmethod
            def buttons():
                return Qt.NoButton

            def accept(self) -> None:
                self.accepted = True

        canvas = CanvasView()
        image = QImage(400, 600, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        canvas.set_brush_mode("paint", 30)
        received: list[dict] = []
        canvas.retouch_committed.connect(received.append)
        canvas._begin_retouch(QPointF(200, 500), QPointF(200, 200))

        event = HoverMove()
        canvas.mouseMoveEvent(event)

        self.assertTrue(event.accepted)
        self.assertIsNone(canvas._stroke_preview)
        self.assertEqual(len(received), 1)
        self.assertLess(received[0]["mask"].shape[0], 50)

    def test_every_brush_finishes_without_a_strip_when_cursor_leaves_image(self) -> None:
        class OutsideMove:
            accepted = False

            @staticmethod
            def position() -> QPointF:
                return QPointF(-500, -500)

            @staticmethod
            def modifiers():
                return Qt.NoModifier

            @staticmethod
            def buttons():
                return Qt.LeftButton

            def accept(self) -> None:
                self.accepted = True

        signal_names = {
            "paint": "retouch_committed",
            "restore": "retouch_committed",
            "mask": "mask_stroke_committed",
            "mask_erase": "mask_preview_erase_committed",
        }
        for mode, signal_name in signal_names.items():
            with self.subTest(mode=mode):
                canvas = CanvasView()
                image = QImage(400, 600, QImage.Format_RGB888)
                image.fill(120)
                canvas.set_image(image)
                canvas.set_brush_mode(mode, 30)
                received: list[dict] = []
                getattr(canvas, signal_name).connect(received.append)
                canvas._begin_retouch(QPointF(200, 500), QPointF(200, 200))

                event = OutsideMove()
                canvas.mouseMoveEvent(event)

                self.assertTrue(event.accepted)
                self.assertIsNone(canvas._stroke_preview)
                self.assertEqual(len(received), 1)
                self.assertLess(received[0]["mask"].shape[0], 60)
                self.assertFalse(StrokeEngine.is_suspicious_connector(received[0]["mask"]))

    def test_every_brush_discards_a_coordinate_jump_before_it_is_persisted(self) -> None:
        signal_names = {
            "paint": "retouch_committed",
            "restore": "retouch_committed",
            "mask": "mask_stroke_committed",
            "mask_erase": "mask_preview_erase_committed",
        }
        for mode, signal_name in signal_names.items():
            with self.subTest(mode=mode):
                canvas = CanvasView()
                image = QImage(500, 1400, QImage.Format_RGB888)
                image.fill(120)
                canvas.set_image(image)
                canvas.set_brush_mode(mode, 36)
                received: list[dict] = []
                getattr(canvas, signal_name).connect(received.append)
                canvas._begin_retouch(QPointF(240, 1100), QPointF(200, 300))
                # Simulate the historical remap: the source coordinate jumps
                # to the top while the physical pointer moved by one pixel.
                canvas._stroke_points.append(QPointF(240, 10))
                canvas._stroke_view_points.append(QPointF(201, 300))
                canvas._finish_retouch()

                self.assertEqual(received, [])

    def test_restore_brush_reads_original_pixels_not_cleaned_preview(self) -> None:
        canvas = CanvasView()
        image = QImage(120, 120, QImage.Format_RGB888)
        image.fill(Qt.red)
        canvas.set_image(image)
        blue = np.full((60, 60, 3), (0, 0, 255), dtype=np.uint8)
        canvas.apply_cleaning([{"x": 30, "y": 30, "pixels": blue}], replace=True)
        canvas.set_brush_mode("restore", 24)
        received: list[dict] = []
        canvas.retouch_committed.connect(received.append)

        canvas._begin_retouch(QPointF(60, 60), QPointF(60, 60))
        canvas._finish_retouch()

        self.assertEqual(len(received), 1)
        patch = received[0]
        self.assertEqual(patch["kind"], "restore")
        painted = patch["mask"] > 0
        self.assertTrue(np.all(patch["pixels"][painted] == np.array([255, 0, 0], dtype=np.uint8)))

    def test_manual_tool_buttons_switch_paint_mask_and_restore(self) -> None:
        panel = AIOptionsPanel()
        emitted: list[str] = []
        panel.action_requested.connect(emitted.append)

        panel.retouch_button.click()
        panel.sync_brush_mode("restore")
        panel.restore_button.click()  # turns restoration off
        panel.mask_brush_button.click()

        self.assertIn("retouch_on", emitted)
        self.assertIn("restore_off", emitted)
        self.assertIn("mask_brush_on", emitted)

    def test_deleting_canvas_region_keeps_selection_empty(self) -> None:
        canvas = CanvasView()
        image = QImage(400, 300, QImage.Format_RGB888)
        image.fill(0)
        canvas.set_image(image)
        canvas.set_regions([
            {"id": str(index), "x": 20 + index * 50, "y": 30, "width": 40, "height": 30}
            for index in range(4)
        ])
        canvas._regions[2].setSelected(True)

        canvas.delete_selected_regions()

        self.assertEqual([item.region["id"] for item in canvas._regions], ["0", "1", "3"])
        self.assertFalse(any(item.isSelected() for item in canvas._regions))

        canvas._delete_region_item(canvas._regions[2])
        self.assertEqual([item.region["id"] for item in canvas._regions], ["0", "1"])
        self.assertFalse(any(item.isSelected() for item in canvas._regions))

    def test_layers_can_refresh_after_deletion_without_selecting_a_fallback(self) -> None:
        panel = LayersPanel()
        original = [
            {"id": str(index), "x": 0, "y": index * 30, "width": 40, "height": 20}
            for index in range(3)
        ]
        panel.set_regions(original)
        panel.set_selected_indices([1], 1)

        panel.set_regions([original[0], original[2]], keep_empty_selection=True)

        self.assertEqual(panel.current_index(), -1)
        self.assertEqual(panel.selected_indices(), [])

        panel.set_selected_indices([0], 0)
        panel.set_selected_indices([], None)
        self.assertEqual(panel.current_index(), -1)
        self.assertEqual(panel.selected_indices(), [])

    def test_layer_content_refresh_reuses_visible_rows_lazily(self) -> None:
        panel = LayersPanel()
        regions = [
            {
                "id": str(index), "number": index + 1, "x": 0, "y": index * 30,
                "width": 80, "height": 20, "text": f"Texto {index}",
            }
            for index in range(40)
        ]
        panel.set_regions(regions)
        loaded_before = [
            panel.layers.itemWidget(panel.layers.item(index))
            for index in range(40)
            if panel.layers.itemWidget(panel.layers.item(index)) is not None
        ]
        self.assertGreater(len(loaded_before), 0)
        self.assertLess(len(loaded_before), len(regions))
        panel.set_selected_indices([17], 17)
        selected_row = panel.layers.itemWidget(panel.layers.item(17))

        regions[17]["x"] = 42
        regions[17]["width"] = 120
        regions[17]["text"] = "Texto actualizado"
        panel.set_regions(regions, "17")

        self.assertIs(selected_row, panel.layers.itemWidget(panel.layers.item(17)))
        self.assertEqual(panel.current_index(), 17)
        self.assertEqual(panel.selected_indices(), [17])
        self.assertEqual(selected_row.name_label.text(), "Texto actualizado")
        self.assertIn("120", selected_row.metadata.text())

    def test_large_layer_list_realizes_only_visible_rows(self) -> None:
        panel = LayersPanel()
        panel.resize(360, 760)
        panel.show()
        self.app.processEvents()
        regions = [
            {
                "id": str(index), "number": index + 1, "x": 0, "y": index * 30,
                "width": 100, "height": 24, "text": f"Texto {index}",
            }
            for index in range(1000)
        ]

        panel.set_regions(regions)
        self.app.processEvents()

        self.assertEqual(panel.layers.count(), 1000)
        self.assertLess(len(panel._realized_layer_rows), 40)
        panel.close()

    def test_canvas_reuses_boxes_when_layer_ids_do_not_change(self) -> None:
        canvas = CanvasView()
        image = QImage(600, 400, QImage.Format_RGB888)
        image.fill(255)
        canvas.set_image(image)
        regions = [
            {"id": str(index), "x": 20 + index * 40, "y": 30, "width": 100, "height": 60}
            for index in range(8)
        ]
        canvas.set_regions(regions)
        original_items = list(canvas._regions)
        updated = [{**region, "y": 90} for region in regions]

        canvas.set_regions(updated)

        self.assertEqual(canvas._regions, original_items)
        self.assertTrue(all(item.region["y"] == 90 for item in canvas._regions))

    def test_script_panel_initialization_and_card_sync(self) -> None:
        from ui.script_panel import ScriptPanel
        panel = ScriptPanel()
        regions = [
            {"id": "r1", "text": "HELLO WORLD", "translation": "HOLA MUNDO"},
            {"id": "r2", "text": "GOODBYE", "translation": "ADIOS"},
        ]
        panel.set_regions(regions, 0)
        self.assertEqual(len(panel._cards), 2)
        self.assertIn("r1", panel._cards)
        self.assertIn("r2", panel._cards)
        self.assertEqual(panel._cards["r1"].editor.toPlainText(), "HOLA MUNDO")

        # Test selecting region
        panel.select_region("r2")
        self.assertTrue(panel._cards["r2"].property("active"))
        self.assertFalse(panel._cards["r1"].property("active"))

    def test_canvas_clone_and_heal_modes(self) -> None:
        canvas = CanvasView()
        image = QImage(300, 300, QImage.Format_RGB888)
        image.fill(200)
        canvas.set_image(image)

        # Test setting clone mode
        canvas.set_brush_mode("clone", 20)
        self.assertEqual(canvas.brush_mode, "clone")
        self.assertEqual(canvas.brush_size, 20)

        # Test setting heal mode
        canvas.set_brush_mode("heal", 25)
        self.assertEqual(canvas.brush_mode, "heal")
        self.assertEqual(canvas.brush_size, 25)

        # Reset brush mode
        canvas.set_brush_mode(None)
        self.assertIsNone(canvas.brush_mode)


if __name__ == "__main__":
    unittest.main()
