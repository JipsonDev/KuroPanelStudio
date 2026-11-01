"""A chapter must stay virtual; crops/export preserve exact page pixels."""
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from core.project_manager import Page
from core.chapter_strip import ChapterStrip, export_strip_png


def pages_at(tmp_path):
    pages = []
    for i, (size, color) in enumerate([((8, 20), (255, 0, 0)),
                                     ((12, 30), (0, 255, 0)),
                                     ((6, 10), (0, 0, 255))]):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", size, color).save(path)
        pages.append(Page(path.name, path, *size))
    return pages


def test_layout_reads_no_pixels_and_crop_only_opens_intersecting_pages(tmp_path, monkeypatch):
    pages = pages_at(tmp_path)
    opened = []
    from core.chapter_strip import compose_page
    def track(page, *args):
        opened.append(page.name)
        return compose_page(page, *args)
    monkeypatch.setattr("core.chapter_strip.compose_page", track)
    strip = ChapterStrip(pages, cache_bytes=1000)
    assert strip.size == (12, 60)
    assert not opened
    assert strip.page_at(20) == 1

    crop = strip.crop((0, 17, 12, 24))

    assert opened == ["0.png", "1.png"]
    assert crop.size == (12, 7)
    assert crop.getpixel((2, 0)) == (255, 0, 0)
    assert crop.getpixel((0, 0)) == (255, 255, 255)
    assert crop.getpixel((0, 3)) == (0, 255, 0)
    assert strip.cache_stats()["bytes"] <= 1000


def test_strip_export_streams_pages_in_order_without_materializing_chapter(tmp_path, monkeypatch):
    pages = pages_at(tmp_path)
    strip = ChapterStrip(pages, cache_bytes=0)
    monkeypatch.setattr(strip, "crop", lambda _: pytest.fail("Export must stream page rows"))
    progress = []
    output = tmp_path / "chapter.png"

    export_strip_png(output, strip, progress.append)

    with Image.open(output) as image:
        assert image.size == (12, 60)
        assert image.getpixel((3, 19)) == (255, 0, 0)
        assert image.getpixel((3, 20)) == (0, 255, 0)
        assert image.getpixel((3, 50)) == (0, 0, 255)
        assert image.getpixel((0, 50)) == (255, 255, 255)
    assert progress[-1] == 100
    assert strip.cache_stats()["bytes"] == 0


def test_cancelled_strip_export_leaves_existing_destination_and_no_temp_file(tmp_path):
    strip = ChapterStrip(pages_at(tmp_path))
    output = tmp_path / "chapter.png"
    output.write_bytes(b"existing output")
    with pytest.raises(InterruptedError):
        export_strip_png(output, strip, cancelled=lambda: True)
    assert output.read_bytes() == b"existing output"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["0.png", "1.png", "2.png", "chapter.png"]


def test_strip_export_includes_patches_and_exact_page_watermarks(tmp_path):
    pages = pages_at(tmp_path)
    key = str(pages[1].path)
    mark = BytesIO()
    Image.new("RGBA", (2, 2), (60, 70, 80, 255)).save(mark, "PNG")
    strip = ChapterStrip(pages,
        clean_results={key: {"patches": [{"pixels": np.full((2, 2, 3), (90, 100, 110), np.uint8),
                        "mask": np.full((2, 2), 255, np.uint8), "x": 4, "y": 5}]}},
        styles={key: {"watermark": dict(png_bytes=mark.getvalue(), size_mode="pixels", width_px=8,
                        margin_x=0, margin_y=0, opacity=100, positions=[[0, 15]])}})
    output = tmp_path / "chapter.png"
    export_strip_png(output, strip)
    with Image.open(output) as image:
        assert image.getpixel((4, 25)) == (90, 100, 110)
        assert image.getpixel((0, 35)) == (60, 70, 80)


def test_natural_page_order_matches_the_reading_order(tmp_path):
    for name in ("10.png", "2.png", "1.png"):
        Image.new("RGB", (2, 3)).save(tmp_path / name)
    from core.project_manager import ProjectManager
    assert [p.name for p in ProjectManager.scan_folder(tmp_path)] == ["1.png", "2.png", "10.png"]
