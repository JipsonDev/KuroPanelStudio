"""The real-page review must count near-white and coloured text remnants too."""
import numpy as np
import pytest

from scripts.evaluate_real_cleaning import flat_residue_count


@pytest.mark.parametrize("colour", [(255, 255, 255), (230, 200, 180), (10, 10, 10)])
def test_flat_residue_counts_faint_marks_with_a_small_noise_tolerance(colour):
    rgb = np.full((4, 5, 3), colour, np.uint8)
    direction = -1 if colour[0] > 128 else 1
    rgb[0, 0] = np.asarray(colour) + direction * 2  # tolerated compression noise
    rgb[1, 1] = np.asarray(colour) + direction * 10  # visible faint text
    rgb[2, 2] = (0, 100, 70)  # coloured remnant
    assert flat_residue_count(rgb, colour) == 2
