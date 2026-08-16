from __future__ import annotations

import unittest

import numpy as np

from core.cleaning_manager import CleaningManager
from core.stroke_engine import StrokeEngine


class StrokeEngineTests(unittest.TestCase):
    def test_stroke_is_compact_round_and_antialiased(self) -> None:
        stroke = StrokeEngine.rasterize(
            [(100.0, 80.0), (160.0, 110.0)],
            diameter=20,
            image_width=1000,
            image_height=2000,
        )

        self.assertIsNotNone(stroke)
        assert stroke is not None
        self.assertLess(stroke.width, 100)
        self.assertLess(stroke.height, 70)
        self.assertEqual(stroke.mask.dtype, np.uint8)
        self.assertEqual(int(stroke.mask.max()), 255)
        self.assertTrue(np.any((stroke.mask > 0) & (stroke.mask < 255)))

    def test_color_brush_changes_only_the_stroke(self) -> None:
        source = np.full((40, 60, 3), 25, dtype=np.uint8)
        mask = np.zeros((40, 60), dtype=np.uint8)
        mask[12:28, 20:40] = 255

        result = StrokeEngine.paint(source, mask, (200, 100, 50))

        np.testing.assert_array_equal(result[mask == 0], source[mask == 0])
        self.assertTrue(np.all(result[mask == 255] == np.array([200, 100, 50], dtype=np.uint8)))

    def test_viewport_jump_starts_a_new_stroke_segment(self) -> None:
        stroke = StrokeEngine.rasterize(
            [(80.0, 80.0), (100.0, 90.0), None, (105.0, 700.0), (120.0, 710.0)],
            diameter=18,
            image_width=500,
            image_height=1000,
        )

        self.assertIsNotNone(stroke)
        assert stroke is not None
        # There must be no vertical bridge between the two independent marks.
        bridge_y = 350 - stroke.y
        self.assertEqual(np.count_nonzero(stroke.mask[bridge_y:bridge_y + 20]), 0)

    def test_implicit_viewport_jump_discards_the_teleported_tail(self) -> None:
        stroke = StrokeEngine.rasterize(
            [(80.0, 80.0), (100.0, 90.0), (105.0, 12_000.0), (120.0, 12_010.0)],
            diameter=18,
            image_width=500,
            image_height=60_000,
        )

        self.assertIsNotNone(stroke)
        assert stroke is not None
        self.assertLess(stroke.height, 80)
        self.assertLess(stroke.y, 100)

    def test_trusted_low_zoom_stroke_has_no_interspaced_gaps(self) -> None:
        stroke = StrokeEngine.rasterize(
            [(30.0, 60.0), (970.0, 60.0)],
            diameter=12,
            image_width=1000,
            image_height=120,
            trusted_continuous=True,
        )

        self.assertIsNotNone(stroke)
        assert stroke is not None
        coverage = np.any(stroke.mask > 8, axis=0)
        painted = np.flatnonzero(coverage)
        self.assertGreater(painted.size, 900)
        self.assertTrue(np.all(coverage[painted[0]:painted[-1] + 1]))

    def test_historical_connectors_are_detected_without_flagging_filled_retouches(self) -> None:
        vertical = np.full((900, 42), 255, dtype=np.uint8)
        diagonal = np.zeros((700, 700), dtype=np.uint8)
        for position in range(80, 620):
            diagonal[position, position - 40:position + 40] = 255
        filled = np.full((700, 700), 255, dtype=np.uint8)

        self.assertTrue(StrokeEngine.is_suspicious_connector(vertical))
        self.assertTrue(StrokeEngine.is_suspicious_connector(diagonal))
        self.assertFalse(StrokeEngine.is_suspicious_connector(filled))

    def test_mask_eraser_changes_only_the_overlapping_mask_pixels(self) -> None:
        entry_mask = np.full((80, 100), 255, dtype=np.uint8)
        entries = [{"x": 50, "y": 70, "mask": entry_mask}]
        stroke = StrokeEngine.rasterize(
            [(90.0, 100.0), (110.0, 100.0)], 18, 400, 500, padding=2
        )

        self.assertIsNotNone(stroke)
        assert stroke is not None
        before = entry_mask.copy()
        changed = StrokeEngine.erase_mask_entries(entries, stroke)

        self.assertTrue(changed)
        self.assertLess(np.count_nonzero(entry_mask), np.count_nonzero(before))
        # The eraser must not manufacture a connector toward the page edge.
        changed_pixels = np.where(entry_mask != before, 255, 0).astype(np.uint8)
        self.assertFalse(StrokeEngine.is_suspicious_connector(changed_pixels))
        np.testing.assert_array_equal(entry_mask[:15], before[:15])

    def test_mask_eraser_ignores_non_overlapping_entries(self) -> None:
        entry_mask = np.full((30, 30), 255, dtype=np.uint8)
        entries = [{"x": 200, "y": 200, "mask": entry_mask}]
        stroke = StrokeEngine.rasterize([(20.0, 20.0)], 16, 400, 500)

        self.assertIsNotNone(stroke)
        assert stroke is not None
        self.assertFalse(StrokeEngine.erase_mask_entries(entries, stroke))
        self.assertTrue(np.all(entry_mask == 255))

    def test_lama_brush_composites_only_inside_user_mask(self) -> None:
        manager = CleaningManager.__new__(CleaningManager)
        source = np.full((48, 64, 3), (80, 120, 160), dtype=np.uint8)
        mask = np.zeros((48, 64), dtype=np.uint8)
        mask[14:34, 22:42] = 255
        source[mask > 0] = (12, 12, 12)
        restored = np.full_like(source, (80, 120, 160))
        manager._lama = lambda rgb, exact: restored.copy()  # type: ignore[method-assign]

        result = manager.clean_mask(source, mask)

        np.testing.assert_array_equal(result[mask == 0], source[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == np.array([80, 120, 160], dtype=np.uint8)))

    def test_lama_brush_rejects_page_sized_accidental_mask(self) -> None:
        manager = CleaningManager.__new__(CleaningManager)
        source = np.full((5000, 32, 3), 255, dtype=np.uint8)
        mask = np.full((5000, 32), 255, dtype=np.uint8)
        manager._lama = lambda rgb, exact: self.fail("LaMa no debe recibir una tira accidental")  # type: ignore[method-assign]

        with self.assertRaisesRegex(ValueError, "salto demasiado grande"):
            manager.clean_mask(source, mask)


if __name__ == "__main__":
    unittest.main()
