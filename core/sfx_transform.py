"""Shared SFX curve, homography and editable 3×3 mesh mathematics."""
from __future__ import annotations

import math
from typing import Callable

import numpy as np


QUAD_DEFAULTS = {
    "tl": (0.0, 0.0), "tr": (100.0, 0.0),
    "bl": (0.0, 100.0), "br": (100.0, 100.0),
}
MESH_NODES = ((0, 1), (1, 0), (1, 1), (1, 2), (2, 1))


def quad_points(style: dict, width: float, height: float) -> dict[str, tuple[float, float]]:
    return {
        corner: (
            float(width) * float(style.get(f"sfx_quad_{corner}_x", default[0])) / 100.0,
            float(height) * float(style.get(f"sfx_quad_{corner}_y", default[1])) / 100.0,
        )
        for corner, default in QUAD_DEFAULTS.items()
    }


def _bilinear_quad(quad: dict[str, tuple[float, float]], u: float, v: float) -> tuple[float, float]:
    tl, tr, bl, br = quad["tl"], quad["tr"], quad["bl"], quad["br"]
    return tuple(
        tl[axis] * (1.0 - u) * (1.0 - v)
        + tr[axis] * u * (1.0 - v)
        + bl[axis] * (1.0 - u) * v
        + br[axis] * u * v
        for axis in (0, 1)
    )


def mesh_node_position(
    style: dict, width: float, height: float, row: int, column: int,
) -> tuple[float, float]:
    quad = quad_points(style, width, height)
    base = _bilinear_quad(quad, column / 2.0, row / 2.0)
    return (
        base[0] + float(width) * float(style.get(f"sfx_mesh_r{row}c{column}_dx", 0.0)) / 100.0,
        base[1] + float(height) * float(style.get(f"sfx_mesh_r{row}c{column}_dy", 0.0)) / 100.0,
    )


def mesh_offset_for_position(
    style: dict, width: float, height: float, row: int, column: int,
    position: tuple[float, float],
) -> tuple[int, int]:
    quad = quad_points(style, width, height)
    base = _bilinear_quad(quad, column / 2.0, row / 2.0)
    return (
        round((float(position[0]) - base[0]) / max(1.0, float(width)) * 100.0),
        round((float(position[1]) - base[1]) / max(1.0, float(height)) * 100.0),
    )


def _homography(quad: dict[str, tuple[float, float]]) -> np.ndarray | None:
    sources = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    targets = (quad["tl"], quad["tr"], quad["br"], quad["bl"])
    matrix, values = [], []
    for (u, v), (x, y) in zip(sources, targets):
        matrix.extend(((u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x),
                       (0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y)))
        values.extend((x, y))
    try:
        coefficients = np.linalg.solve(np.asarray(matrix, np.float64), np.asarray(values, np.float64))
    except np.linalg.LinAlgError:
        return None
    return np.append(coefficients, 1.0).reshape(3, 3)


def build_warp(style: dict, width: float, height: float) -> Callable[[float, float], tuple[float, float]]:
    """Build a unit-square warp used by both the canvas and the exporter."""
    quad = quad_points(style, width, height)
    if not bool(style.get("sfx_mesh_enabled", False)):
        homography = _homography(quad)

        def projective(u: float, v: float) -> tuple[float, float]:
            if homography is None:
                return _bilinear_quad(quad, u, v)
            result = homography @ np.asarray((float(u), float(v), 1.0), np.float64)
            if abs(float(result[2])) < 1e-9:
                return _bilinear_quad(quad, u, v)
            return float(result[0] / result[2]), float(result[1] / result[2])

        return projective

    grid = [[None for _column in range(3)] for _row in range(3)]
    for row in range(3):
        for column in range(3):
            if (row, column) == (0, 0):
                point = quad["tl"]
            elif (row, column) == (0, 2):
                point = quad["tr"]
            elif (row, column) == (2, 0):
                point = quad["bl"]
            elif (row, column) == (2, 2):
                point = quad["br"]
            else:
                point = mesh_node_position(style, width, height, row, column)
            grid[row][column] = point

    def mesh(u: float, v: float) -> tuple[float, float]:
        # Extrapolate outside the box so unconstrained Photoshop-like handles
        # do not clip glyphs dragged beyond the original rectangle.
        scaled_u, scaled_v = float(u) * 2.0, float(v) * 2.0
        column = 0 if scaled_u < 1.0 else 1
        row = 0 if scaled_v < 1.0 else 1
        local_u, local_v = scaled_u - column, scaled_v - row
        p00, p10 = grid[row][column], grid[row][column + 1]
        p01, p11 = grid[row + 1][column], grid[row + 1][column + 1]
        return tuple(
            p00[axis] * (1.0 - local_u) * (1.0 - local_v)
            + p10[axis] * local_u * (1.0 - local_v)
            + p01[axis] * (1.0 - local_u) * local_v
            + p11[axis] * local_u * local_v
            for axis in (0, 1)
        )

    return mesh


def bezier_baseline(style: dict, t: float, width: float, height: float) -> tuple[float, float, float]:
    """Return normalized x, y in pixels and tangent angle for a cubic baseline."""
    value = max(0.0, min(1.0, float(t)))
    if bool(style.get("sfx_bezier_enabled", False)):
        p1 = (
            float(style.get("sfx_bezier_c1_x", 33)) / 100.0,
            float(style.get("sfx_bezier_c1_y", 0)) / 100.0 * float(height),
        )
        p2 = (
            float(style.get("sfx_bezier_c2_x", 67)) / 100.0,
            float(style.get("sfx_bezier_c2_y", 0)) / 100.0 * float(height),
        )
    else:
        curve = float(style.get("sfx_curve", 0)) / 100.0 * float(height) * 0.34
        control_y = -curve / 0.75
        p1, p2 = (1.0 / 3.0, control_y), (2.0 / 3.0, control_y)
    p0, p3 = (0.0, 0.0), (1.0, 0.0)
    one = 1.0 - value
    x = one ** 3 * p0[0] + 3 * one * one * value * p1[0] + 3 * one * value * value * p2[0] + value ** 3 * p3[0]
    y = one ** 3 * p0[1] + 3 * one * one * value * p1[1] + 3 * one * value * value * p2[1] + value ** 3 * p3[1]
    dx = 3 * one * one * (p1[0] - p0[0]) + 6 * one * value * (p2[0] - p1[0]) + 3 * value * value * (p3[0] - p2[0])
    dy = 3 * one * one * (p1[1] - p0[1]) + 6 * one * value * (p2[1] - p1[1]) + 3 * value * value * (p3[1] - p2[1])
    angle = math.degrees(math.atan2(dy, max(1e-9, dx * max(1.0, float(width)))))
    return x, y, angle
