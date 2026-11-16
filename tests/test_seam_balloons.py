from PIL import Image, ImageDraw

from core.seam_balloons import anchor_first, seam_pairs, stitch, stitched_file


def region(x, y, w, h, name):
    return {"id": name, "x": x, "y": y, "width": w, "height": h}


def test_only_boxes_cut_by_the_same_join_are_paired():
    cut_top = region(100, 900, 200, 100, "a")
    elsewhere = region(500, 950, 100, 50, "b")
    cut_bottom = region(120, 0, 190, 60, "c")
    unrelated_top = region(700, 0, 80, 40, "d")
    pages = [("p1", (800, 1000), [cut_top, elsewhere]), ("p2", (800, 1200), [cut_bottom, unrelated_top])]
    pairs = seam_pairs(pages)
    assert [(a[1]["id"], b[1]["id"]) for a, b in pairs] == [("a", "c")]
    # A page of another width is a different strip.
    assert seam_pairs([pages[0], ("p2", (700, 1200), [cut_bottom])]) == []


def test_halves_are_joined_in_place_and_cached(tmp_path):
    upper_image = Image.new("RGB", (400, 300), "gray")
    ImageDraw.Draw(upper_image).rectangle((100, 250, 199, 299), fill="red")
    lower_image = Image.new("RGB", (400, 300), "gray")
    ImageDraw.Draw(lower_image).rectangle((100, 0, 199, 39), fill="blue")
    upper, lower = region(100, 250, 100, 50, "u"), region(100, 0, 100, 40, "l")
    joined = stitch(upper_image, upper, lower_image, lower)
    assert joined.size == (100, 90)
    assert joined.getpixel((50, 10)) == (255, 0, 0) and joined.getpixel((50, 80)) == (0, 0, 255)
    upper_image.save(tmp_path / "1.png"); lower_image.save(tmp_path / "2.png")
    first = stitched_file(tmp_path / "1.png", upper, tmp_path / "2.png", lower, tmp_path / "seams")
    stamp = first.stat().st_mtime_ns
    assert stitched_file(tmp_path / "1.png", upper, tmp_path / "2.png", lower, tmp_path / "seams") == first
    assert first.stat().st_mtime_ns == stamp
    assert anchor_first(upper, lower) == (upper, lower)


def test_chapter_ocr_reads_a_cut_balloon_once(tmp_path):
    import pytest
    pytest.importorskip("PySide6")
    from types import SimpleNamespace
    from core.ocr_manager import OCRManager
    from core.project_io import page_key
    from ui.main_window import MainWindow

    paths = []
    for number in (1, 2):
        path = tmp_path / f"{number}.png"
        Image.new("RGB", (400, 300), "white").save(path)
        paths.append(path)
    pages = [SimpleNamespace(path=path) for path in paths]
    upper, lone = region(100, 250, 100, 50, "u"), region(10, 10, 50, 50, "x")
    lower = region(110, 0, 90, 40, "l")
    work = [(pages[0], [lone, upper]), (pages[1], [lower])]
    sent = []

    def run_regions(path, regions, *args, **kwargs):
        sent.append(path)
        return [{**regions[0], "text": "UN SOLO GLOBO"}]

    fake = SimpleNamespace(project=SimpleNamespace(pages=pages), ocr=SimpleNamespace(run_regions=run_regions))
    jobs = MainWindow._chapter_seam_jobs(fake, work)
    assert work[0][1] == [lone] and work[1][1] == [] and len(jobs) == 1
    MainWindow._read_seam_balloon(fake, *jobs[0], "Gemini", "m", "k", lambda: False)
    assert upper["text"] == "UN SOLO GLOBO" and lower["text"] == ""
    assert upper["seam_partner"] == "l" and lower["seam_partner"] == "u"
    assert len(sent) == 1 and not upper.get(OCRManager.FAILED_KEY)
    assert page_key(pages[0]) != page_key(pages[1])
