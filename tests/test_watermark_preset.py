"""A moderate preset must cap the whole chapter and preserve the user's PNG."""
from io import BytesIO
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from core.chapter_watermarks import chapter_watermark_settings
from core.settings_manager import SettingsManager


def logo():
    buffer = BytesIO()
    Image.new("RGBA", (300, 100), (20, 60, 90, 255)).save(buffer, "PNG")
    return buffer.getvalue()


def test_new_preferences_produce_moderate_spacing_and_limit_a_long_chapter(tmp_path):
    preferences = SettingsManager(tmp_path / "settings.json").data["watermark"]
    pages = [dict(name=str(i), width=900, height=60000) for i in range(4)]
    config = {**preferences, "png_bytes": logo(), "enabled_pages": [p["name"] for p in pages]}
    plan = chapter_watermark_settings(pages, config)
    points = [(x, i * 60000 + y) for i, page in enumerate(pages)
              for x, y in plan[page["name"]]["positions"]]
    assert 12 <= len(points) <= 24
    assert len({x for x, _ in points}) == 2
    assert all(b[1] - a[1] >= 7000 for a, b in zip(points, points[1:]))
    assert preferences["scale_percent"] == 22
    assert preferences["opacity"] == 55


def test_automatic_cap_changes_density_without_limiting_explicit_manual_count():
    pages = [dict(name="one", width=900, height=200000)]
    common = dict(png_bytes=logo(), enabled_pages=["one"], repeat=True,
                  interval_px=5000, minimum_gap=1000, auto_count_limit=8)
    auto = chapter_watermark_settings(pages, {**common, "auto_count": True})
    explicit = chapter_watermark_settings(pages, {**common, "auto_count": False, "repeat_count": 12})
    assert len(auto["one"]["positions"]) == 8
    assert len(explicit["one"]["positions"]) == 12


def test_preset_button_retains_logo_and_resets_positions_for_full_redistribution():
    from ui.watermark_dialog import WatermarkDialog
    app = QApplication.instance() or QApplication([])
    png = logo()
    dialog = WatermarkDialog(dict(png_bytes=png, source_name="my-logo.png",
                                 scale_percent=43, minimum_gap=5000, interval_px=1,
                                 page_positions={"one": [[3, 6]]}))
    resets = []
    dialog.redistribute_chapter_requested.connect(lambda: resets.append(True))
    dialog.recommended_button.click()
    values = dialog.values()
    assert values["png_bytes"] == png and values["source_name"] == "my-logo.png"
    assert values["scale_percent"] == 22 and values["opacity"] == 55
    assert values["interval_px"] == 7000 and values["auto_count_limit"] == 24
    assert values["page_positions"] == {}
    assert resets == [True]
    dialog.close()
