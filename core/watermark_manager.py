"""Non-destructive watermark geometry and full-resolution composition."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from core.watermark_layout import balanced_positions


DEFAULT_WATERMARK = {
    "source_name": "",
    "source_path": "",
    "png_bytes": b"",
    "enabled_pages": [],
    "active": False,
    "page_positions": {},
    "positions": None,
    "size_mode": "percent",
    "scale_percent": 14,
    "width_px": 320,
    "opacity": 65,
    "anchor": "bottom-right",
    "margin_x": 24,
    "margin_y": 24,
    "offset_x": 0,
    "offset_y": 0,
    "rotation": 0,
    "blend_mode": "normal",
    "repeat": False,
    "distribution": "column",
    "minimum_gap": 48,
    "auto_count": True,
    "repeat_count": 3,
    "repeat_spacing_x": 0,
    "repeat_spacing_y": 900,
    "keep_inside": True,
    "visible_preview": True,
    "avoid_text": True,
    "seam_safe": True,
    "avoid_regions": [],
}


def normalized_watermark(value: dict | None) -> dict:
    result = {**DEFAULT_WATERMARK, **dict(value or {})}
    result["source_name"] = str(result.get("source_name", ""))
    result["source_path"] = str(result.get("source_path", ""))
    raw = result.get("png_bytes", b"")
    result["png_bytes"] = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else b""
    result["enabled_pages"] = list(dict.fromkeys(str(page) for page in result.get("enabled_pages", []) if str(page)))
    result["active"] = bool(result.get("active", False))
    page_positions = {}
    for page, positions in dict(result.get("page_positions", {})).items():
        page_positions[str(page)] = [
            [int(position[0]), int(position[1])]
            for position in positions
            if isinstance(position, (list, tuple)) and len(position) >= 2
        ]
    result["page_positions"] = page_positions
    positions = result.get("positions")
    result["positions"] = None if positions is None else [
        [int(position[0]), int(position[1])]
        for position in positions
        if isinstance(position, (list, tuple)) and len(position) >= 2
    ]
    result["size_mode"] = str(result.get("size_mode", "percent"))
    if result["size_mode"] not in {"percent", "pixels"}:
        result["size_mode"] = "percent"
    result["scale_percent"] = max(1, min(100, int(result.get("scale_percent", 14))))
    result["width_px"] = max(8, min(20000, int(result.get("width_px", 320))))
    result["opacity"] = max(0, min(100, int(result.get("opacity", 65))))
    result["anchor"] = str(result.get("anchor", "bottom-right"))
    if result["anchor"] not in {
        "top-left", "top-center", "top-right", "middle-left", "center",
        "middle-right", "bottom-left", "bottom-center", "bottom-right",
    }:
        result["anchor"] = "bottom-right"
    for key in ("margin_x", "margin_y", "repeat_spacing_x", "repeat_spacing_y"):
        result[key] = max(0, min(10000, int(result.get(key, DEFAULT_WATERMARK[key]))))
    for key in ("offset_x", "offset_y"):
        result[key] = max(-20000, min(20000, int(result.get(key, 0))))
    result["rotation"] = max(-180, min(180, int(result.get("rotation", 0))))
    result["blend_mode"] = str(result.get("blend_mode", "normal")).lower()
    if result["blend_mode"] not in {"normal", "multiply", "screen", "overlay"}:
        result["blend_mode"] = "normal"
    result["distribution"] = str(result.get("distribution", "column"))
    if result["distribution"] not in {"column", "alternating"}:
        result["distribution"] = "column"
    result["minimum_gap"] = max(0, min(10000, int(result.get("minimum_gap", 48))))
    result["repeat"] = bool(result.get("repeat", False))
    result["auto_count"] = bool(result.get("auto_count", True))
    result["repeat_count"] = max(1, min(100, int(result.get("repeat_count", 3))))
    result["keep_inside"] = bool(result.get("keep_inside", True))
    result["visible_preview"] = bool(result.get("visible_preview", True))
    result["avoid_text"] = bool(result.get("avoid_text", True))
    result["seam_safe"] = bool(result.get("seam_safe", True))
    result["avoid_regions"] = [
        {
            "x": int(region.get("x", 0)), "y": int(region.get("y", 0)),
            "width": max(0, int(region.get("width", 0))),
            "height": max(0, int(region.get("height", 0))),
        }
        for region in result.get("avoid_regions", []) if isinstance(region, dict)
    ]
    return result


def automatic_watermark_count(page_size: tuple[int, int], mark_size: tuple[int, int]) -> int:
    """Return a conservative count for regular pages and very long webtoons."""
    page_width, page_height = page_size
    _mark_width, mark_height = mark_size
    target_interval = max(1600.0, page_width * 2.5, mark_height * 8.0)
    return max(1, min(12, int(round(page_height / target_interval))))


def watermark_vertical_bounds(page_size, mark_size, config):
    """Shared export/drag limits, reducing excessive margins symmetrically."""
    available = max(0, page_size[1] - mark_size[1])
    if config.get("chapter_resolved"):
        return 0, available
    edge = config["margin_y"]
    if config["repeat"] and config["seam_safe"]:
        edge = max(edge, mark_size[1] * 2, min(400, round(page_size[0] * .12)))
    edge = min(edge, available // 2)
    return edge, available - edge


def watermark_bytes(value: dict | None) -> bytes:
    config = normalized_watermark(value)
    if config["png_bytes"]:
        return config["png_bytes"]
    path = Path(config["source_path"]) if config["source_path"] else None
    try:
        return path.read_bytes() if path and path.is_file() else b""
    except OSError:
        return b""


def load_watermark_image(value: dict | None) -> Image.Image | None:
    payload = watermark_bytes(value)
    if not payload:
        return None
    try:
        with Image.open(BytesIO(payload)) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def prepare_watermark(page_size: tuple[int, int], value: dict | None) -> Image.Image | None:
    config = normalized_watermark(value)
    source = load_watermark_image(config)
    if source is None or source.width <= 0 or source.height <= 0:
        return None
    page_width, _page_height = page_size
    target_width = (
        max(1, round(page_width * config["scale_percent"] / 100.0))
        if config["size_mode"] == "percent" else config["width_px"]
    )
    target_height = max(1, round(source.height * target_width / source.width))
    source = source.resize((target_width, target_height), Image.Resampling.LANCZOS)
    if config["rotation"]:
        source = source.rotate(-config["rotation"], expand=True, resample=Image.Resampling.BICUBIC)
    if config["keep_inside"]:
        # Rotation can make the requested width taller than a short page.
        available_width = max(1, page_width - 2 * min(config["margin_x"], (page_width - 1) // 2))
        available_height = max(1, _page_height - 2 * min(config["margin_y"], (_page_height - 1) // 2))
        source.thumbnail((available_width, available_height), Image.Resampling.LANCZOS)
    if config["opacity"] < 100:
        source.putalpha(source.getchannel("A").point(lambda alpha: alpha * config["opacity"] // 100))
    return source


def watermark_positions(
    page_size: tuple[int, int], mark_size: tuple[int, int], value: dict | None,
) -> list[tuple[int, int]]:
    config = normalized_watermark(value)
    page_width, page_height = page_size
    mark_width, mark_height = mark_size
    if config.get("positions") is not None:
        positions = [(int(position[0]), int(position[1])) for position in config["positions"]]
        if config["keep_inside"]:
            lower, upper = watermark_vertical_bounds(page_size, mark_size, config)
            positions = [(max(0, min(x, max(0, page_width - mark_width))),
                          max(lower, min(y, upper))) for x, y in positions]
        return positions
    if config["keep_inside"] and (mark_width > page_width or mark_height > page_height):
        return []
    if config["repeat"]:
        horizontal = config["anchor"].split("-")[-1] if "-" in config["anchor"] else "center"
        left = config["margin_x"] + config["offset_x"]
        right = page_width - config["margin_x"] - mark_width + config["offset_x"]
        x = left if horizontal == "left" else right if horizontal == "right" else (page_width - mark_width) // 2 + config["offset_x"]
        if config["keep_inside"]:
            def inside(position):
                return max(0, min(position, max(0, page_width - mark_width)))
            x, left, right = inside(x), inside(left), inside(right)
        lower, upper = watermark_vertical_bounds(page_size, mark_size, config)
        if not config["keep_inside"]:
            lower += config["offset_y"]
            upper += config["offset_y"]
        count = automatic_watermark_count(page_size, mark_size) if config["auto_count"] else config["repeat_count"]
        gap = config["minimum_gap"]
        count = min(count, 1 + (upper - lower) // max(1, mark_height + gap))
        regions = config["avoid_regions"] if config["avoid_text"] else []
        # Reduce only when no complete placement exists, then redistribute the
        # remaining count over the full usable height instead of dropping a slot.
        for number in range(count, 0, -1):
            if number == 1:
                vertical = config["anchor"].split("-")[0] if "-" in config["anchor"] else "middle"
                ideals = [lower if vertical == "top" else upper if vertical == "bottom" else (lower + upper) / 2]
            else:
                ideals = [lower + (upper - lower) * index / (number - 1) for index in range(number)]
            if config["keep_inside"]:
                ideals = [max(lower, min(y + config["offset_y"], upper)) for y in ideals]
            xs = [x] * number
            if config["distribution"] == "alternating" and number > 1:
                sides = (left, right) if horizontal == "left" else (right, left)
                xs = [sides[index % 2] for index in range(number)]
            placed = balanced_positions(xs, ideals, (lower, upper), mark_size, regions, gap)
            if placed is not None:
                return placed
        return []

    horizontal, vertical = {
        "top-left": ("left", "top"), "top-center": ("center", "top"), "top-right": ("right", "top"),
        "middle-left": ("left", "middle"), "center": ("center", "middle"), "middle-right": ("right", "middle"),
        "bottom-left": ("left", "bottom"), "bottom-center": ("center", "bottom"), "bottom-right": ("right", "bottom"),
    }[config["anchor"]]
    if horizontal == "left":
        x = config["margin_x"]
    elif horizontal == "right":
        x = page_width - config["margin_x"] - mark_width
    else:
        x = (page_width - mark_width) // 2
    if vertical == "top":
        y = config["margin_y"]
    elif vertical == "bottom":
        y = page_height - config["margin_y"] - mark_height
    else:
        y = (page_height - mark_height) // 2
    x += config["offset_x"]; y += config["offset_y"]
    if config["keep_inside"]:
        x = max(0, min(x, max(0, page_width - mark_width)))
        y = max(0, min(y, max(0, page_height - mark_height)))
    positions = [(int(x), int(y))]
    if config["avoid_text"] and config["avoid_regions"]:
        bounds = watermark_vertical_bounds(page_size, mark_size, config) if config["keep_inside"] else (min(0, y), max(page_height - mark_height, y))
        return balanced_positions([int(x)], [int(y)], bounds, mark_size, config["avoid_regions"], 0) or []
    return positions


def _paste_blended(base: Image.Image, overlay: Image.Image, position: tuple[int, int], mode: str) -> None:
    x, y = position
    left, top = max(0, x), max(0, y)
    right, bottom = min(base.width, x + overlay.width), min(base.height, y + overlay.height)
    if right <= left or bottom <= top:
        return
    layer = overlay.crop((left - x, top - y, right - x, bottom - y)).convert("RGBA")
    if mode == "normal":
        base.paste(layer, (left, top), layer)
        return
    bottom_rgb = np.asarray(base.crop((left, top, right, bottom)).convert("RGB"), dtype=np.float32) / 255.0
    top_rgb = np.asarray(layer.convert("RGB"), dtype=np.float32) / 255.0
    alpha = np.asarray(layer.getchannel("A"), dtype=np.float32)[:, :, None] / 255.0
    if mode == "multiply":
        mixed = bottom_rgb * top_rgb
    elif mode == "screen":
        mixed = 1.0 - (1.0 - bottom_rgb) * (1.0 - top_rgb)
    else:
        mixed = np.where(bottom_rgb <= 0.5, 2 * bottom_rgb * top_rgb, 1 - 2 * (1 - bottom_rgb) * (1 - top_rgb))
    result = np.clip((bottom_rgb * (1 - alpha) + mixed * alpha) * 255, 0, 255).astype(np.uint8)
    base.paste(Image.fromarray(result, "RGB"), (left, top))


def compose_watermark(image: Image.Image, value: dict | None) -> bool:
    config = normalized_watermark(value)
    mark = prepare_watermark(image.size, config)
    if mark is None or config["opacity"] <= 0:
        return False
    positions = watermark_positions(image.size, mark.size, config)
    for position in positions:
        _paste_blended(image, mark, position, config["blend_mode"])
    return bool(positions)
