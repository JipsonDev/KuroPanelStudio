"""Continuous viewport requests only nearby pixels and rejects stale workers."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time

from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF

from core.chapter_strip import ChapterStrip
from core.project_manager import Page
from ui.strip_view import StripView


def app_wait(app, predicate, timeout=8):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_strip_view_keeps_scene_height_but_only_decodes_nearby_pages(tmp_path):
    app = QApplication.instance() or QApplication([])
    pages = []
    for i in range(24):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", (320, 2400), (i, 100, 200)).save(path)
        pages.append(Page(path.name, path, 320, 2400))
    source = ChapterStrip(pages)
    view = StripView()
    view.resize(500, 600)
    view.show()
    view.set_source(source)
    view.set_zoom(100)
    view.centerOn(160, 300)
    app_wait(app, lambda: bool(view._tiles))
    assert view.sceneRect().height() == 57600
    assert source.cache_stats()["items"] <= 3
    assert view.cache_bytes() <= view.pixel_budget
    view.jump_to_page(23)
    app_wait(app, lambda: view.visible_page() == 23 and any(key[1] > 54000 for key in view._tiles))
    assert source.cache_stats()["items"] < 24
    assert view.cache_bytes() <= view.pixel_budget
    view.dispose()
    assert not view._tiles
    view.close()


def test_strip_watermark_moves_are_saved_in_page_coordinates(tmp_path):
    from io import BytesIO
    from core.chapter_watermarks import chapter_watermark_settings
    app = QApplication.instance() or QApplication([])
    pages = []
    for i in range(2):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", (800, 1000), "white").save(path)
        pages.append(Page(path.name, path, 800, 1000))
    stream = BytesIO()
    Image.new("RGBA", (100, 50), "red").save(stream, "PNG")
    config = dict(png_bytes=stream.getvalue(), repeat=True, auto_count=False,
                  repeat_count=2, enabled_pages=[p.name for p in pages],
                  size_mode="pixels", width_px=100, minimum_gap=100)
    plan = chapter_watermark_settings([dict(name=p.name, width=800, height=1000) for p in pages], config)
    view = StripView()
    view.set_source(ChapterStrip(pages))
    view.set_watermarks(plan)
    assert view._watermarks["0.png"][0].pixmap().cacheKey() == view._watermarks["1.png"][0].pixmap().cacheKey()
    changes = []
    view.watermark_positions_changed.connect(lambda name, positions: changes.append((name, positions)))
    item = view._watermarks["1.png"][0]
    item.setPos(45, 222)
    view._emit_watermark_positions("1.png")
    assert changes == [("1.png", [[45, 222]])]
    assert item.scenePos() == QPointF(45, 1222)
    view.dispose()
    view.close()


def test_replacing_source_during_decode_never_paints_old_chapter(tmp_path):
    from threading import Event
    app = QApplication.instance() or QApplication([])
    paths = [tmp_path / "old.png", tmp_path / "new.png"]
    for path, color in zip(paths, ("red", "green")):
        Image.new("RGB", (320, 900), color).save(path)
    old = ChapterStrip([Page("old.png", paths[0], 320, 900)])
    new = ChapterStrip([Page("new.png", paths[1], 320, 900)])
    started, release = Event(), Event()
    crop = old.crop
    def slow_crop(box):
        started.set()
        assert release.wait(5)
        return crop(box)
    old.crop = slow_crop
    view = StripView()
    view.resize(400, 500)
    view.show()
    view.set_source(old)
    app_wait(app, started.is_set)
    view.set_source(new)
    release.set()
    app_wait(app, lambda: bool(view._tiles))
    for item in view._tiles.values():
        assert item.pixmap().toImage().pixelColor(10, 10).green() == 128
    view.dispose()
    view.close()


def test_zoom_preview_tiles_cover_exact_original_bounds_with_odd_dimensions(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "odd.png"
    Image.new("RGB", (317, 1403), "blue").save(path)
    view = StripView()
    view.resize(400, 500)
    view.show()
    view.set_source(ChapterStrip([Page(path.name, path, 317, 1403)]))
    view.set_zoom(10)
    app_wait(app, lambda: bool(view._tiles))
    item = next(iter(view._tiles.values()))
    rect = item.mapRectToScene(item.boundingRect())
    assert abs(rect.width() - 317) < .001
    assert abs(rect.height() - 1403) < .001
    view.dispose()
    view.close()
