from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from core.retouch_layers import consolidate_layer, layer_counts, patch_history
from core.cleaning_manager import CleaningManager
from core.image_manager import ImageManager
from core.project_manager import Page
from ui.canvas_view import CanvasView
from ui.cleaning_quality_dialog import CleaningQualityDialog
from ui.images_panel import ImagesPanel
from ui.layers_panel import LayersPanel


class RetouchQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _patch(x: int, y: int, colour, kind: str, size: int = 12) -> dict:
        return {
            "id": f"{kind}-{x}-{y}", "x": x, "y": y, "kind": kind,
            "pixels": np.full((size, size, 3), colour, dtype=np.uint8),
            "mask": np.full((size, size), 255, dtype=np.uint8),
        }

    def test_professional_layers_count_and_consolidate_patches(self) -> None:
        patches = [
            self._patch(5, 7, (255, 0, 0), "manual"),
            self._patch(24, 7, (0, 255, 0), "manual"),
            self._patch(10, 35, (255, 255, 255), "automatic"),
            self._patch(30, 35, (10, 20, 30), "restore"),
        ]

        self.assertEqual(layer_counts(patches), {"automatic": 1, "paint": 2, "restore": 1})
        consolidated, merged = consolidate_layer(patches, "paint")

        self.assertEqual(merged, 2)
        self.assertEqual(layer_counts(consolidated), {"automatic": 1, "paint": 1, "restore": 1})
        merged_patch = next(patch for patch in consolidated if patch.get("kind") == "manual")
        self.assertEqual(merged_patch["consolidated"], 2)
        self.assertGreater(np.count_nonzero(merged_patch["mask"]), 250)

    def test_retouch_layers_have_independent_visibility_opacity_and_history(self) -> None:
        panel = LayersPanel()
        history = patch_history([
            {**self._patch(0, 0, (1, 2, 3), "automatic"), "qc_status": "attention"},
            self._patch(15, 0, (4, 5, 6), "manual"),
        ])
        panel.set_image_layers(
            True, True, {}, {"automatic": 1, "paint": 1, "restore": 0},
            {"automatic": {"visible": True, "opacity": 70, "locked": True}}, history,
        )

        self.assertTrue(panel.retouch_rows["automatic"].available)
        self.assertEqual(panel.retouch_rows["automatic"].opacity.value(), 70)
        self.assertTrue(panel.retouch_rows["automatic"].locked)
        self.assertFalse(panel.retouch_rows["restore"].available)
        self.assertFalse(hasattr(panel, "retouch_history"))

        # Empty locked layers must remain unlockable.
        panel.set_image_layers(
            True, False, {}, {"automatic": 0, "paint": 0, "restore": 0},
            {"paint": {"visible": True, "opacity": 100, "locked": True}}, [],
        )
        self.assertTrue(panel.retouch_rows["paint"].lock_button.isEnabled())

    def test_page_list_does_not_decode_or_render_thumbnails(self) -> None:
        pages = [Page(f"{index:03}.png") for index in range(180)]
        panel = ImagesPanel(pages, ImageManager())
        requested: list[tuple[int, int]] = []
        panel.thumbnail_range_requested.connect(lambda first, last: requested.append((first, last)))
        panel.resize(320, 520)
        panel.show()
        self.app.processEvents()
        panel.request_visible_thumbnails()

        self.assertEqual(requested, [])
        first_row = panel.list.itemWidget(panel.list.item(0))
        self.assertFalse(hasattr(first_row, "thumbnail"))
        panel.close()

    def test_progressive_page_preview_keeps_original_scene_geometry_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "long_page.jpg"
            Image.fromarray(np.full((2400, 800, 3), 125, dtype=np.uint8)).save(path, quality=90)
            manager = ImageManager(memory_limit_mb=96)

            self.assertIsNone(manager.cached_active_image(path))
            preview = manager.preview_image(path, 200)
            self.assertIsNotNone(preview)
            assert preview is not None
            self.assertEqual((preview.width(), preview.height()), (200, 320))

            canvas = CanvasView()
            canvas.set_loading_preview(preview, (800, 2400))
            self.assertTrue(canvas._image_preview_only)
            self.assertAlmostEqual(canvas.scene.sceneRect().width(), 800, delta=1)
            self.assertAlmostEqual(canvas.scene.sceneRect().height(), 2400, delta=1)

            full = manager.active_image(path)
            self.assertIsNotNone(full)
            self.assertIs(manager.cached_active_image(path), full)
            canvas.set_image(full, (800, 2400))
            self.assertFalse(canvas._image_preview_only)

    def test_prefetch_keeps_current_fast_path_and_caches_neighbor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first_path = Path(temp) / "01.png"
            second_path = Path(temp) / "02.png"
            Image.new("RGB", (120, 180), (20, 30, 40)).save(first_path)
            Image.new("RGB", (120, 180), (50, 60, 70)).save(second_path)
            manager = ImageManager(memory_limit_mb=64)
            current = manager.active_image(first_path)

            neighbor = manager.prefetch_image(second_path)

            self.assertIsNotNone(neighbor)
            self.assertIs(manager.cached_active_image(second_path), neighbor)
            self.assertIs(manager.active_image(first_path), current)

    def test_page_working_set_releases_decoded_images_outside_profile_radius(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / f"{index:02}.png" for index in range(3)]
            for index, path in enumerate(paths):
                Image.new("RGB", (120, 180), (20 + index, 30, 40)).save(path)
            manager = ImageManager(memory_limit_mb=64)
            manager.active_image(paths[1])
            manager.prefetch_image(paths[0])
            manager.prefetch_image(paths[2])

            removed = manager.retain_pages({paths[1]})

            self.assertEqual(removed, 2)
            self.assertIsNotNone(manager.cached_active_image(paths[1]))
            self.assertIsNone(manager.cached_active_image(paths[0]))
            self.assertIsNone(manager.cached_active_image(paths[2]))

    def test_canvas_toggles_only_requested_retouch_layer(self) -> None:
        canvas = CanvasView()
        image = QImage(100, 100, QImage.Format_RGB888)
        image.fill(Qt.white)
        canvas.set_image(image)
        canvas.apply_cleaning([
            self._patch(5, 5, (255, 0, 0), "automatic"),
            self._patch(25, 5, (0, 255, 0), "manual"),
            self._patch(45, 5, (0, 0, 255), "restore"),
        ], replace=True)
        for _ in range(5):
            self.app.processEvents()

        canvas.set_retouch_layer_state("paint", visible=False)
        visibility = {str(item.data(20)): item.isVisible() for item in canvas._clean_items}

        self.assertTrue(visibility["automatic"])
        self.assertNotIn("paint", visibility)
        self.assertTrue(visibility["restore"])
        canvas.set_retouch_layer_state("paint", visible=True)
        for _ in range(3):
            self.app.processEvents()
        self.assertTrue(any(str(item.data(20)) == "paint" for item in canvas._clean_items))

    def test_quality_dialog_renders_original_mask_result_and_accepts_box(self) -> None:
        original = QImage(220, 140, QImage.Format_RGB888)
        original.fill(Qt.white)
        mask = np.zeros((70, 140), dtype=np.uint8)
        mask[25:45, 35:105] = 255
        residual = np.zeros_like(mask)
        residual[32:36, 62:70] = 255
        patch = {
            "id": "quality-1", "kind": "automatic", "x": 40, "y": 35,
            "pixels": np.full((70, 140, 3), 250, dtype=np.uint8), "mask": mask,
            "target": {"x": 40, "y": 35, "width": 140, "height": 70},
            "residual_mask": residual, "residual_pixels": int(np.count_nonzero(residual)),
            "qc_status": "attention",
        }
        dialog = CleaningQualityDialog(original, {"patches": [patch]}, None)
        accepted: list[str] = []
        dialog.accepted_patch.connect(accepted.append)
        dialog.show()
        self.app.processEvents()

        self.assertFalse(dialog.original_view[1].pixmap().isNull())
        self.assertFalse(dialog.mask_view[1].pixmap().isNull())
        self.assertFalse(dialog.result_view[1].pixmap().isNull())
        dialog.accept_button.click()
        self.assertEqual(accepted, ["quality-1"])
        self.assertEqual(patch["qc_status"], "accepted")
        dialog.close()

    def test_complete_chapter_quality_suite_covers_normal_pointed_and_transparent_balloons(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pages: list[Path] = []
            for page_index in range(3):
                yy, xx = np.mgrid[:220, :420]
                if page_index == 2:
                    image = np.stack((
                        80 + (xx % 55), 105 + (yy % 45), 135 + ((xx + yy) % 35),
                    ), axis=2).astype(np.uint8)
                    overlay = image.copy()
                    cv2.ellipse(overlay, (210, 110), (170, 78), 0, 0, 360, (235, 235, 242), -1)
                    image = cv2.addWeighted(overlay, 0.58, image, 0.42, 0)
                else:
                    image = np.full((220, 420, 3), (28, 31, 36), dtype=np.uint8)
                    cv2.ellipse(image, (210, 110), (170, 78), 0, 0, 360, (252, 252, 252), -1)
                if page_index == 1:
                    for angle in np.linspace(0, 2 * np.pi, 72, endpoint=False):
                        inner = np.asarray((210, 110)) + np.asarray((np.cos(angle) * 158, np.sin(angle) * 72))
                        outer = np.asarray((210, 110)) + np.asarray((np.cos(angle) * 190, np.sin(angle) * 92))
                        cv2.line(image, tuple(inner.astype(int)), tuple(outer.astype(int)), (170, 45, 42), 3)
                cv2.putText(image, "TEXT", (130, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (5, 5, 5), 5, cv2.LINE_AA)
                path = folder / f"{page_index + 1:02}.png"
                Image.fromarray(image).save(path)
                pages.append(path)

            manager = CleaningManager(folder)

            def detector(crop: np.ndarray) -> np.ndarray:
                mask = np.zeros(crop.shape[:2], dtype=np.uint8)
                height, width = mask.shape
                mask[height // 3:height * 2 // 3, width // 4:width * 3 // 4] = 255
                return mask

            def lama(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
                output = crop.copy()
                fill = np.median(crop[mask == 0], axis=0).astype(np.uint8)
                output[mask > 0] = fill
                return output

            manager._text_mask = detector  # type: ignore[method-assign]
            manager._lama = lama  # type: ignore[method-assign]
            manager._residual_mask = lambda cleaned, text_mask, selection: np.zeros_like(text_mask)  # type: ignore[method-assign]
            results = [
                manager.clean(
                    path, [{"x": 35, "y": 25, "width": 350, "height": 170}],
                    lambda _value: None, lambda: False,
                )
                for path in pages
            ]

            self.assertEqual(len(results), 3)
            for result in results:
                self.assertEqual(len(result["patches"]), 1)
                patch = result["patches"][0]
                self.assertIn(patch["qc_status"], {"pending", "attention"})
                self.assertIn("residual_mask", patch)
                self.assertEqual(np.count_nonzero(patch["mask"][:8]), 0)
                self.assertEqual(np.count_nonzero(patch["mask"][-8:]), 0)


if __name__ == "__main__":
    unittest.main()
