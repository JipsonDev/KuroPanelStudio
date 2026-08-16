from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from core.balloon_typesetter import detect_balloon_interior, fit_balanced_text, line_intervals
from core.cleaning_manager import CleaningManager
from core.linguistic_composer import linguistic_wrap
from core.sfx_transform import build_warp
from core.text_layout import fit_rectangular_text, layout_signature
from core.project_io import load_project_bundle, save_project_bundle
from core.project_manager import Page
from core.performance_manager import PerformanceTracker, resource_policy


class ProfessionalTypesettingTests(unittest.TestCase):
    def test_low_resource_policy_caps_memory_and_parallelism(self) -> None:
        policy = resource_policy("low", 1024)
        self.assertEqual(policy.name, "low")
        self.assertLessEqual(policy.image_cache_mb, 128)
        self.assertEqual(policy.foreground_workers, 1)
        self.assertEqual(policy.io_workers, 1)
        self.assertFalse(policy.auto_warmup)
        self.assertLessEqual(policy.cleaning_cache_mb, 32)
        self.assertEqual(policy.page_cache_radius, 0)
        self.assertFalse(policy.retain_offscreen_text)

    def test_resource_profiles_define_distinct_page_working_sets(self) -> None:
        low = resource_policy("low", 2048)
        balanced = resource_policy("balanced", 2048)
        high = resource_policy("high", 2048)

        self.assertEqual((low.page_cache_radius, balanced.page_cache_radius, high.page_cache_radius), (0, 1, 2))
        self.assertLess(low.image_cache_mb, balanced.image_cache_mb)
        self.assertLess(balanced.image_cache_mb, high.image_cache_mb)

    def test_performance_tracker_keeps_rolling_averages(self) -> None:
        tracker = PerformanceTracker(history_limit=12)
        tracker.record("Zoom", 0.010)
        tracker.record("Zoom", 0.030)
        tracker.record("OCR", 1.5)

        summary = tracker.summary()

        self.assertAlmostEqual(summary["Zoom"].seconds, 0.020, places=4)
        self.assertEqual(summary["Zoom"].samples, 2)
        self.assertEqual(summary["OCR"].samples, 1)

    @staticmethod
    def _balloon(width: int = 300, height: int = 180) -> np.ndarray:
        image = np.full((height, width, 3), 30, np.uint8)
        cv2.ellipse(image, (width // 2, height // 2), (width // 2 - 12, height // 2 - 14), 0, 0, 360, (247, 247, 247), -1)
        cv2.putText(image, "TEXTO", (82, 102), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (15, 15, 15), 2, cv2.LINE_AA)
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def test_balloon_mask_respects_curved_border_and_per_line_width(self) -> None:
        mask = detect_balloon_interior(self._balloon(), padding=10)
        self.assertGreater(mask[90, 150], 0)
        self.assertEqual(mask[5, 5], 0)
        intervals = line_intervals(mask, 3, 30)
        self.assertEqual(len(intervals), 3)
        widths = [right - left for left, right in intervals]
        self.assertGreater(widths[1], widths[0])
        self.assertGreater(widths[1], widths[2])

    def test_balanced_fit_obeys_each_local_width(self) -> None:
        mask = detect_balloon_interior(self._balloon(260, 150), padding=12)
        layout = fit_balanced_text(
            "ESTE ES UN TEXTO EQUILIBRADO PARA EL GLOBO",
            mask, 28, 8, True,
            lambda size: size * 1.25,
            lambda size, value: len(value) * size * 0.55,
        )
        self.assertFalse(layout["overflow"])
        self.assertGreater(len(layout["lines"]), 1)
        for line, (left, right) in zip(layout["lines"], layout["intervals"]):
            self.assertLessEqual(len(line) * layout["font_size"] * 0.55, right - left)

    def test_unfittable_text_reports_overflow_when_auto_fit_is_disabled(self) -> None:
        mask = np.zeros((40, 80), np.uint8)
        mask[8:32, 12:68] = 255
        layout = fit_balanced_text(
            "ESTE TEXTO NO PUEDE CABER", mask, 42, 8, False,
            lambda size: size * 1.2,
            lambda size, value: len(value) * size,
        )
        self.assertTrue(layout["overflow"])

    def test_cjk_breaks_do_not_start_with_closing_punctuation(self) -> None:
        lines = linguistic_wrap(
            "你好，世界。今天很好！", [38, 38, 38, 38], lambda value: len(value) * 10,
            language="zh", orphan_control=True, hanging=True,
        )
        self.assertIsNotNone(lines)
        self.assertTrue(all(not line.startswith(("，", "。", "！")) for line in lines[1:]))

    def test_manual_line_breaks_are_never_joined_or_discarded(self) -> None:
        lines = linguistic_wrap(
            "PRIMER BLOQUE FIJO\nSEGUNDO BLOQUE FIJO",
            [95, 95, 95, 95], lambda value: len(value) * 8,
            language="es", hyphenate=False,
        )
        self.assertIsNotNone(lines)
        first_end = next(index for index, line in enumerate(lines) if "FIJO" in line)
        self.assertTrue(all("SEGUNDO" not in line for line in lines[:first_end + 1]))
        self.assertTrue(any("SEGUNDO" in line for line in lines[first_end + 1:]))

    def test_japanese_kinsoku_and_korean_nonstarters(self) -> None:
        japanese = linguistic_wrap(
            "これは「重要」です。次です！", [50] * 5,
            lambda value: len(value) * 10, language="ja",
        )
        korean = linguistic_wrap(
            "안녕하세요！반갑습니다。", [50] * 4,
            lambda value: len(value) * 10, language="ko",
        )
        self.assertIsNotNone(japanese); self.assertIsNotNone(korean)
        self.assertTrue(all(not line.startswith(("」", "。", "！")) for line in japanese[1:]))
        self.assertTrue(all(not line.startswith(("。", "！")) for line in korean[1:]))

    def test_shared_rectangle_layout_is_manual_by_default_and_signed(self) -> None:
        style = {"font_size": 30, "auto_fit": False, "language": "es"}
        signature = layout_signature("UNO DOS\nTRES", 160, 90, style)
        layout = fit_rectangular_text(
            "UNO DOS\nTRES", 140, 70, 30, 6, False,
            lambda size: size * 1.2,
            lambda size, value: len(value) * size * 0.5,
            language="es", signature=signature,
        )
        self.assertEqual(layout["font_size"], 30)
        self.assertEqual(layout["signature"], signature)
        self.assertIn("\n", layout["text"])

    def test_sfx_mesh_moves_only_the_requested_control_area(self) -> None:
        style = {
            "sfx_mesh_enabled": True,
            "sfx_mesh_r1c1_dx": 20,
            "sfx_mesh_r1c1_dy": -15,
        }
        warp = build_warp(style, 200, 100)
        self.assertEqual(tuple(round(value) for value in warp(0, 0)), (0, 0))
        self.assertEqual(tuple(round(value) for value in warp(1, 1)), (200, 100))
        self.assertEqual(tuple(round(value) for value in warp(0.5, 0.5)), (140, 35))

    def test_horizontal_compression_is_used_before_smaller_font(self) -> None:
        mask = np.zeros((75, 170), np.uint8); mask[5:70, 5:165] = 255
        layout = fit_balanced_text(
            "PALABRA LARGA", mask, 32, 8, True,
            lambda size: size * 1.2, lambda size, value: len(value) * size * 0.55,
            language="es", max_horizontal_compression=20, auto_scale=True,
        )
        self.assertFalse(layout["overflow"])
        self.assertGreaterEqual(layout["font_size"], 30)
        self.assertLessEqual(layout["scale_x"], 1.0)

    def test_gradient_guidance_restores_smooth_balloon_colour(self) -> None:
        height, width = 100, 180
        xx = np.linspace(225, 250, width, dtype=np.float32)[None, :, None]
        original = np.repeat(xx, height, axis=0)
        original = np.repeat(original, 3, axis=2).astype(np.uint8)
        mask = np.zeros((height, width), np.uint8); mask[42:58, 45:135] = 255
        damaged = original.copy(); damaged[mask > 0] = 180
        guided, used = CleaningManager._gradient_guided_restoration(original, damaged, mask)
        self.assertTrue(used)
        self.assertLess(float(np.abs(guided.astype(np.int16) - original.astype(np.int16))[mask > 0].mean()), 1.5)

    def test_effect_presets_survive_project_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "page.png"
            cv2.imwrite(str(source), np.full((80, 120, 3), 255, np.uint8))
            page = Page("page.png", source, 120, 80, "1 KB")
            project = root / "sample.mseproj"
            effects = {"Grito propio": {"stroke2_enabled": True, "blend_mode": "overlay"}}
            save_project_bundle(project, [page], 0, {}, {}, {}, {}, {}, "", "", effects)
            restored = load_project_bundle(project)
            self.assertEqual(restored["effect_presets"], effects)


if __name__ == "__main__":
    unittest.main()
