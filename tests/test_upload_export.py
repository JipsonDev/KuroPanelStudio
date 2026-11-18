import zipfile
from types import SimpleNamespace

import numpy as np
from PIL import Image

from core.upload_export import UploadOptions, best_cut, export_for_upload, safe_export_folder


def page(height, gutter_at=None, width=300):
    rng = np.random.default_rng(height)
    rows = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    if gutter_at is not None:
        rows[gutter_at - 10:gutter_at + 10] = 255
    return Image.fromarray(rows)


def test_cuts_land_on_quiet_rows_and_slices_are_numbered(tmp_path):
    pages = [page(1500, gutter_at=1100), page(1500, gutter_at=600)]
    options = UploadOptions(slice_height=1000, format_name="PNG", search=0.2)
    files = export_for_upload(pages, tmp_path / "Cap 185", options, total=2)
    slices, archive = files[:-1], files[-1]
    assert [path.name for path in slices] == ["001.png", "002.png", "003.png"]
    heights = [Image.open(path).height for path in slices]
    assert sum(heights) == 3000
    assert 1090 <= heights[0] <= 1110  # moved to the gutter instead of 1000
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["001.png", "002.png", "003.png"]
    assert archive.name == "Cap 185.zip"


def test_short_tail_joins_the_last_slice_and_widths_match(tmp_path):
    files = export_for_upload([page(2100), page(300, width=600)], tmp_path / "out",
                              UploadOptions(slice_height=1000, format_name="JPG", make_zip=False))
    heights = [Image.open(path).height for path in files]
    assert sum(heights) == 2100 + 150 and all(Image.open(path).width == 300 for path in files)
    assert heights[-1] > 250  # no 50 px sliver


def test_best_cut_prefers_target_on_uniform_rows():
    assert best_cut(np.full((3000, 10, 3), 200, np.uint8), 1000, 200) == 1000


def test_never_overwrites_sources(tmp_path):
    source = tmp_path / "01.png"
    source.write_bytes(b"x")
    pages = [SimpleNamespace(path=source, name="01.png")]
    assert safe_export_folder(tmp_path, pages, ".png") == tmp_path / "Exportado"
    assert safe_export_folder(tmp_path, pages, ".jpg") == tmp_path


def test_cuts_do_not_cross_text_boxes():
    rows = np.full((3000, 50, 3), 255, np.uint8)
    assert best_cut(rows, 1000, 200) == 1000
    cut = best_cut(rows, 1000, 200, [(950, 1080)])
    assert not 950 - 12 <= cut <= 1080 + 12


def test_a_text_block_taller_than_the_window_is_not_cut():
    rows = np.full((6000, 50, 3), 255, np.uint8)
    cut = best_cut(rows, 2000, 400, [(1300, 3750)])
    assert cut == 1300 - 12


def test_a_second_export_replaces_the_first(tmp_path):
    folder = tmp_path / "out"
    options = UploadOptions(slice_height=1000, format_name="PNG", make_zip=False)
    first = export_for_upload([page(6000)], folder, options)
    (folder / "notas.txt").write_text("mías")
    second = export_for_upload([page(2000)], folder, options)
    assert len(first) > len(second)
    assert sorted(path.name for path in folder.iterdir()) == sorted([*(p.name for p in second), "notas.txt"])


def test_no_thin_last_image_and_nothing_is_lost(tmp_path):
    for total in (2100, 2450, 4990, 5250):
        files = export_for_upload([page(total)], tmp_path / str(total),
                                  UploadOptions(slice_height=1000, format_name="PNG", make_zip=False))
        heights = [Image.open(path).height for path in files]
        assert sum(heights) == total and min(heights) >= 250, (total, heights)


def test_empty_destination_is_refused():
    import pytest
    with pytest.raises(ValueError):
        export_for_upload([page(500)], "", UploadOptions())
