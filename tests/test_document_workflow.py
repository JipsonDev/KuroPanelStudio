from __future__ import annotations

import base64
import copy
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2
from PIL import Image, ImageDraw

from core.cleaning_manager import CleaningManager
from core.detection_manager import DetectionManager, TextRegion, _OnnxTextDetector
from core.export_manager import compose_page, export_page
from core.font_profile_manager import FontProfileManager
from core.history_manager import HistoryManager
from core.ocr_manager import ALIBABA_COMPATIBLE_URL, OCRManager, OCRNetworkError
from core.project_io import load_project_bundle, page_key, save_project_bundle
from core.project_manager import Page, ProjectManager
from core.psd_manager import inspect_psd, render_psd
from core.psd_sync import (
    file_signature, inspect_stable_psd, inspect_stable_raster,
    inspect_stable_source, reconcile_layer_states,
)
from core.performance_manager import ByteLRUCache, adaptive_gpu_batch, cuda_memory_mb, normalize_device_mode
from core.recovery_manager import RecoveryManager
from core.settings_manager import SettingsManager
from core.sfx_layout import automatic_sfx_lines
from core.text_transfer import (
    format_chapter_sections, format_numbered_entries,
    parse_chapter_sections, parse_numbered_entries, strip_numeric_prefix,
)
from core.translation_manager import (
    DEEPSEEK_TRANSLATION_URL, DEFAULT_TRANSLATION_PROMPT, LEGACY_TRANSLATION_PROMPT, TranslationManager,
    glossary_to_text, normalize_glossary,
)
from core.typography_manager import TypographyManager
from core.workflow_status import page_workflow_status
from core.watermark_manager import automatic_watermark_count, compose_watermark, prepare_watermark, watermark_positions


class DocumentWorkflowTests(unittest.TestCase):
    def test_internal_ocr_box_markers_never_become_visible_text(self) -> None:
        self.assertEqual(
            OCRManager.normalize_cjk_text("MSE_BOX_002\r\nä½ å¥½ä¸–ç•Œ"),
            "ä½ å¥½ä¸–ç•Œ",
        )
        self.assertEqual(OCRManager.normalize_cjk_text("MSE-BOX-17"), "")

        manager = TranslationManager()
        manager._cache_put(("marker",), {
            "translations": ["<<<MSE_BOX_0002>>>\r\nTexto traducido"],
            "terms": [],
        })
        self.assertEqual(
            manager._cache_get(("marker",))["translations"],
            ["Texto traducido"],
        )

    def test_sfx_auto_layout_matches_box_shape_and_preserves_manual_breaks(self) -> None:
        measure = lambda value: len(value) * 10.0

        square = automatic_sfx_lines("UNO DOS TRES CUATRO", 180, 180, measure, 30, "es")
        wide = automatic_sfx_lines("UNO DOS TRES CUATRO", 600, 80, measure, 30, "es")
        manual = automatic_sfx_lines("BOOM\nCRASH", 600, 80, measure, 30, "es")

        self.assertGreater(len(square), 1)
        self.assertEqual(wide, ["UNO DOS TRES CUATRO"])
        self.assertEqual(manual, ["BOOM", "CRASH"])

    def test_legacy_alternative_fonts_are_removed_from_normalized_styles(self) -> None:
        normalized = TypographyManager.normalized({"font_family": "Arial", "fallback_fonts": "Otra"})

        self.assertEqual(normalized["font_family"], "Arial")
        self.assertNotIn("fallback_fonts", normalized)

    def test_watermark_preferences_are_flushed_and_reloadable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = SettingsManager(path)
            settings.update_watermark({
                "active": True, "scale_percent": 23, "opacity": 47,
                "repeat": True, "auto_count": False, "repeat_count": 5,
                "anchor": "middle-right", "margin_x": 31, "margin_y": 44,
            })

            restored = SettingsManager(path).data["watermark"]

            self.assertTrue(restored["active"])
            self.assertEqual(restored["scale_percent"], 23)
            self.assertEqual(restored["opacity"], 47)
            self.assertEqual(restored["repeat_count"], 5)
            self.assertEqual(restored["anchor"], "middle-right")
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_watermark_geometry_respects_ratio_anchor_and_margins(self) -> None:
        buffer = BytesIO()
        Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(buffer, "PNG")
        settings = {
            "png_bytes": buffer.getvalue(), "size_mode": "percent", "scale_percent": 20,
            "anchor": "bottom-right", "margin_x": 30, "margin_y": 40,
        }
        mark = prepare_watermark((1000, 800), settings)
        self.assertEqual(mark.size, (200, 100))
        self.assertEqual(watermark_positions((1000, 800), mark.size, settings), [(770, 660)])

    def test_repeated_watermark_uses_one_vertical_column_and_page_height(self) -> None:
        settings = {
            "repeat": True, "anchor": "top-right", "margin_x": 20, "margin_y": 30,
            "auto_count": False, "repeat_count": 4, "seam_safe": False,
        }
        positions = watermark_positions((800, 1000), (100, 50), settings)
        self.assertEqual({x for x, _y in positions}, {680})
        self.assertEqual([y for _x, y in positions], [30, 327, 623, 920])
        self.assertEqual(watermark_positions((800, 1000), (100, 50), {**settings, "positions": []}), [])

    def test_vertical_distribution_reserves_space_across_page_seams(self) -> None:
        settings = {
            "repeat": True, "auto_count": False, "repeat_count": 2,
            "anchor": "top-center", "margin_y": 0, "seam_safe": True,
        }
        positions = watermark_positions((800, 3000), (100, 50), settings)
        first_y, last_y = positions[0][1], positions[-1][1]
        seam_gap = (3000 - (last_y + 50)) + first_y
        self.assertGreaterEqual(seam_gap, 200)

    def test_automatic_watermark_count_is_conservative_for_long_pages(self) -> None:
        self.assertEqual(automatic_watermark_count((800, 1200), (120, 60)), 1)
        self.assertEqual(automatic_watermark_count((800, 6000), (120, 60)), 3)
        self.assertEqual(automatic_watermark_count((800, 60000), (120, 60)), 12)

    def test_automatic_watermarks_move_away_from_detected_text_regions(self) -> None:
        settings = {
            "repeat": True, "auto_count": False, "repeat_count": 3,
            "anchor": "top-center", "avoid_text": True,
            "avoid_regions": [{"x": 250, "y": 1300, "width": 300, "height": 400}],
        }
        positions = watermark_positions((800, 3000), (100, 50), settings)
        self.assertEqual(len(positions), 3)
        self.assertFalse(any(1250 < y < 1750 for _x, y in positions))

    def test_watermark_composition_is_non_destructive_outside_mark(self) -> None:
        buffer = BytesIO()
        Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(buffer, "PNG")
        page = Image.new("RGB", (100, 80), "white")
        applied = compose_watermark(page, {
            "png_bytes": buffer.getvalue(), "size_mode": "pixels", "width_px": 20,
            "anchor": "top-left", "margin_x": 5, "margin_y": 7, "opacity": 50,
        })
        self.assertTrue(applied)
        self.assertEqual(page.getpixel((0, 0)), (255, 255, 255))
        marked = page.getpixel((6, 8))
        self.assertEqual(marked[0], 255)
        self.assertLess(marked[1], 255)

    def test_project_embeds_watermark_asset_for_portable_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "page.png"
            Image.new("RGB", (80, 60), "white").save(source)
            page = Page("page.png", source, 80, 60, "0.0 MB")
            buffer = BytesIO()
            Image.new("RGBA", (12, 6), (0, 0, 0, 255)).save(buffer, "PNG")
            project = folder / "portable.mseproj"
            settings = {"png_bytes": buffer.getvalue(), "source_path": "Z:/missing/logo.png", "enabled_pages": ["page.png"]}
            settings["page_positions"] = {"page.png": [[7, 11]]}
            save_project_bundle(project, [page], 0, {}, {}, {}, {}, watermark_settings=settings)
            restored = load_project_bundle(project)["watermark_settings"]
            self.assertEqual(restored["enabled_pages"], ["page.png"])
            self.assertEqual(restored["source_path"], "")
            self.assertGreater(len(restored["png_bytes"]), 0)
            self.assertEqual(restored["page_positions"]["page.png"], [[7, 11]])

    def test_performance_policy_adapts_batch_to_free_vram(self) -> None:
        self.assertEqual(adaptive_gpu_batch(900)[0], 1)
        self.assertEqual(adaptive_gpu_batch(2500)[0], 1)
        self.assertEqual(adaptive_gpu_batch(5000)[0], 2)
        self.assertEqual(adaptive_gpu_batch(9000)[0], 6)
        self.assertEqual(normalize_device_mode("GPU"), "gpu")
        self.assertEqual(normalize_device_mode("invalid"), "auto")

    def test_cuda_memory_uses_driver_query_without_importing_torch(self) -> None:
        with patch("subprocess.check_output", return_value="7000, 8188\n") as query:
            self.assertEqual(cuda_memory_mb(), (7000, 8188))
        query.assert_called_once()

    def test_byte_cache_evicts_by_memory_and_records_hits(self) -> None:
        cache = ByteLRUCache[str](10)
        cache.put("a", "A", 6)
        cache.put("b", "B", 6)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), "B")
        self.assertEqual(cache.stats()["bytes"], 6)
        self.assertEqual(cache.stats()["hits"], 1)

    def test_psd_layers_are_detected_and_composited_non_destructively(self) -> None:
        from psd_tools import PSDImage

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "chapter.psd"
            psd = PSDImage.new("RGB", (80, 60), color=(255, 255, 255))
            psd.create_pixel_layer(
                Image.new("RGBA", (20, 20), (255, 0, 0, 255)),
                name="Diálogo", left=5, top=5,
            )
            psd.create_pixel_layer(
                Image.new("RGBA", (20, 20), (0, 0, 255, 255)),
                name="Arte", left=40, top=20,
            )
            psd.save(source)
            original_bytes = source.read_bytes()

            width, height, layers = inspect_psd(source)
            self.assertEqual((width, height), (80, 60))
            self.assertEqual([layer["name"] for layer in layers], ["Diálogo", "Arte"])
            hidden = render_psd(source, {"1": {"visible": False}})
            self.assertEqual(hidden.getpixel((45, 25)), (255, 255, 255))
            self.assertEqual(source.read_bytes(), original_bytes)

            scanned = ProjectManager.scan_folder(Path(temp))
            self.assertEqual(len(scanned), 1)
            self.assertEqual(scanned[0].source_layers[0]["kind"], "pixel")
            project = Path(temp) / "psd.mseproj"
            key = str(source)
            save_project_bundle(
                project, scanned, 0, {}, {}, {},
                {key: {"source_layers": {"1": {"visible": False}}}},
            )
            restored = load_project_bundle(project)
            self.assertEqual(restored["pages"][0].source_layers[1]["name"], "Arte")
            self.assertFalse(restored["page_styles"][key]["source_layers"]["1"]["visible"])

    def test_psd_sync_waits_for_a_stable_file_and_reinspects_layers(self) -> None:
        from psd_tools import PSDImage

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "live.psd"
            psd = PSDImage.new("RGB", (96, 72), color=(255, 255, 255))
            psd.create_pixel_layer(
                Image.new("RGBA", (96, 72), (220, 30, 50, 255)), name="Diálogo"
            )
            psd.save(source)

            result = inspect_stable_psd(source, attempts=3, interval=0.02)

            self.assertEqual((result["width"], result["height"]), (96, 72))
            self.assertEqual(result["layers"][0]["name"], "Diálogo")
            self.assertEqual(result["signature"], file_signature(source))

    def test_raster_sync_waits_for_a_complete_photoshop_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "pagina.png"
            Image.new("RGB", (120, 260), (255, 255, 255)).save(source)

            result = inspect_stable_raster(source, attempts=3, interval=0.02)
            generic = inspect_stable_source(source, attempts=3, interval=0.02)

            self.assertEqual((result["width"], result["height"]), (120, 260))
            self.assertIsNone(result["layers"])
            self.assertEqual(generic["signature"], file_signature(source))

    def test_psd_sync_keeps_overrides_when_layers_are_reordered(self) -> None:
        old_layers = [
            {"id": "0", "name": "Fondo", "kind": "pixel", "depth": 0, "group": False},
            {"id": "1", "name": "Texto", "kind": "type", "depth": 0, "group": False},
        ]
        new_layers = [
            {"id": "0", "name": "Nueva", "kind": "pixel", "depth": 0, "group": False},
            {"id": "1", "name": "Fondo", "kind": "pixel", "depth": 0, "group": False},
            {"id": "2", "name": "Texto", "kind": "type", "depth": 0, "group": False},
        ]

        states = reconcile_layer_states(
            old_layers, new_layers,
            {"0": {"opacity": 55}, "1": {"visible": False}},
        )

        self.assertEqual(states["1"]["opacity"], 55)
        self.assertFalse(states["2"]["visible"])
        self.assertNotIn("0", states)

    def test_raster_metadata_scan_does_not_decode_full_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "long-page.png"
            Image.new("RGB", (800, 6000), (245, 245, 245)).save(source)

            with patch("PIL.Image.Image.load", side_effect=AssertionError("pixel decode")):
                page = ProjectManager._page_from_path(source)

            self.assertEqual((page.width, page.height), (800, 6000))
            self.assertEqual(page.path, source)

    def test_ocr_parallelism_adapts_without_overloading_provider(self) -> None:
        manager = OCRManager()
        self.assertEqual(manager.parallelism, 3)
        for _ in range(4):
            manager._observe_latency(2.0)
        self.assertEqual(manager.parallelism, 4)
        manager._observe_latency(30.0)
        self.assertEqual(manager.parallelism, 2)

    def test_detection_warmup_pays_first_prediction_cost_once(self) -> None:
        class FakeModel:
            def __init__(self) -> None:
                self.calls = 0

            def predict(self, *_args, **_kwargs):
                self.calls += 1
                return []

        model = FakeModel()
        manager = DetectionManager(Path("models"))
        manager._device = "cpu"
        with patch.object(manager, "_load_model", return_value=model):
            manager.warm_up()
            manager.warm_up()
        self.assertEqual(model.calls, 1)

    def test_detector_matches_reference_editor_geometry_and_thresholds(self) -> None:
        manager = DetectionManager(Path("models"))
        self.assertEqual(manager.confidence, 0.25)
        self.assertEqual((manager.tile_height, manager.tile_overlap), (2000, 300))

        stronger = TextRegion(10, 10, 100, 80, 0.90)
        contained = TextRegion(20, 20, 60, 40, 0.70)
        self.assertEqual(manager._deduplicate([contained, stronger]), [stronger])

        first = TextRegion(20, 100, 180, 40, 0.80)
        second = TextRegion(100, 144, 160, 45, 0.75)
        self.assertTrue(manager._are_adjacent_lines(first, second))
        second.y = 146
        self.assertFalse(manager._are_adjacent_lines(first, second))

    def test_onnx_detector_batches_rgb_strips_without_channel_inversion(self) -> None:
        captured: dict[str, np.ndarray] = {}

        class FakeSession:
            def run(self, _outputs, inputs):
                tensor = inputs["images"]
                captured["tensor"] = tensor.copy()
                return [np.zeros((tensor.shape[0], 5, 1), dtype=np.float32)]

        detector = _OnnxTextDetector.__new__(_OnnxTextDetector)
        detector.session = FakeSession()
        detector.input_name = "images"
        detector.size = 2
        detector.cv2 = cv2
        detector.uses_cuda = False
        rgb = np.full((2, 2, 3), (10, 20, 30), dtype=np.uint8)

        result = detector.predict_boxes([rgb, rgb], confidence=0.25, iou=0.70)

        self.assertEqual(result, [[], []])
        self.assertEqual(captured["tensor"].shape, (2, 3, 2, 2))
        np.testing.assert_allclose(
            captured["tensor"][0, :, 0, 0], np.array([10, 20, 30]) / 255.0,
            rtol=0, atol=1e-6,
        )

    def test_onnx_detector_preloads_cuda_before_session_creation(self) -> None:
        class FakeSession:
            def get_inputs(self):
                return [type("Input", (), {"name": "images"})()]

            def get_providers(self):
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        with (
            patch("core.detection_manager.register_cuda_dll_directories"),
            patch("core.detection_manager.preload_onnx_cuda") as preload,
            patch("onnxruntime.get_available_providers", return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]),
            patch("onnxruntime.InferenceSession", return_value=FakeSession()),
        ):
            detector = _OnnxTextDetector(Path("Models/yolo.onnx"), "auto", 2)

        preload.assert_called_once()
        self.assertTrue(detector.uses_cuda)

    def test_cleaning_warmup_runs_both_networks_once(self) -> None:
        manager = CleaningManager(Path("models"))
        calls: list[str] = []
        with (
            patch.object(manager, "_get_ocr_session", return_value=object()),
            patch.object(manager, "_get_lama_session", return_value=object()),
            patch.object(manager, "_text_mask", side_effect=lambda _rgb: calls.append("ocr")),
            patch.object(manager, "_lama", side_effect=lambda _rgb, _mask: calls.append("lama")),
        ):
            manager.warm_up()
            manager.warm_up()
        self.assertEqual(calls, ["ocr", "lama"])

    def test_cleaning_text_masks_share_one_dynamic_onnx_batch(self) -> None:
        calls: list[tuple[int, int, int, int]] = []

        class FakeSession:
            def get_providers(self):
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

            def get_inputs(self):
                return [type("Input", (), {"name": "image"})()]

            def run(self, _outputs, inputs):
                tensor = inputs["image"]
                calls.append(tuple(tensor.shape))
                heatmaps = np.zeros((tensor.shape[0], 1, tensor.shape[2], tensor.shape[3]), dtype=np.float32)
                heatmaps[:, :, 20:34, 24:42] = 0.95
                return [heatmaps]

        manager = CleaningManager(Path("models"))
        crops = [
            np.full((150 + index * 3, 210 + index * 2, 3), 255, dtype=np.uint8)
            for index in range(6)
        ]
        with (
            patch.object(manager, "_get_ocr_session", return_value=FakeSession()),
            patch("core.cleaning_manager.cuda_memory_mb", return_value=(8192, 12288)),
        ):
            masks = manager._text_mask_many(crops)
            batch_calls = list(calls)
            calls.clear()
            individual_masks = [manager._text_mask(crop) for crop in crops]

        self.assertEqual(len(batch_calls), 1)
        self.assertEqual(batch_calls[0][0], 6)
        self.assertEqual(len(calls), 6)
        self.assertEqual([mask.shape for mask in masks], [crop.shape[:2] for crop in crops])
        self.assertTrue(all(np.count_nonzero(mask) > 0 for mask in masks))
        for batched, individual in zip(masks, individual_masks):
            np.testing.assert_array_equal(batched, individual)

    def test_cleaning_residual_retries_share_one_lama_batch(self) -> None:
        manager = CleaningManager.__new__(CleaningManager)
        calls: list[int] = []

        def fake_many(jobs):
            calls.append(len(jobs))
            restored = []
            for image, mask in jobs:
                result = image.copy()
                result[mask > 0] = 255
                restored.append(result)
            return restored

        manager._lama_many = fake_many  # type: ignore[method-assign]
        jobs = []
        for offset in (0, 12):
            cleaned = np.full((180, 260, 3), 255, dtype=np.uint8)
            text_mask = np.zeros((180, 260), dtype=np.uint8)
            cv2.rectangle(text_mask, (80, 65), (180, 115), 255, -1)
            cleaned[82 + offset:88 + offset, 118:145] = 225
            jobs.append((cleaned, text_mask, np.full_like(text_mask, 255)))

        refined = manager._refine_lama_residuals_many(jobs, max_passes=1)

        self.assertEqual(calls, [2])
        self.assertEqual([passes for _, passes in refined], [1, 1])
        self.assertTrue(all(np.count_nonzero(image < 250) == 0 for image, _ in refined))

    def test_ocr_joins_visual_cjk_lines_but_preserves_paragraphs(self) -> None:
        chinese = "虽 然 不 能\n全部看懂，\n但这是同一句。\n\n第二段。"
        japanese = "これは\nテストです。"
        korean = "이것은\n테스트입니다."

        self.assertEqual(
            OCRManager.normalize_cjk_text(chinese),
            "虽然不能全部看懂，但这是同一句。\n\n第二段。",
        )
        self.assertEqual(OCRManager.normalize_cjk_text(japanese), "これはテストです。")
        self.assertEqual(OCRManager.normalize_cjk_text(korean), "이것은 테스트입니다.")

    def test_numbered_text_roundtrip_preserves_box_order(self) -> None:
        copied = format_numbered_entries([
            (1, "Primer diálogo"),
            (2, "Segunda línea\ndel mismo globo"),
        ])
        self.assertEqual(
            parse_numbered_entries(copied),
            ["Primer diálogo", "Segunda línea del mismo globo"],
        )

    def test_chapter_clipboard_sections_map_text_by_page(self) -> None:
        copied = format_chapter_sections([
            ("01.png", "1. Uno\n2. Dos"),
            ("02.png", "1. Tres"),
        ])
        self.assertEqual(parse_chapter_sections(copied), {
            "01.png": ["Uno", "Dos"],
            "02.png": ["Tres"],
        })

    def test_pasted_box_numbers_are_never_part_of_dialogue(self) -> None:
        variants = ["1. Hola", "#04 Hola", "4) Hola", "[4] Hola", "4 - Hola", "4: Hola"]
        self.assertEqual([strip_numeric_prefix(value) for value in variants], ["Hola"] * len(variants))
        self.assertEqual(parse_numbered_entries("#01 Uno\n#02 Dos"), ["Uno", "Dos"])

    def test_translation_batch_accepts_numbering_and_separated_paragraphs(self) -> None:
        self.assertEqual(
            parse_numbered_entries("1. Primera línea\ncontinuación\n2) Segunda\n[3] Tercera"),
            ["Primera línea\ncontinuación", "Segunda", "Tercera"],
        )
        self.assertEqual(
            parse_numbered_entries("Primer párrafo\ncon salto manual\n\nSegundo párrafo"),
            ["Primer párrafo\ncon salto manual", "Segundo párrafo"],
        )
        self.assertEqual(parse_numbered_entries("1\nHola\n2\nAdiós"), ["Hola", "Adiós"])
        self.assertEqual(parse_numbered_entries("1. 2 kilos\n2. 3 días"), ["2 kilos", "3 días"])

    def test_ocr_regions_are_cached_after_parallel_api_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            page = Image.new("RGB", (360, 180), (255, 255, 255))
            draw = ImageDraw.Draw(page)
            regions = []
            expected = []
            for index, tone in enumerate((40, 80, 120, 160)):
                region = {
                    "id": str(index), "x": 20 + index * 82, "y": 45,
                    "width": 58, "height": 42,
                }
                regions.append(region)
                expected.append(f"tono {tone}")
                draw.rectangle(
                    (region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"]),
                    fill=(tone, tone, tone),
                )
            page.save(source)
            calls: list[str] = []
            requests_seen: list[tuple[str, dict]] = []

            class FakeResponse:
                status_code = 200

                def __init__(self, content: str) -> None:
                    self.content = content

                def json(self):
                    return {"choices": [{"message": {"content": self.content}}]}

            class FakeSession:
                def post(self, url, **kwargs):
                    from time import sleep

                    calls.append(kwargs["json"]["model"])
                    requests_seen.append((url, kwargs))
                    content = kwargs["json"]["messages"][0]["content"]
                    image_url = next(item["image_url"]["url"] for item in content if item.get("type") == "image_url")
                    with Image.open(BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as crop:
                        tone = int(round(crop.getpixel((crop.width // 2, crop.height // 2))[0] / 40.0) * 40)
                    # Deliberately finish darker crops later to prove that
                    # completion order never changes region ownership.
                    sleep((200 - tone) / 20_000)
                    return FakeResponse(f"tono {tone}")
            manager = OCRManager()
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                first = manager.run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )
                second = manager.run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(len(calls), 4)
            self.assertEqual(set(calls), {"qwen-vl-ocr"})
            self.assertTrue(all(url == ALIBABA_COMPATIBLE_URL for url, _ in requests_seen))
            for _, request in requests_seen:
                content = request["json"]["messages"][0]["content"]
                images = [item for item in content if item.get("type") == "image_url"]
                self.assertEqual(len(images), 1)
                self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(first, second)
            self.assertEqual([item["id"] for item in first], [item["id"] for item in regions])
            self.assertEqual([item["text"] for item in first], expected)

    def test_forced_single_box_ocr_bypasses_cached_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.new("RGB", (160, 100), (255, 255, 255)).save(source)
            calls = 0

            class FakeResponse:
                status_code = 200

                def __init__(self, content: str) -> None:
                    self.content = content

                def json(self):
                    return {"choices": [{"message": {"content": self.content}}]}

            class FakeSession:
                def post(self, _url, **_kwargs):
                    nonlocal calls
                    calls += 1
                    return FakeResponse(f"lectura {calls}")

            region = {"id": "1", "x": 20, "y": 20, "width": 90, "height": 40}
            manager = OCRManager()
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                first = manager.run_regions(
                    source, [region], "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )
                refreshed = manager.run_regions(
                    source, [region], "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False, force_refresh=True,
                )

            self.assertEqual(calls, 2)
            self.assertEqual(first[0]["text"], "lectura 1")
            self.assertEqual(refreshed[0]["text"], "lectura 2")

    def test_ocr_single_crop_transport_does_not_prompt_begin_or_box_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            page = Image.new("RGB", (360, 180), (255, 255, 255))
            draw = ImageDraw.Draw(page)
            draw.text((28, 50), "texto uno", fill=(0, 0, 0))
            draw.text((200, 50), "texto dos", fill=(0, 0, 0))
            page.save(source)
            seen_prompts: list[str] = []
            seen_images: list[bytes] = []

            class FakeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "texto"}}]}

            class FakeSession:
                def post(self, _url, **kwargs):
                    content = kwargs["json"]["messages"][0]["content"]
                    seen_images.extend([
                        base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
                        for item in content if item.get("type") == "image_url"
                    ])
                    seen_prompts.append(next(item["text"] for item in content if item.get("type") == "text"))
                    return FakeResponse()

            regions = [
                {"id": "1", "x": 20, "y": 40, "width": 120, "height": 60},
                {"id": "2", "x": 190, "y": 40, "width": 130, "height": 60},
            ]
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                OCRManager().run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(len(seen_prompts), 2)
            for prompt in seen_prompts:
                self.assertNotRegex(prompt, r"(?i)\bBEGIN\b|\bEND\b|BOX\s*###")
            self.assertEqual(len(seen_images), 2)
            with Image.open(BytesIO(seen_images[0])) as first, Image.open(BytesIO(seen_images[1])) as second:
                self.assertNotEqual(first.size, (0, 0))
                self.assertNotEqual(second.size, (0, 0))

    def test_overlapping_ocr_boxes_are_sent_as_independent_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            page = Image.new("RGB", (320, 220), (255, 255, 255))
            draw = ImageDraw.Draw(page)
            draw.text((70, 80), "inner", fill=(0, 0, 0))
            page.save(source)
            image_counts: list[int] = []

            class FakeResponse:
                status_code = 200

                def __init__(self, content: str) -> None:
                    self.content = content

                def json(self):
                    return {"choices": [{"message": {"content": self.content}}]}

            class FakeSession:
                def post(self, _url, **kwargs):
                    content = kwargs["json"]["messages"][0]["content"]
                    images = [item for item in content if item.get("type") == "image_url"]
                    image_counts.append(len(images))
                    payload = base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])
                    with Image.open(BytesIO(payload)) as crop:
                        label = "globo completo" if crop.height > 150 else "línea interior"
                    return FakeResponse(label)

            regions = [
                {"id": "outer", "x": 30, "y": 35, "width": 250, "height": 150},
                {"id": "inner", "x": 65, "y": 70, "width": 100, "height": 45},
            ]
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                result = OCRManager().run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(sorted(image_counts), [1, 1])
            self.assertEqual([item["text"] for item in result], ["globo completo", "línea interior"])

    def test_ocr_uses_custom_model_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.fromarray(np.full((80, 120, 3), 255, dtype=np.uint8)).save(source)
            received: list[str] = []

            class FakeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "texto"}}]}

            class FakeSession:
                def post(self, _url, **kwargs):
                    received.append(kwargs["json"]["model"])
                    return FakeResponse()

            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                OCRManager().run_regions(
                    source, [{"id": "1", "x": 10, "y": 10, "width": 50, "height": 30}],
                    "Alibaba Cloud", "mi-modelo-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(received, ["mi-modelo-ocr"])

    def test_ocr_never_groups_unrelated_regions_in_one_provider_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.new("RGB", (420, 180), (255, 255, 255)).save(source)
            calls = 0
            images_per_call: list[int] = []

            class FakeResponse:
                status_code = 200

                def __init__(self, content: str) -> None:
                    self.content = content

                def json(self):
                    return {"choices": [{"message": {"content": self.content}}]}

            class FakeSession:
                def post(self, _url, **kwargs):
                    nonlocal calls
                    calls += 1
                    content = kwargs["json"]["messages"][0]["content"]
                    images_per_call.append(sum(item.get("type") == "image_url" for item in content))
                    return FakeResponse("lectura independiente")

            regions = [
                {"id": str(index), "x": 20 + index * 110, "y": 50, "width": 90, "height": 45}
                for index in range(3)
            ]
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                result = OCRManager().run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(calls, 3)
            self.assertEqual(images_per_call, [1, 1, 1])
            self.assertEqual([item["text"] for item in result], ["lectura independiente"] * 3)

    def test_ocr_empty_coloured_crop_does_not_trigger_another_paid_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            page = Image.new("RGB", (300, 140), (255, 255, 255))
            draw = ImageDraw.Draw(page)
            draw.rectangle((30, 45, 115, 78), fill=(195, 50, 90))
            draw.rectangle((180, 45, 265, 78), fill=(20, 20, 20))
            page.save(source)
            calls = 0
            colour_attempts = 0

            class FakeResponse:
                status_code = 200

                def __init__(self, content: str) -> None:
                    self.content = content

                def json(self):
                    return {"choices": [{"message": {"content": self.content}}]}

            class FakeSession:
                def post(self, _url, **kwargs):
                    nonlocal calls, colour_attempts
                    calls += 1
                    content = kwargs["json"]["messages"][0]["content"]
                    image_url = next(
                        item["image_url"]["url"] for item in content
                        if item.get("type") == "image_url"
                    )
                    payload = base64.b64decode(image_url.split(",", 1)[1])
                    with Image.open(BytesIO(payload)) as image:
                        is_colour_crop = image.width < 210
                    if is_colour_crop:
                        colour_attempts += 1
                        return FakeResponse("" if colour_attempts == 1 else "color recuperado")
                    return FakeResponse("negro")

            regions = [
                {"id": "1", "x": 25, "y": 38, "width": 80, "height": 50},
                {"id": "2", "x": 175, "y": 38, "width": 100, "height": 50},
            ]
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                result = OCRManager().run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(calls, 2)
            self.assertEqual([item["text"] for item in result], ["", "negro"])

    def test_long_page_ocr_keeps_every_region_independent_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "long-page.png"
            Image.new("RGB", (600, 4200), (255, 255, 255)).save(source)
            calls = 0

            class FakeResponse:
                status_code = 200

                def __init__(self, content: str) -> None:
                    self.content = content

                def json(self):
                    return {"choices": [{"message": {"content": self.content}}]}

            class FakeSession:
                def post(self, _url, **kwargs):
                    nonlocal calls
                    calls += 1
                    request_content = kwargs["json"]["messages"][0]["content"]
                    assert sum(item.get("type") == "image_url" for item in request_content) == 1
                    return FakeResponse("texto")

            regions = [
                {"id": str(index + 1), "x": 20, "y": index * 90, "width": 360, "height": 64}
                for index in range(42)
            ]
            with patch("core.ocr_manager.requests.Session", return_value=FakeSession()):
                result = OCRManager().run_regions(
                    source, regions, "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )

            self.assertEqual(calls, 42)
            self.assertEqual([item["id"] for item in result], [item["id"] for item in regions])
            self.assertEqual([item["text"] for item in result], ["texto"] * 42)

    def test_ocr_windows_10054_waits_for_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.fromarray(np.full((80, 120, 3), 255, dtype=np.uint8)).save(source)
            attempts = 0

            class FakeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "recuperado"}}]}

            class FakeSession:
                def post(self, _url, **_kwargs):
                    nonlocal attempts
                    attempts += 1
                    if attempts < 2:
                        raise ConnectionResetError(10054, "remote host closed the connection")
                    return FakeResponse()

            with (
                patch("core.ocr_manager.requests.Session", return_value=FakeSession()),
                patch("core.ocr_manager.time.sleep", return_value=None),
            ):
                manager = OCRManager()
                args = (
                    source, [{"id": "1", "x": 10, "y": 10, "width": 50, "height": 30}],
                    "Alibaba Cloud", "qwen-vl-ocr", "key",
                    lambda _value: None, lambda: False,
                )
                with self.assertRaises(OCRNetworkError):
                    manager.run_regions(*args)
                self.assertEqual(attempts, 1)
                result = manager.run_regions(*args)

            self.assertEqual(attempts, 2)
            self.assertEqual(result[0]["text"], "recuperado")

    def test_page_workflow_status_tracks_each_production_stage(self) -> None:
        regions = [{
            "id": "r1", "text": "原文", "translation": "Texto", "applied_text": "Texto",
            "style": {"font_family": "Segoe UI"}, "translation_completed": True,
        }]
        status = page_workflow_status(regions, {"patches": [{"id": "p1"}]})
        self.assertEqual(status, {
            "detected": True, "ocr": True, "cleaned": True,
            "translated": True, "typeset": True,
        })

    def test_manhwa_font_profiles_support_named_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp) / "Manhwas")
            project_type = manager.create_type("Manhwas")
            project = manager.create_project(project_type, "Valiente")
            manager.set_font(project_type, project, "Diálogo", "Segoe UI")
            manager.set_font(project_type, project, "Gritos", "Arial")
            manager.set_font(project_type, project, "Narrador", "Georgia", previous_alias="Diálogo")
            self.assertEqual(manager.project_types(), ["Manhwas"])
            self.assertEqual(manager.projects(project_type), ["Valiente"])
            self.assertEqual(set(manager.font_entries(project_type, project)), {"Gritos", "Narrador"})
            self.assertEqual(manager.font_entries(project_type, project)["Narrador"]["family"], "Georgia")

    def test_font_profile_keeps_style_and_never_overwrites_another_role_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_project("Manhwas", "Valiente")
            manager.set_font(
                "Manhwas", "Valiente", "Diálogo", "Segoe UI",
                style={"font_weight": 700, "italic": True, "text_case": "upper"},
            )
            manager.set_font("Manhwas", "Valiente", "Gritos", "Impact")

            dialogue = manager.font_entries("Manhwas", "Valiente")["Diálogo"]
            self.assertEqual(dialogue["style"]["font_weight"], 700)
            self.assertTrue(dialogue["style"]["italic"])
            self.assertEqual(dialogue["style"]["text_case"], "upper")
            with self.assertRaises(ValueError):
                manager.set_font(
                    "Manhwas", "Valiente", "Gritos", "Arial",
                    previous_alias="Diálogo",
                )
            self.assertEqual(
                set(manager.font_entries("Manhwas", "Valiente")),
                {"Diálogo", "Gritos"},
            )

    def test_dialogue_role_is_resolved_and_applied_to_unstyled_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Mangas")
            manager.create_project("Mangas", "Prueba")
            manager.set_font("Mangas", "Prueba", "DIÁLOGO", "Comic Sans Test")
            alias, entry = manager.dialogue_font("Mangas", "Prueba")
            regions = [
                {"id": "new"},
                {"id": "manual", "style": {"font_family": "Fuente manual"}},
                {"id": "sfx", "font_role": "Gritos", "style": {"font_family": "Impact"}},
            ]

            TypographyManager.assign_font_role_defaults(
                regions, alias, entry["family"], "dialogue.ttf",
                {"font_weight": 700, "italic": True},
            )

            self.assertEqual(regions[0]["font_role"], "DIÁLOGO")
            self.assertEqual(regions[0]["style"]["font_family"], "Comic Sans Test")
            self.assertEqual(regions[0]["style"]["font_file"], "dialogue.ttf")
            self.assertEqual(regions[0]["style"]["font_weight"], 700)
            self.assertTrue(regions[0]["style"]["italic"])
            self.assertEqual(regions[1]["style"]["font_family"], "Fuente manual")
            self.assertEqual(regions[2]["font_role"], "Gritos")

    def test_manual_project_folder_is_loaded_as_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp) / "Manhwas")
            (manager.root / "Manhwas" / "Valiente").mkdir(parents=True)
            self.assertEqual(manager.projects("Manhwas"), ["Valiente"])
            manager.set_font("Manhwas", "Valiente", "Diálogo", "Segoe UI")
            self.assertEqual(manager.font_entries("Manhwas", "Valiente")["Diálogo"]["family"], "Segoe UI")

    def test_flat_profiles_migrate_to_internal_project_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "old"
            (legacy / "Valiente").mkdir(parents=True)
            (legacy / "Valiente" / "profile.json").write_text(
                '{"name":"Valiente","fonts":{"Diálogo":{"family":"Segoe UI","file":""}}}',
                encoding="utf-8",
            )
            manager = FontProfileManager(root / "internal")
            self.assertEqual(manager.import_legacy_root(legacy), ["Valiente"])
            self.assertEqual(manager.projects("Manhwas"), ["Valiente"])
            self.assertEqual(manager.font_entries("Manhwas", "Valiente")["Diálogo"]["family"], "Segoe UI")

    def test_project_types_and_projects_can_be_renamed_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp) / "profiles")
            project_type = manager.create_type("Manhwas")
            project = manager.create_project(project_type, "Valiente")
            manager.set_font(project_type, project, "Diálogo", "Segoe UI")

            project = manager.rename_project(project_type, project, "Valiente Plus")
            project_type = manager.rename_type(project_type, "Manhuas")
            self.assertTrue(manager.has_project(project_type, project))
            self.assertEqual(manager.font_entries(project_type, project)["Diálogo"]["family"], "Segoe UI")

            manager.delete_project(project_type, project)
            self.assertEqual(manager.projects(project_type), [])
            manager.delete_type(project_type)
            self.assertEqual(manager.project_types(), [])

    def test_reading_order_supports_manga_direction(self) -> None:
        regions = [
            {"id": "left", "x": 10, "y": 12, "width": 20, "height": 20},
            {"id": "right", "x": 90, "y": 10, "width": 20, "height": 20},
            {"id": "bottom", "x": 50, "y": 80, "width": 20, "height": 20},
        ]
        self.assertEqual([item["id"] for item in TypographyManager.ordered(regions)], ["left", "right", "bottom"])
        self.assertEqual([item["id"] for item in TypographyManager.ordered(regions, True)], ["right", "left", "bottom"])

    def test_typography_supports_weight_and_emphasis_variants(self) -> None:
        migrated = TypographyManager.normalized({"bold": True, "italic": True})
        self.assertEqual(migrated["font_weight"], 700)
        self.assertTrue(migrated["italic"])
        self.assertNotIn("bold", migrated)

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.new("RGB", (360, 160), "white").save(source)
            page = Page("page.png", source, 360, 160, "1 KB")
            base = {
                "x": 10, "y": 10, "width": 340, "height": 140,
                "applied_text": "Texto de prueba",
                "style": {"font_family": "Arial", "font_size": 42, "auto_fit": False},
            }
            regular = np.asarray(compose_page(page, [base], None, None))
            decorated_region = copy.deepcopy(base)
            decorated_region["style"].update({
                "font_weight": 700, "italic": True, "underline": True, "strikeout": True,
            })
            decorated = np.asarray(compose_page(page, [decorated_region], None, None))
            self.assertGreater(np.count_nonzero(decorated < 245), np.count_nonzero(regular < 245))

    def test_text_effects_render_in_full_resolution_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "effects.png"
            Image.new("RGB", (520, 240), "white").save(source)
            page = Page("effects.png", source, 520, 240, "1 KB")
            region = {
                "x": 40, "y": 35, "width": 440, "height": 170,
                "applied_text": "EFECTOS",
                "style": {
                    "font_family": "Arial", "font_size": 68, "auto_fit": False,
                    "stroke_width": 4, "stroke_color": "#111111",
                    "gradient_enabled": True, "gradient_start": "#FF2040",
                    "gradient_end": "#2040FF", "gradient_angle": 0,
                    "glow_enabled": True, "glow_color": "#00FF80",
                    "glow_radius": 12, "glow_opacity": 80,
                },
            }
            rendered = np.asarray(compose_page(page, [region], None, None))
            coloured = rendered[np.any(rendered < 245, axis=2)]
            self.assertGreater(len(coloured), 1000)
            self.assertGreater(len(np.unique(coloured.reshape(-1, 3), axis=0)), 40)

    def test_typography_normalizes_case_transform(self) -> None:
        self.assertEqual(TypographyManager.normalized({"text_case": "upper"})["text_case"], "upper")
        self.assertEqual(TypographyManager.normalized({"text_case": "lower"})["text_case"], "lower")
        self.assertEqual(TypographyManager.normalized({"text_case": "invalid"})["text_case"], "original")

    def test_translation_adapter_preserves_gemini_array_order(self) -> None:
        manager = TranslationManager()
        manager._request = lambda *_args, **_kwargs: {"candidates": [{"content": {"parts": [{"text": '["uno", "dos"]'}]}}]}  # type: ignore[method-assign]
        result = manager.translate(["一", "二"], "Gemini", "gemini-2.5-flash", "test", "ZH", "ES")
        self.assertEqual(result, ["uno", "dos"])

    def test_deepseek_translation_uses_project_context_and_json_mode(self) -> None:
        manager = TranslationManager()
        captured: list[tuple[str, dict, dict]] = []

        def request(url, payload, headers, **_kwargs):
            captured.append((url, payload, headers))
            return {"choices": [{"message": {"content": json.dumps({
                "translations": ["Lin Feng llegó."],
                "terms": [{
                    "source": "林枫", "target": "Lin Feng",
                    "category": "personaje", "note": "Protagonista",
                }],
            }, ensure_ascii=False)}}]}

        manager._request = request  # type: ignore[method-assign]
        result = manager.translate_with_context(
            ["林枫来了"], "DeepSeek", "deepseek-chat", "sk-test", "ZH", "ES",
            [], DEFAULT_TRANSLATION_PROMPT, "Contexto del capítulo anterior",
        )

        self.assertEqual(result["translations"], ["Lin Feng llegó."])
        self.assertEqual(result["terms"][0]["target"], "Lin Feng")
        self.assertEqual(captured[0][0], DEEPSEEK_TRANSLATION_URL)
        self.assertEqual(captured[0][1]["model"], "deepseek-chat")
        self.assertEqual(captured[0][1]["response_format"], {"type": "json_object"})
        self.assertEqual(captured[0][2]["Authorization"], "Bearer sk-test")
        self.assertIn("manhua", captured[0][1]["messages"][1]["content"].lower())

    def test_translation_prompt_covers_manhwa_manhua_and_manga(self) -> None:
        prompt = TranslationManager._prompt(["原文"], "ZH", "ES", [], "", "")
        for medium in ("manhwa", "manhua", "manga"):
            self.assertIn(medium, prompt.lower())
            self.assertIn(medium, DEFAULT_TRANSLATION_PROMPT.lower())

    def test_alibaba_mt_uses_native_glossary_and_translation_memory(self) -> None:
        manager = TranslationManager()
        payloads: list[dict] = []

        def request(_url, payload, _headers, **_kwargs):
            payloads.append(payload)
            if payload["model"] == "qwen-plus":
                return {"choices": [{"message": {"content": json.dumps({"terms": [
                    {"source": "青云宗", "target": "Secta Nube Azul", "category": "organización"}
                ]}, ensure_ascii=False)}}]}
            source = payload["messages"][0]["content"]
            if "<<<MSE_BOX_" in source:
                matches = list(TranslationManager._BATCH_MARKER.finditer(source))
                translated = []
                for position, match in enumerate(matches):
                    end = matches[position + 1].start() if position + 1 < len(matches) else len(source)
                    translated.append(f"{match.group(0)}\nES:{source[match.end():end].strip()}")
                return {"choices": [{"message": {"content": "\n\n".join(translated)}}]}
            return {"choices": [{"message": {"content": f"ES:{source}"}}]}

        manager._request = request  # type: ignore[method-assign]
        result = manager.translate_with_context(
            ["甲", "乙"], "Alibaba Cloud", "qwen-mt-flash", "test", "ZH", "ES",
            [{"source": "宗主", "target": "líder de secta", "category": "título"}],
            DEFAULT_TRANSLATION_PROMPT, "旧文 → Texto anterior",
        )
        self.assertEqual(result["translations"], ["ES:甲", "ES:乙"])
        self.assertEqual(len(payloads), 2)
        options = next(payload["translation_options"] for payload in payloads if "translation_options" in payload)
        self.assertEqual(options["source_lang"], "zh")
        self.assertEqual(options["target_lang"], "es")
        self.assertEqual(options["terms"][0]["target"], "líder de secta")
        self.assertEqual(options["tm_list"][0]["target"], "Texto anterior")
        self.assertEqual(result["terms"][0]["target"], "Secta Nube Azul")

    def test_alibaba_translation_batches_and_caches_long_pages(self) -> None:
        manager = TranslationManager()
        payloads: list[dict] = []

        def request(_url, payload, _headers, **_kwargs):
            payloads.append(payload)
            if payload["model"] == "qwen-plus":
                return {"choices": [{"message": {"content": '{"terms":[]}'}}]}
            source = payload["messages"][0]["content"]
            matches = list(TranslationManager._BATCH_MARKER.finditer(source))
            translated = []
            for position, match in enumerate(matches):
                end = matches[position + 1].start() if position + 1 < len(matches) else len(source)
                translated.append(f"{match.group(0)}\nES:{source[match.end():end].strip()}")
            return {"choices": [{"message": {"content": "\n\n".join(translated)}}]}

        manager._request = request  # type: ignore[method-assign]
        texts = [f"Texto {index}" for index in range(40)]
        first = manager.translate_with_context(
            texts, "Alibaba Cloud", "qwen-mt-flash", "test", "ZH", "ES", [], "", "",
        )
        calls_after_first = len(payloads)
        second = manager.translate_with_context(
            texts, "Alibaba Cloud", "qwen-mt-flash", "test", "ZH", "ES", [], "", "",
        )

        self.assertEqual(calls_after_first, 3)  # 2 translation batches (since max_items=32) + one glossary pass
        self.assertEqual(len(payloads), calls_after_first)
        self.assertEqual(first, second)
        self.assertEqual(first["translations"], [f"ES:Texto {index}" for index in range(40)])

    def test_project_translation_profile_persists_glossary_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas")
            manager.create_project("Manhwas", "Valiente")
            manager.set_translation_profile(
                "Manhwas", "Valiente", "Conserva los honoríficos.",
                "林枫\tLin Feng\tpersonaje\tProtagonista\n天云城 | Ciudad Tianyun | lugar",
            )
            profile = manager.translation_profile("Manhwas", "Valiente")
            self.assertEqual(profile["prompt"], "Conserva los honoríficos.")
            self.assertEqual(len(profile["glossary"]), 2)
            self.assertEqual(profile["glossary"][0]["target"], "Lin Feng")

    def test_existing_default_prompt_is_upgraded_without_overwriting_custom_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas"); manager.create_project("Manhwas", "Viejo")
            manager.set_translation_profile("Manhwas", "Viejo", LEGACY_TRANSLATION_PROMPT, [])
            upgraded = manager.translation_profile("Manhwas", "Viejo")["prompt"]
            self.assertIn("manhua", upgraded.lower())
            self.assertIn("manga", upgraded.lower())
            manager.set_translation_profile("Manhwas", "Viejo", "Mi prompt personalizado", [])
            self.assertEqual(manager.translation_profile("Manhwas", "Viejo")["prompt"], "Mi prompt personalizado")

    def test_glossary_merge_never_overwrites_manual_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = FontProfileManager(Path(temp))
            manager.create_type("Manhwas"); manager.create_project("Manhwas", "Valiente")
            manager.set_translation_profile(
                "Manhwas", "Valiente", DEFAULT_TRANSLATION_PROMPT,
                [{"source": "林枫", "target": "Lin Feng", "category": "personaje"}],
            )
            merged = manager.merge_glossary("Manhwas", "Valiente", [
                {"source": "林枫", "target": "Bosque Lin", "category": "lugar"},
                {"source": "天云城", "target": "Ciudad Tianyun", "category": "lugar"},
            ])
            self.assertEqual(len(merged), 2)
            self.assertEqual(merged[0]["target"], "Lin Feng")

    def test_structured_translation_returns_new_project_terms(self) -> None:
        manager = TranslationManager()
        manager._request = lambda *_args, **_kwargs: {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "translations": ["Lin Feng llegó a Ciudad Tianyun."],
            "terms": [
                {"source": "林枫", "target": "Lin Feng", "category": "personaje", "note": "Protagonista"},
                {"source": "天云城", "target": "Ciudad Tianyun", "category": "lugar", "note": ""},
            ],
        }, ensure_ascii=False)}]}}]}
        result = manager.translate_with_context(
            ["林枫到了天云城"], "Gemini", "gemini-2.5-flash", "test", "ZH", "ES",
            [], DEFAULT_TRANSLATION_PROMPT, "Capítulo anterior: 林枫 partió.",
        )
        self.assertEqual(result["translations"][0], "Lin Feng llegó a Ciudad Tianyun.")
        self.assertEqual({term["category"] for term in result["terms"]}, {"personaje", "lugar"})

    def test_glossary_text_is_copy_paste_round_trip(self) -> None:
        entries = normalize_glossary("林枫\tLin Feng\tpersonaje\tProtagonista")
        self.assertEqual(normalize_glossary(glossary_to_text(entries)), entries)

    def test_glossary_accepts_common_free_form_formats(self) -> None:
        glossary = normalize_glossary(
            "- 林枫 = Lin Feng\n"
            "2. 天云城 → Ciudad Tianyun\n"
            "Kim Dokja,Kim Dokja,personaje,Protagonista\n"
            "Regression;Regresión;técnica\n"
            "Solo Leveling\n"
            "| Yoo Joonghyuk | Yoo Joonghyuk | personaje | |"
        )
        self.assertEqual(len(glossary), 6)
        self.assertEqual(glossary[0]["target"], "Lin Feng")
        self.assertEqual(glossary[1]["target"], "Ciudad Tianyun")
        self.assertEqual(glossary[4]["target"], "Solo Leveling")
        self.assertEqual(glossary[5]["source"], "Yoo Joonghyuk")

        mapped = normalize_glossary({"林枫": "Lin Feng", "天云城": {"translation": "Ciudad Tianyun"}})
        self.assertEqual([item["target"] for item in mapped], ["Lin Feng", "Ciudad Tianyun"])

    def test_recovery_rotates_incremental_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chapter.mseproj"
            source.write_bytes(b"project")
            recovery = RecoveryManager(root / "state", backup_limit=2)
            first = recovery.backup(source)
            second = recovery.backup(source)
            third = recovery.backup(source)
            self.assertIsNotNone(first)
            self.assertEqual(len(list(recovery.backup_root.glob("*.mseproj"))), 2)
            self.assertTrue(second.exists() or third.exists())

    def test_project_bundle_restores_reusable_typography_presets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "page.png"
            Image.new("RGB", (64, 64), "white").save(image_path)
            page = Page("page.png", image_path, 64, 64, "1 KB")
            project = root / "styles.mseproj"
            preset = {"Narrador": TypographyManager.normalized({"font_size": 28, "vertical_text": True})}
            save_project_bundle(project, [page], 0, {}, {}, {}, {}, preset, "Valiente", "Manhwas")
            restored = load_project_bundle(project)
            self.assertEqual(restored["style_presets"], preset)
            self.assertEqual(restored["project_profile"], "Valiente")
            self.assertEqual(restored["project_profile_type"], "Manhwas")

    def test_clean_uses_reference_mask_and_returns_only_user_box(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.new("RGB", (420, 320), (40, 80, 120)).save(source)
            manager = CleaningManager(Path(temp))
            seen: dict[str, np.ndarray] = {}

            def fake_text_mask(crop: np.ndarray) -> np.ndarray:
                mask = np.zeros(crop.shape[:2], dtype=np.uint8)
                mask[160:190, 170:230] = 255
                return mask

            def fake_lama(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
                seen["mask"] = mask.copy()
                result = crop.copy()
                result[mask > 0] = (12, 34, 56)
                return result

            manager._text_mask = fake_text_mask  # type: ignore[method-assign]
            manager._lama = fake_lama  # type: ignore[method-assign]
            result = manager.clean(
                source,
                [{"x": 100, "y": 80, "width": 180, "height": 140}],
                lambda value: None,
                lambda: False,
            )

            self.assertEqual(len(result["patches"]), 1)
            patch = result["patches"][0]
            self.assertEqual((patch["x"], patch["y"]), (100, 80))
            self.assertEqual(patch["pixels"].shape, (140, 180, 3))
            self.assertEqual(patch["mask"].shape, (140, 180))
            self.assertGreater(np.count_nonzero(patch["mask"]), 0)
            self.assertLess(np.count_nonzero(patch["mask"]), patch["mask"].size)
            self.assertGreater(np.count_nonzero(seen["mask"]), 0)

    def test_automatic_clean_always_uses_lama_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "balloon.png"
            original = np.full((280, 480, 3), 255, dtype=np.uint8)
            for y in range(55, 226, 14):
                cv2.line(original, (20, y), (85, y + 5), (20, 20, 20), 4)
                cv2.line(original, (460, y), (395, y + 5), (20, 20, 20), 4)
            Image.fromarray(original).save(source)
            manager = CleaningManager(Path(temp))
            tight = np.zeros((280, 480), dtype=np.uint8)
            cv2.putText(tight, "TEXT", (155, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 255, 8, cv2.LINE_AA)
            manager._text_mask = lambda crop: tight.copy()  # type: ignore[method-assign]
            seen: dict[str, np.ndarray | tuple[int, ...]] = {}
            def fake_lama(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
                seen["mask"] = mask.copy()
                seen["crop_shape"] = crop.shape
                # Simulate a model that also shifts pixels outside its mask.
                # The compositor must never allow that to deform the balloon.
                result = np.zeros_like(crop)
                result[mask > 0] = (255, 255, 255)
                return result
            manager._lama = fake_lama  # type: ignore[method-assign]

            result = manager.clean(
                source,
                [{"x": 0, "y": 0, "width": 480, "height": 280}],
                lambda value: None,
                lambda: False,
            )
            cleaned = result["patches"][0]["pixels"]

            # The detector is only a seed: post-processing may complete
            # partially detected glyph strokes, but it must not turn the
            # whole selection or the balloon rays into a mask.
            model_mask = seen["mask"]
            assert isinstance(model_mask, np.ndarray)
            self.assertGreaterEqual(np.count_nonzero(model_mask), np.count_nonzero(tight))
            self.assertLess(np.count_nonzero(model_mask), int(model_mask.size * 0.30))
            self.assertLess(seen["crop_shape"][0], original.shape[0])
            self.assertLess(seen["crop_shape"][1], original.shape[1])
            np.testing.assert_array_equal(cleaned[:, :90], original[:, :90])
            np.testing.assert_array_equal(cleaned[:, 390:], original[:, 390:])

    def test_search_box_completes_fragmented_glyphs_without_becoming_the_mask(self) -> None:
        image = np.full((220, 520, 3), 255, dtype=np.uint8)
        # Four character-like glyphs containing several separate strokes.
        for x in (120, 190, 260, 330):
            cv2.rectangle(image, (x, 78), (x + 34, 128), (8, 8, 8), 3)
            cv2.line(image, (x + 6, 88), (x + 28, 118), (8, 8, 8), 3)
            cv2.line(image, (x + 28, 88), (x + 6, 118), (8, 8, 8), 3)
        # Balloon/impact strokes entering through the selection boundary.
        for y in range(15, 205, 18):
            cv2.line(image, (0, y), (45, y + 5), (150, 45, 35), 3)
            cv2.line(image, (519, y), (474, y + 5), (150, 45, 35), 3)

        selection = np.full((220, 520), 255, dtype=np.uint8)
        fragmented = np.zeros_like(selection)
        for x in (120, 190, 260, 330):
            fragmented[82:91, x + 2:x + 11] = 255

        completed = CleaningManager._build_text_mask(image, fragmented, selection)
        dark_text = np.all(image < 40, axis=2)

        self.assertGreater(np.count_nonzero(completed & dark_text.astype(np.uint8)), 1000)
        self.assertLess(np.count_nonzero(completed), int(completed.size * 0.20))
        self.assertEqual(np.count_nonzero(completed[:, :48]), 0)
        self.assertEqual(np.count_nonzero(completed[:, 472:]), 0)

    def test_colour_refinement_uses_real_black_and_red_glyph_pixels(self) -> None:
        image = np.full((190, 460, 3), 248, dtype=np.uint8)
        selection = np.zeros((190, 460), dtype=np.uint8)
        selection[10:180, 10:450] = 255
        cv2.putText(
            image, "BLACK", (70, 85), cv2.FONT_HERSHEY_SIMPLEX,
            1.25, (7, 7, 7), 5, cv2.LINE_AA,
        )
        cv2.putText(
            image, "RED", (155, 145), cv2.FONT_HERSHEY_SIMPLEX,
            1.25, (205, 45, 92), 5, cv2.LINE_AA,
        )
        # Simulate a broad neural locator instead of an exact glyph mask.
        neural = np.zeros_like(selection)
        neural[45:94, 55:345] = 255
        neural[105:155, 140:300] = 255

        refined = CleaningManager._refine_neural_mask_by_colour(
            image, neural, selection
        )
        black = np.max(image, axis=2) < 40
        red = (image[:, :, 0] > 160) & (image[:, :, 1] < 100)

        self.assertGreater(np.count_nonzero((refined > 0) & black), 700)
        self.assertGreater(np.count_nonzero((refined > 0) & red), 400)
        self.assertLess(np.count_nonzero(refined), np.count_nonzero(neural))

    def test_flat_coloured_balloon_uses_median_fill_without_lama(self) -> None:
        image = np.full((220, 520, 3), (28, 35, 44), dtype=np.uint8)
        cv2.ellipse(image, (260, 110), (220, 82), 0, 0, 360, (218, 202, 171), -1)
        cv2.ellipse(image, (260, 110), (220, 82), 0, 0, 360, (20, 20, 20), 3)
        text_mask = np.zeros((220, 520), dtype=np.uint8)
        cv2.putText(
            text_mask, "TEXT", (170, 125), cv2.FONT_HERSHEY_SIMPLEX,
            1.2, 255, 5, cv2.LINE_AA,
        )
        selection = np.zeros_like(text_mask)
        selection[10:210, 10:510] = 255

        fill = CleaningManager._flat_balloon_fill_colour(
            image, text_mask, selection
        )

        self.assertIsNotNone(fill)
        assert fill is not None
        self.assertLess(max(abs(value - expected) for value, expected in zip(fill, (218, 202, 171))), 3)

    def test_gradient_balloon_remains_assigned_to_lama(self) -> None:
        image = np.full((220, 520, 3), (28, 35, 44), dtype=np.uint8)
        yy, xx = np.mgrid[:220, :520]
        interior = ((xx - 260) / 220) ** 2 + ((yy - 110) / 82) ** 2 <= 1
        for y in range(220):
            image[y, interior[y]] = (180 + y // 3, 170 + y // 4, 150 + y // 5)
        cv2.ellipse(image, (260, 110), (220, 82), 0, 0, 360, (20, 20, 20), 3)
        text_mask = np.zeros((220, 520), dtype=np.uint8)
        cv2.putText(
            text_mask, "TEXT", (170, 125), cv2.FONT_HERSHEY_SIMPLEX,
            1.2, 255, 5, cv2.LINE_AA,
        )
        selection = np.zeros_like(text_mask)
        selection[10:210, 10:510] = 255

        fill = CleaningManager._flat_balloon_fill_colour(
            image, text_mask, selection
        )

        self.assertIsNone(fill)

    def test_flat_balloon_recovers_a_complete_line_missed_by_neural_mask(self) -> None:
        image = np.full((230, 520, 3), 250, dtype=np.uint8)
        selection = np.zeros((230, 520), dtype=np.uint8)
        selection[12:218, 12:508] = 255
        neural = np.zeros_like(selection)
        line_positions = (42, 88, 134, 180)
        for line_index, y in enumerate(line_positions):
            for x in range(90, 430, 42):
                cv2.rectangle(image, (x, y), (x + 22, y + 25), (12, 12, 12), 3)
                cv2.line(image, (x + 4, y + 5), (x + 18, y + 20), (12, 12, 12), 2)
                if line_index != 2:
                    cv2.rectangle(neural, (x - 2, y - 2), (x + 24, y + 27), 255, -1)

        final = CleaningManager._build_text_mask(image, neural, selection)

        self.assertGreater(np.count_nonzero(final[130:165]), 500)
        for y in line_positions:
            self.assertGreater(np.count_nonzero(final[y - 3:y + 30]), 400)

    def test_coloured_text_is_recovered_without_masking_shout_balloon_rays(self) -> None:
        image = np.full((260, 560, 3), 252, dtype=np.uint8)
        selection = np.zeros((260, 560), dtype=np.uint8)
        selection[20:240, 20:540] = 255
        # Red impact rays enter through the selection boundary.
        for x in range(35, 525, 28):
            cv2.line(image, (x, 20), (x + 10, 58), (175, 45, 45), 4, cv2.LINE_AA)
            cv2.line(image, (x, 239), (x + 10, 202), (175, 45, 45), 4, cv2.LINE_AA)
        # The OCR model contributes no seed for this coloured lettering.
        for y in (95, 145):
            for x in range(125, 425, 44):
                cv2.rectangle(image, (x, y), (x + 24, y + 27), (205, 65, 105), 4)
                cv2.line(image, (x + 4, y + 6), (x + 20, y + 21), (205, 65, 105), 3)

        final = CleaningManager._build_text_mask(image, np.zeros_like(selection), selection)

        self.assertGreater(np.count_nonzero(final[85:180, 105:455]), 700)
        self.assertEqual(np.count_nonzero(final[15:65]), 0)
        self.assertEqual(np.count_nonzero(final[195:245]), 0)

    def test_black_text_is_recovered_when_box_also_contains_textured_art(self) -> None:
        image = np.zeros((360, 620, 3), dtype=np.uint8)
        # Textured artwork occupies most of the search box, so white is not a
        # globally dominant colour.
        yy, xx = np.mgrid[:360, :620]
        image[:, :, 0] = (35 + (xx % 91)).astype(np.uint8)
        image[:, :, 1] = (25 + (yy % 73)).astype(np.uint8)
        image[:, :, 2] = (45 + ((xx + yy) % 67)).astype(np.uint8)
        selection = np.full((360, 620), 255, dtype=np.uint8)
        # Only the bottom portion is a speech balloon.
        cv2.ellipse(image, (320, 285), (250, 66), 0, 0, 360, (252, 252, 252), -1)
        for x in range(120, 500, 46):
            cv2.rectangle(image, (x, 258), (x + 25, 287), (8, 8, 8), 3)
            cv2.line(image, (x + 4, 264), (x + 20, 282), (8, 8, 8), 3)
            cv2.rectangle(image, (x, 302), (x + 25, 330), (8, 8, 8), 3)

        final = CleaningManager._build_text_mask(
            image, np.zeros_like(selection), selection
        )

        self.assertGreater(np.count_nonzero(final[245:340, 100:520]), 900)
        self.assertLess(np.count_nonzero(final[:210]), 80)

    def test_solid_balloon_mask_captures_black_and_coloured_text(self) -> None:
        image = np.full((220, 520, 3), (28, 35, 44), dtype=np.uint8)
        selection = np.zeros((220, 520), dtype=np.uint8)
        selection[12:208, 12:508] = 255
        cv2.ellipse(image, (260, 110), (220, 82), 0, 0, 360, (250, 250, 250), -1)
        cv2.putText(image, "BLACK", (95, 105), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (8, 8, 8), 4, cv2.LINE_AA)
        cv2.putText(image, "RED", (190, 155), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (205, 55, 95), 4, cv2.LINE_AA)

        mask, fill = CleaningManager._solid_balloon_text_mask(image, selection)

        self.assertIsNotNone(fill)
        self.assertGreater(np.count_nonzero(mask[65:170, 70:390]), 1200)
        self.assertEqual(np.count_nonzero(mask[:20]), 0)

    def test_solid_balloon_mask_keeps_impact_rays_outside_the_text_mask(self) -> None:
        image = np.full((260, 620, 3), (25, 28, 32), dtype=np.uint8)
        selection = np.zeros((260, 620), dtype=np.uint8)
        selection[8:252, 8:612] = 255
        centre = np.asarray((310, 130))
        axes = np.asarray((260, 95))
        cv2.ellipse(image, tuple(centre), tuple(axes), 0, 0, 360, (252, 252, 252), -1)
        # Roots enter the white balloon, reproducing a pointed shout bubble.
        for angle in np.linspace(0, 2 * np.pi, 80, endpoint=False):
            inner = centre + np.asarray((np.cos(angle), np.sin(angle))) * axes * 0.91
            outer = centre + np.asarray((np.cos(angle), np.sin(angle))) * axes * 1.08
            cv2.line(
                image, tuple(inner.astype(int)), tuple(outer.astype(int)),
                (175, 45, 38), 3, cv2.LINE_AA,
            )
        cv2.putText(
            image, "BLACK", (145, 120), cv2.FONT_HERSHEY_SIMPLEX,
            1.2, (8, 8, 8), 4, cv2.LINE_AA,
        )
        cv2.putText(
            image, "RED", (245, 175), cv2.FONT_HERSHEY_SIMPLEX,
            1.2, (205, 55, 95), 4, cv2.LINE_AA,
        )

        mask, fill = CleaningManager._solid_balloon_text_mask(image, selection)

        self.assertIsNotNone(fill)
        self.assertGreater(np.count_nonzero(mask[70:195, 120:500]), 3000)
        self.assertEqual(np.count_nonzero(mask) - np.count_nonzero(mask[70:195, 120:500]), 0)

    def test_solid_balloon_mask_rejects_a_connected_ray_crown(self) -> None:
        image = np.full((300, 600, 3), (25, 25, 25), dtype=np.uint8)
        selection = np.zeros((300, 600), dtype=np.uint8)
        selection[10:285, 10:590] = 255
        cv2.ellipse(image, (300, 155), (270, 120), 0, 0, 360, (255, 255, 255), -1)
        # Closely spaced rays merge after mask expansion, reproducing the
        # crown that previously made LaMa erase the rectangular/balloon edge.
        for x in range(25, 576, 9):
            relative_x = (x - 300) / 270
            if abs(relative_x) >= 1:
                continue
            arc_y = int(155 - 120 * np.sqrt(max(0.0, 1.0 - relative_x * relative_x)))
            cv2.line(
                image, (x, 10), (x + 5, min(arc_y + 22, 85)),
                (180, 55, 65), 3, cv2.LINE_AA,
            )
        cv2.putText(
            image, "TEXT LINE", (135, 155), cv2.FONT_HERSHEY_SIMPLEX,
            1.4, (5, 5, 5), 5, cv2.LINE_AA,
        )
        cv2.putText(
            image, "SECOND", (185, 215), cv2.FONT_HERSHEY_SIMPLEX,
            1.4, (205, 50, 90), 5, cv2.LINE_AA,
        )

        mask, fill = CleaningManager._solid_balloon_text_mask(image, selection)

        self.assertIsNotNone(fill)
        self.assertGreater(np.count_nonzero(mask[105:240]), 8000)
        self.assertEqual(np.count_nonzero(mask[10:90]), 0)

    def test_complete_balloon_box_keeps_every_dense_text_line(self) -> None:
        image = np.full((360, 800, 3), (24, 27, 31), dtype=np.uint8)
        selection = np.zeros((360, 800), dtype=np.uint8)
        selection[5:355, 5:795] = 255
        cv2.ellipse(image, (400, 180), (365, 145), 0, 0, 360, (252, 252, 252), -1)
        for angle in np.linspace(0, 2 * np.pi, 100, endpoint=False):
            inner = np.asarray((400, 180)) + np.asarray(
                (np.cos(angle) * 350, np.sin(angle) * 139)
            )
            outer = np.asarray((400, 180)) + np.asarray(
                (np.cos(angle) * 390, np.sin(angle) * 165)
            )
            cv2.line(
                image, tuple(inner.astype(int)), tuple(outer.astype(int)),
                (170, 48, 42), 3, cv2.LINE_AA,
            )
        lines = (
            ("DENSE DIALOGUE LINE 1", 66),
            ("DENSE DIALOGUE LINE 2", 125),
            ("DENSE DIALOGUE LINE 3", 184),
            ("DENSE DIALOGUE LINE 4 ...", 243),
        )
        for text, y in lines:
            cv2.putText(
                image, text, (105, y + 35), cv2.FONT_HERSHEY_SIMPLEX,
                0.95, (5, 5, 5), 3, cv2.LINE_AA,
            )

        mask, fill = CleaningManager._solid_balloon_text_mask(image, selection)

        self.assertIsNotNone(fill)
        for _, y in lines:
            self.assertGreater(np.count_nonzero(mask[y:y + 45, 80:720]), 900)
        self.assertEqual(np.count_nonzero(mask[:35]), 0)
        self.assertEqual(np.count_nonzero(mask[325:]), 0)

    def test_solid_balloon_contour_protection_keeps_inner_punctuation(self) -> None:
        interior = np.zeros((260, 620), dtype=np.uint8)
        cv2.ellipse(interior, (310, 130), (270, 105), 0, 0, 360, 255, -1)
        mask = np.zeros_like(interior)
        # A ray crossing the real upper silhouette, far from the box edge.
        cv2.line(mask, (250, 29), (265, 72), 255, 3, cv2.LINE_AA)
        # Dialogue punctuation near the right end of a line, but safely
        # inside the balloon contour.
        for x in (505, 518, 531, 544):
            cv2.circle(mask, (x, 150), 4, 255, -1)

        protected = CleaningManager._protect_solid_balloon_silhouette(mask, interior)

        self.assertEqual(np.count_nonzero(protected[20:82, 235:280]), 0)
        self.assertGreater(np.count_nonzero(protected[140:160, 495:555]), 120)

    def test_solid_balloon_cleanup_uses_line_seed_but_skips_lama(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "solid.png"
            image = np.full((220, 520, 3), (30, 35, 42), dtype=np.uint8)
            cv2.ellipse(image, (260, 110), (220, 82), 0, 0, 360, (252, 252, 252), -1)
            cv2.putText(image, "TEXT", (170, 125), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (5, 5, 5), 5, cv2.LINE_AA)
            Image.fromarray(image).save(source)
            manager = CleaningManager(Path(temp))
            def line_seed(crop: np.ndarray) -> np.ndarray:
                seed = np.zeros(crop.shape[:2], dtype=np.uint8)
                seed[65:155, 135:385] = 255
                return seed

            manager._text_mask = lambda _crop: self.fail(  # type: ignore[method-assign]
                "Un globo sólido validado no debe ejecutar ocr.onnx"
            )
            manager._lama = lambda _crop, _mask: self.fail("No debe ejecutar LaMa")  # type: ignore[method-assign]

            result = manager.clean(
                source, [{"x": 70, "y": 45, "width": 380, "height": 130}],
                lambda _value: None, lambda: False,
            )

            self.assertEqual(result["solid_entries"], 1)
            self.assertEqual(len(result["patches"]), 1)
            patch = result["patches"][0]
            self.assertGreater(float(patch["pixels"][patch["mask"] > 0].mean()), 245.0)

    def test_detector_guided_solid_mask_excludes_impact_rays(self) -> None:
        image = np.full((280, 660, 3), (28, 31, 36), dtype=np.uint8)
        selection = np.zeros((280, 660), dtype=np.uint8)
        selection[8:272, 8:652] = 255
        cv2.ellipse(image, (330, 140), (285, 108), 0, 0, 360, (252, 252, 252), -1)
        for x in range(55, 606, 12):
            relative_x = (x - 330) / 285
            if abs(relative_x) >= 1:
                continue
            arc_y = int(140 - 108 * np.sqrt(max(0.0, 1.0 - relative_x * relative_x)))
            cv2.line(image, (x, 8), (x + 5, arc_y + 18), (175, 45, 40), 3, cv2.LINE_AA)
        cv2.putText(image, "FIRST LINE", (170, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (5, 5, 5), 4)
        cv2.putText(image, "SECOND...", (175, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (5, 5, 5), 4)
        detected = np.zeros(selection.shape, dtype=np.uint8)
        detected[88:145, 145:525] = 255
        detected[148:205, 145:540] = 255

        mask = CleaningManager._solid_text_mask_from_detector(
            image, detected, selection, (252, 252, 252)
        )

        self.assertGreater(np.count_nonzero(mask[80:215, 130:555]), 4000)
        self.assertEqual(np.count_nonzero(mask[:70]), 0)

    def test_detector_guided_mask_recovers_distant_ellipsis_not_outline(self) -> None:
        image = np.full((240, 620, 3), (250, 250, 250), dtype=np.uint8)
        selection = np.full((240, 620), 255, dtype=np.uint8)
        cv2.ellipse(image, (310, 120), (285, 96), 0, 0, 360, (20, 20, 20), 3)
        cv2.putText(image, "TEXT", (165, 135), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (5, 5, 5), 4)
        for x in (405, 418, 431, 444, 457):
            cv2.circle(image, (x, 126), 3, (5, 5, 5), -1)
        detected = np.zeros(selection.shape, dtype=np.uint8)
        detected[82:150, 140:330] = 255

        mask = CleaningManager._solid_text_mask_from_detector(
            image, detected, selection, (250, 250, 250)
        )

        self.assertGreater(np.count_nonzero(mask[112:142, 392:470]), 180)
        self.assertEqual(np.count_nonzero(mask[15:42]), 0)

    def test_mask_growth_is_bounded_for_large_balloon_boxes(self) -> None:
        raw = np.zeros((500, 1200), dtype=np.uint8)
        raw[235:265, 540:660] = 255

        expanded = CleaningManager._expand_text_mask(raw, 100)

        ys, xs = np.nonzero(expanded)
        self.assertGreater(xs.min(), 530)
        self.assertLess(xs.max(), 670)
        self.assertGreater(ys.min(), 225)
        self.assertLess(ys.max(), 275)
        self.assertEqual(np.count_nonzero(expanded[:, :500]), 0)
        self.assertEqual(np.count_nonzero(expanded[:, 700:]), 0)

    def test_selection_boundary_protects_balloon_rays_but_keeps_text(self) -> None:
        selection = np.zeros((300, 600), dtype=np.uint8)
        selection[25:275, 30:570] = 255
        mask = np.zeros_like(selection)
        mask[120:180, 210:390] = 255  # Internal text block.
        mask[50:55, 30:220] = 255     # Ray entering from left edge.
        mask[245:250, 380:570] = 255  # Ray entering from right edge.

        protected = CleaningManager._protect_selection_boundary(mask, selection)

        self.assertGreater(np.count_nonzero(protected[120:180, 210:390]), 0)
        self.assertEqual(np.count_nonzero(protected[50:55, 30:220]), 0)
        self.assertEqual(np.count_nonzero(protected[245:250, 380:570]), 0)

    def test_original_pixel_guard_reserves_spikes_and_antialiasing(self) -> None:
        image = np.full((240, 520, 3), 255, dtype=np.uint8)
        selection = np.zeros((240, 520), dtype=np.uint8)
        selection[20:220, 20:500] = 255
        # Impact rays enter through all four sides of the selection.
        for x in range(35, 490, 28):
            cv2.line(image, (x, 20), (x + 12, 55), (8, 8, 8), 4, cv2.LINE_AA)
            cv2.line(image, (x, 219), (x + 12, 184), (160, 45, 35), 4, cv2.LINE_AA)
        cv2.line(image, (20, 70), (60, 82), (8, 8, 8), 4, cv2.LINE_AA)
        cv2.line(image, (499, 155), (459, 167), (8, 8, 8), 4, cv2.LINE_AA)
        # Interior text-like block must not be protected as balloon border.
        cv2.putText(image, "TEXT", (175, 135), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (8, 8, 8), 4, cv2.LINE_AA)

        guard = CleaningManager._selection_border_guard(image, selection)

        self.assertGreater(np.count_nonzero(guard[15:62]), 500)
        self.assertGreater(np.count_nonzero(guard[178:225]), 500)
        self.assertEqual(np.count_nonzero(guard[95:150, 150:350]), 0)

    def test_border_guard_is_subtracted_from_completed_text_mask(self) -> None:
        image = np.full((180, 360, 3), 255, dtype=np.uint8)
        selection = np.full((180, 360), 255, dtype=np.uint8)
        cv2.line(image, (0, 30), (70, 55), (15, 15, 15), 5, cv2.LINE_AA)
        cv2.putText(image, "AB", (135, 105), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (10, 10, 10), 4, cv2.LINE_AA)
        detected = np.zeros_like(selection)
        cv2.line(detected, (0, 30), (70, 55), 255, 9, cv2.LINE_AA)
        cv2.rectangle(detected, (125, 65), (220, 115), 255, -1)

        final = CleaningManager._build_text_mask(image, detected, selection)

        self.assertEqual(np.count_nonzero(final[:, :75]), 0)
        self.assertGreater(np.count_nonzero(final[55:125, 115:230]), 0)

    def test_lama_compositor_uses_model_only_inside_exact_mask(self) -> None:
        yy, xx = np.mgrid[:120, :240]
        original = np.stack((
            80 + (xx % 19) * 4,
            55 + (yy % 17) * 5,
            100 + ((xx + yy) % 23) * 3,
        ), axis=2).astype(np.uint8)
        mask = np.zeros((120, 240), dtype=np.uint8)
        cv2.rectangle(mask, (70, 45), (170, 75), 255, -1)
        restored = original.copy()
        restored[mask > 0] = (17, 33, 49)

        cleaned = CleaningManager._composite_lama(original, restored, mask)

        np.testing.assert_array_equal(cleaned[mask == 0], original[mask == 0])
        np.testing.assert_array_equal(cleaned[mask > 0], restored[mask > 0])

    def test_isolated_outer_ray_is_not_accepted_as_text(self) -> None:
        selection = np.full((289, 532), 255, dtype=np.uint8)
        ray = np.zeros_like(selection)
        cv2.line(ray, (18, 70), (50, 38), 255, 14, cv2.LINE_AA)

        filtered = CleaningManager._filter_text_seed_components(ray, selection)

        self.assertEqual(np.count_nonzero(filtered), 0)

    def test_disconnected_impact_tips_outside_text_band_are_removed(self) -> None:
        selection = np.full((300, 520), 255, dtype=np.uint8)
        mask = np.zeros_like(selection)
        cv2.rectangle(mask, (95, 95), (420, 125), 255, -1)
        cv2.rectangle(mask, (75, 145), (445, 177), 255, -1)
        cv2.rectangle(mask, (105, 198), (405, 228), 255, -1)
        for x in range(35, 490, 55):
            cv2.line(mask, (x, 18), (x + 18, 42), 255, 3)

        filtered = CleaningManager._suppress_peripheral_strokes(mask, selection)

        self.assertGreater(np.count_nonzero(filtered[90:235]), 0)
        self.assertEqual(np.count_nonzero(filtered[:55]), 0)

    def test_tight_single_character_box_remains_valid(self) -> None:
        selection = np.full((52, 52), 255, dtype=np.uint8)
        character = np.zeros_like(selection)
        cv2.rectangle(character, (10, 9), (41, 43), 255, 5)

        filtered = CleaningManager._filter_text_seed_components(character, selection)

        self.assertGreater(np.count_nonzero(filtered), 0)

    def test_residual_retry_mask_stays_inside_validated_text(self) -> None:
        cleaned = np.full((120, 240, 3), 255, dtype=np.uint8)
        text_mask = np.zeros((120, 240), dtype=np.uint8)
        cv2.rectangle(text_mask, (70, 44), (170, 76), 255, -1)
        selection = np.full_like(text_mask, 255)
        cleaned[55:58, 104:111] = (238, 238, 238)  # faint residual ink
        cleaned[10:30, 5:12] = (0, 0, 0)           # balloon ray outside mask

        residual = CleaningManager._residual_mask(cleaned, text_mask, selection)

        self.assertGreater(np.count_nonzero(residual[52:61, 100:115]), 0)
        self.assertEqual(np.count_nonzero(residual[text_mask == 0]), 0)

    def test_residual_retry_is_disabled_on_textured_background(self) -> None:
        yy, xx = np.mgrid[:120, :240]
        cleaned = np.stack((
            50 + (xx % 31) * 5,
            70 + (yy % 29) * 5,
            40 + ((xx + yy) % 37) * 5,
        ), axis=2).astype(np.uint8)
        text_mask = np.zeros((120, 240), dtype=np.uint8)
        cv2.rectangle(text_mask, (70, 44), (170, 76), 255, -1)
        selection = np.full_like(text_mask, 255)

        residual = CleaningManager._residual_mask(cleaned, text_mask, selection)

        self.assertEqual(np.count_nonzero(residual), 0)

    def test_iterative_refiner_retries_locally_and_preserves_outside(self) -> None:
        manager = CleaningManager.__new__(CleaningManager)
        cleaned = np.full((240, 420, 3), 255, dtype=np.uint8)
        text_mask = np.zeros((240, 420), dtype=np.uint8)
        cv2.rectangle(text_mask, (130, 90), (290, 145), 255, -1)
        selection = np.full_like(text_mask, 255)
        cleaned[112:116, 195:220] = (236, 236, 236)
        outside_before = cleaned[text_mask == 0].copy()
        calls: list[tuple[tuple[int, int], int]] = []

        def fake_lama(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
            calls.append((crop.shape[:2], int(np.count_nonzero(mask))))
            result = crop.copy()
            result[mask > 0] = 255
            return result

        manager._lama = fake_lama  # type: ignore[method-assign]
        refined, passes = manager._refine_lama_residuals(
            cleaned, text_mask, selection, max_passes=2
        )

        self.assertEqual(passes, 1)
        self.assertEqual(len(calls), 1)
        self.assertLess(calls[0][0][0], cleaned.shape[0])
        self.assertLess(calls[0][0][1], cleaned.shape[1])
        np.testing.assert_array_equal(refined[text_mask == 0], outside_before)
        self.assertEqual(np.count_nonzero(manager._residual_mask(refined, text_mask, selection)), 0)

    def test_nearby_boxes_share_one_lama_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            original = np.full((480, 720, 3), 255, dtype=np.uint8)
            Image.fromarray(original).save(source)
            manager = CleaningManager(Path(temp))
            calls: list[tuple[int, int]] = []
            ocr_calls: list[tuple[int, int]] = []

            def fake_text_mask(crop: np.ndarray) -> np.ndarray:
                ocr_calls.append(crop.shape[:2])
                mask = np.zeros(crop.shape[:2], dtype=np.uint8)
                h, w = mask.shape
                cv2.rectangle(mask, (w // 3, h // 3), (w * 2 // 3, h * 2 // 3), 255, -1)
                return mask

            def fake_lama(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
                calls.append(crop.shape[:2])
                result = crop.copy()
                result[mask > 0] = 255
                return result

            manager._text_mask = fake_text_mask  # type: ignore[method-assign]
            manager._build_text_mask = lambda rgb, detected, selection: cv2.bitwise_and(  # type: ignore[method-assign]
                detected, selection
            )
            manager._lama = fake_lama  # type: ignore[method-assign]
            manager._residual_mask = lambda cleaned, text_mask, selection: np.zeros_like(text_mask)  # type: ignore[method-assign]

            result = manager.clean(
                source,
                [
                    {"x": 150, "y": 140, "width": 130, "height": 90},
                    {"x": 245, "y": 180, "width": 130, "height": 90},
                    {"x": 335, "y": 215, "width": 130, "height": 90},
                ],
                lambda value: None,
                lambda: False,
            )

            self.assertEqual(len(calls), 1)
            self.assertEqual(len(ocr_calls), 1)
            self.assertEqual(len(result["patches"]), 3)

    def test_mask_preview_reuses_ocr_cache_and_returns_independent_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.fromarray(np.full((240, 360, 3), 255, dtype=np.uint8)).save(source)
            manager = CleaningManager(Path(temp))
            calls = 0

            def fake_text_mask(crop: np.ndarray) -> np.ndarray:
                nonlocal calls
                calls += 1
                mask = np.zeros(crop.shape[:2], dtype=np.uint8)
                cv2.rectangle(mask, (90, 70), (145, 105), 255, -1)
                return mask

            manager._text_mask = fake_text_mask  # type: ignore[method-assign]
            manager._build_text_mask = lambda rgb, detected, selection: cv2.bitwise_and(  # type: ignore[method-assign]
                detected, selection
            )
            regions = [{"x": 60, "y": 45, "width": 180, "height": 120}]

            first = manager.prepare_masks(source, regions, lambda value: None, lambda: False)
            self.assertFalse(first["cached"])
            self.assertEqual(calls, 1)
            self.assertTrue(first["entries"])

            # Editing a preview must not mutate the cached source mask.
            first["entries"][0]["mask"][:] = 0
            second = manager.prepare_masks(source, regions, lambda value: None, lambda: False)
            self.assertTrue(second["cached"])
            self.assertEqual(calls, 1)
            self.assertGreater(np.count_nonzero(second["entries"][0]["mask"]), 0)

    def test_mask_cache_reuses_unchanged_boxes_when_one_box_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.fromarray(np.full((720, 360, 3), 255, dtype=np.uint8)).save(source)
            manager = CleaningManager(Path(temp))
            calls = 0

            def fake_text_mask(crop: np.ndarray) -> np.ndarray:
                nonlocal calls
                calls += 1
                return np.full(crop.shape[:2], 255, dtype=np.uint8)

            manager._solid_balloon_text_mask = lambda crop, selection: (  # type: ignore[method-assign]
                np.zeros(selection.shape, dtype=np.uint8), None,
            )
            manager._text_mask = fake_text_mask  # type: ignore[method-assign]
            manager._build_text_mask = lambda rgb, detected, selection: cv2.bitwise_and(  # type: ignore[method-assign]
                detected, selection
            )
            first_regions = [
                {"x": 30, "y": 30, "width": 140, "height": 90},
                {"x": 30, "y": 380, "width": 140, "height": 90},
            ]
            manager.prepare_masks(source, first_regions, lambda _value: None, lambda: False)
            self.assertEqual(calls, 2)

            moved_regions = [first_regions[0], {**first_regions[1], "y": 500}]
            plan = manager.prepare_masks(source, moved_regions, lambda _value: None, lambda: False)

            self.assertEqual(calls, 3)
            self.assertEqual(len(plan["entries"]), 2)
            self.assertGreaterEqual(manager.cache_stats()["entry_hits"], 1)

    def test_cleaning_reuses_decoded_page_between_mask_and_lama(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "page.png"
            Image.new("RGB", (120, 180), "white").save(source)
            manager = CleaningManager(Path(temp))
            manager.set_resource_profile("balanced")
            calls = 0

            def load(_path, _states=None):
                nonlocal calls
                calls += 1
                return Image.new("RGB", (120, 180), "white")

            with patch("core.cleaning_manager.load_source_image", side_effect=load):
                first = manager._load_source_page(source)
                second = manager._load_source_page(source)

            self.assertIs(first, second)
            self.assertEqual(calls, 1)
            self.assertEqual(manager.cache_stats()["source_bytes"], first.nbytes)

    def test_text_mask_never_expands_into_the_whole_balloon(self) -> None:
        image = np.full((100, 200, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (80, 40), (120, 60), (0, 0, 0), 2)
        cv2.line(image, (10, 5), (10, 95), (0, 0, 0), 3)
        cv2.line(image, (190, 5), (190, 95), (0, 0, 0), 3)
        neural = np.zeros((100, 200), dtype=np.uint8)
        neural[40:61, 80:121] = 255
        selection = np.full((100, 200), 255, dtype=np.uint8)

        completed = CleaningManager._complete_glyph_mask(image, neural, selection)

        self.assertEqual(np.count_nonzero(completed[:, 8:13]), 0)
        self.assertEqual(np.count_nonzero(completed[:, 188:193]), 0)
        self.assertLess(np.count_nonzero(completed), int(completed.size * 0.50))

    def test_multiline_mask_keeps_impact_rays_outside_text_runs(self) -> None:
        image = np.full((320, 650, 3), 255, dtype=np.uint8)
        for y in range(15, 306, 18):
            cv2.line(image, (4, y), (45, y + 5), (180, 60, 45), 3)
            cv2.line(image, (646, y), (605, y + 5), (180, 60, 45), 3)
        neural = np.zeros((320, 650), dtype=np.uint8)
        for y in (75, 125, 175, 225):
            cv2.putText(image, "TEXT ......", (110, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (15, 15, 15), 2, cv2.LINE_AA)
            neural[y:y + 22, 105:545] = 255
        selection = np.full((320, 650), 255, dtype=np.uint8)

        completed = CleaningManager._complete_glyph_mask(image, neural, selection)

        self.assertEqual(np.count_nonzero(completed[:, :48]), 0)
        self.assertEqual(np.count_nonzero(completed[:, 602:]), 0)
        for y in (85, 135, 185, 235):
            self.assertGreater(np.count_nonzero(completed[y - 12:y + 24, 80:580]), 1000)

    def test_history_restores_regions_text_and_cleaning(self) -> None:
        pixels = np.full((3, 3, 3), 127, dtype=np.uint8)
        initial = {
            "active_index": 0,
            "page_regions": {"page": [{"id": "r1", "x": 1, "y": 2, "width": 20, "height": 10, "text": "原文"}]},
            "clean_results": {"page": {"patches": [{"id": "p1", "x": 1, "y": 2, "pixels": pixels}]}},
            "page_texts": {"page": "original"},
            "page_styles": {"page": {"translation": {"margin": 8}}},
        }
        history = HistoryManager()
        history.reset(initial)
        changed = {**initial, "page_regions": {"page": [{**initial["page_regions"]["page"][0], "x": 42}]}, "page_texts": {"page": "traducido"}}
        self.assertTrue(history.record(changed))
        undone = history.undo()
        self.assertEqual(undone["page_regions"]["page"][0]["x"], 1)
        self.assertEqual(undone["page_texts"]["page"], "original")
        self.assertIs(undone["clean_results"]["page"]["patches"][0]["pixels"], pixels)
        redone = history.redo()
        self.assertEqual(redone["page_regions"]["page"][0]["x"], 42)

    def test_project_bundle_round_trip_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "01.png"
            Image.new("RGB", (120, 80), "white").save(source)
            page = Page("01.png", source, 120, 80, "0.0 MB")
            key = page_key(page)
            regions = {key: [{
                "id": "r1", "number": 1, "x": 40, "y": 20, "width": 50, "height": 30,
                "text": "原文", "translation": "Texto", "applied_text": "",
                "style": {"font_size": 12},
            }]}
            red_patch = np.zeros((5, 7, 3), dtype=np.uint8)
            red_patch[:, :, 0] = 255
            stroke_alpha = np.zeros((5, 7), dtype=np.uint8)
            stroke_alpha[1:4, 2:5] = 255
            residual_alpha = np.zeros((5, 7), dtype=np.uint8)
            residual_alpha[2, 3] = 255
            cleaning = {key: {"patches": [{
                "id": "p1", "x": 10, "y": 12, "pixels": red_patch,
                "mask": stroke_alpha, "kind": "restore", "qc_status": "attention",
                "residual_pixels": 1, "residual_mask": residual_alpha,
                "target": {"x": 10, "y": 12, "width": 7, "height": 5},
            }]}}
            texts = {key: "Texto"}
            styles = {key: {"translation": {"margin": 9, "manga_mode": False}}}
            project_file = folder / "chapter.mseproj"

            save_project_bundle(project_file, [page], 0, regions, cleaning, texts, styles)
            restored = load_project_bundle(project_file)
            restored_key = page_key(restored["pages"][0])
            self.assertEqual(restored["page_regions"][restored_key][0]["text"], "原文")
            self.assertEqual(restored["page_regions"][restored_key][0]["translation"], "Texto")
            self.assertEqual(restored["page_texts"][restored_key], "Texto")
            self.assertEqual(restored["page_styles"][restored_key]["translation"]["margin"], 9)
            np.testing.assert_array_equal(restored["clean_results"][restored_key]["patches"][0]["pixels"], red_patch)
            np.testing.assert_array_equal(restored["clean_results"][restored_key]["patches"][0]["mask"], stroke_alpha)
            self.assertEqual(restored["clean_results"][restored_key]["patches"][0]["kind"], "restore")
            self.assertEqual(restored["clean_results"][restored_key]["patches"][0]["qc_status"], "attention")
            np.testing.assert_array_equal(
                restored["clean_results"][restored_key]["patches"][0]["residual_mask"], residual_alpha,
            )

            output = folder / "final.png"
            export_page(
                output, restored["pages"][0], restored["page_regions"][restored_key],
                restored["clean_results"][restored_key], restored["page_styles"][restored_key], "PNG",
            )
            with Image.open(output) as exported:
                self.assertEqual(exported.size, (120, 80))
                array = np.asarray(exported.convert("RGB"))
            expected_patch = np.full((5, 7, 3), 255, dtype=np.uint8)
            expected_patch[stroke_alpha == 255] = red_patch[stroke_alpha == 255]
            np.testing.assert_array_equal(array[12:17, 10:17], expected_patch)
            # A scene capture would leave cyan at this region corner; the
            # compositor starts from the original, so the editing box is absent.
            np.testing.assert_array_equal(array[20, 40], np.array([255, 255, 255], dtype=np.uint8))

    def test_hidden_and_zero_opacity_layers_are_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "page.png"
            Image.new("RGB", (180, 100), "white").save(source)
            page = Page("page.png", source, 180, 100, "0.0 MB")
            base = {
                "id": "layer", "x": 10, "y": 10, "width": 160, "height": 80,
                "applied_text": "TEXTO", "style": {"font_size": 28, "auto_fit": False},
            }
            blank = np.full((100, 180, 3), 255, dtype=np.uint8)

            hidden = np.asarray(compose_page(page, [{**base, "visible": False}], None, None))
            transparent = np.asarray(compose_page(page, [{**base, "opacity": 0}], None, None))

            np.testing.assert_array_equal(hidden, blank)
            np.testing.assert_array_equal(transparent, blank)

    def test_ocr_normalize_cjk_removes_begin_and_box_headers(self) -> None:
        self.assertEqual(OCRManager.normalize_cjk_text("MSE_BOX_001  BEGIN\nHola mundo"), "Hola mundo")
        self.assertEqual(OCRManager.normalize_cjk_text("BEGIN\nHola mundo\nEND"), "Hola mundo")
        self.assertEqual(OCRManager.normalize_cjk_text("BOX 001\nHola mundo"), "Hola mundo")
        self.assertEqual(OCRManager.normalize_cjk_text("<<<MSE_BOX_0001>>>\nDiálogo"), "Diálogo")
        self.assertEqual(OCRManager.normalize_cjk_text("BEGIN"), "")
        self.assertEqual(OCRManager.normalize_cjk_text("BOX_001 BEGIN"), "")

    def test_translation_sanitize_removes_begin_and_box_headers(self) -> None:
        self.assertEqual(TranslationManager._sanitize_translation("<<<MSE_BOX_0001>>>\nTexto traducido"), "Texto traducido")
        self.assertEqual(TranslationManager._sanitize_translation("BEGIN\nTexto traducido\nEND"), "Texto traducido")
        self.assertEqual(TranslationManager._sanitize_translation("MSE_BOX_001 Texto"), "Texto")

    def test_spiky_balloon_rays_are_protected_from_text_mask(self) -> None:
        image = np.full((200, 200, 3), 255, dtype=np.uint8)
        selection = np.full((200, 200), 255, dtype=np.uint8)
        # Draw dense shout-balloon spikes converging from top and left
        for i in range(21):
            cv2.line(image, (0, i * 10), (120, 100), (10, 10, 10), 6)
            cv2.line(image, (i * 10, 0), (100, 120), (10, 10, 10), 6)
        cv2.putText(image, "TEXTO", (90, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        detected = np.zeros_like(selection)
        cv2.putText(detected, "TEXTO", (90, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
        # Neural OCR false activation along the black spikes
        for i in range(21):
            cv2.line(detected, (0, i * 10), (120, 100), 255, 6)

        final = CleaningManager._build_text_mask(image, detected, selection)
        # Spikes in the top-left area must be 100% protected (0 mask pixels)
        self.assertEqual(np.count_nonzero(final[:60, :60]), 0)
        # Central dialogue must be retained
        self.assertGreater(np.count_nonzero(final[140:180, 80:180]), 100)

    def test_symmetric_shape_intervals_maintains_exact_axial_symmetry(self) -> None:
        from core.balloon_typesetter import symmetric_shape_intervals
        for shape in ("ellipse", "diamond", "rectangle"):
            intervals = symmetric_shape_intervals(shape, 300, 200, 5, 30.0, padding=10)
            self.assertEqual(len(intervals), 5)
            for left, right in intervals:
                # margin_left must equal margin_right identically (width - right == left)
                self.assertEqual(left, 300 - right, f"Failed symmetry on {shape}: {left} vs {300 - right}")

    def test_quick_typography_presets_are_valid_and_normalized(self) -> None:
        from core.typography_manager import TypographyManager, QUICK_TYPOGRAPHY_PRESETS
        for key, preset in QUICK_TYPOGRAPHY_PRESETS.items():
            normalized = TypographyManager.normalized(preset)
            self.assertIn("font_family", normalized)
            self.assertIn("font_size", normalized)
            self.assertIn("balloon_shape", normalized)
            self.assertIn(normalized["balloon_shape"], {"auto", "ellipse", "diamond", "rectangle"})

    def test_translation_batches_scales_to_32_items_per_call(self) -> None:
        texts = [f"Dialogue line {i}" for i in range(30)]
        batches = TranslationManager._translation_batches(texts, max_items=32, max_chars=12000)
        # 30 short lines must fit cleanly in a SINGLE batch for maximum speed
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 30)

    def test_export_layered_psd_creates_valid_photoshop_file(self) -> None:
        from core.export_manager import export_layered_psd
        from psd_tools import PSDImage
        with tempfile.TemporaryDirectory() as temp:
            page_path = Path(temp) / "page_01.png"
            Image.new("RGB", (400, 500), (250, 250, 250)).save(page_path)
            page = Page(name="page_01.png", path=page_path, width=400, height=500)
            regions = [
                {"id": "r1", "x": 50, "y": 60, "width": 120, "height": 70, "applied_text": "¡Hola mundo!"},
                {"id": "r2", "x": 100, "y": 250, "width": 150, "height": 80, "applied_text": "Texto dos"},
            ]
            psd_dest = Path(temp) / "output.psd"
            export_layered_psd(psd_dest, page, regions, None, None)
            self.assertTrue(psd_dest.is_file())
            self.assertGreater(psd_dest.stat().st_size, 1000)

            # Re-read with psd-tools
            psd = PSDImage.open(str(psd_dest))
            self.assertEqual(psd.size, (400, 500))
            layers = list(psd)
            self.assertEqual(len(layers), 3)  # Fondo Limpio + 2 text layers
            self.assertEqual(layers[0].name, "Fondo Limpio")
            self.assertIn("¡Hola mundo!", layers[1].name)
            self.assertEqual(layers[1].offset, (50, 60))
            self.assertIn("Texto dos", layers[2].name)
            self.assertEqual(layers[2].offset, (100, 250))


if __name__ == "__main__":
    unittest.main()
