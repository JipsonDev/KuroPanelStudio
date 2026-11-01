"""Lightweight defaults shared by preferences and the watermark editor."""


BALANCED_WATERMARK = {
    "size_mode": "percent",
    "scale_percent": 22,
    "opacity": 55,
    "anchor": "middle-right",
    "margin_x": 24,
    "margin_y": 160,
    "offset_x": 0,
    "offset_y": 0,
    "rotation": 0,
    "blend_mode": "normal",
    "repeat": True,
    "distribution": "alternating",
    "minimum_gap": 2500,
    "interval_px": 7000,
    "auto_count": True,
    "auto_count_limit": 24,
    "keep_inside": True,
    "trim_transparent": True,
    "avoid_text": True,
    "seam_safe": True,
}


def balanced_watermark(value: dict | None = None) -> dict:
    """Keep the PNG/scope while explicitly resetting old manual placement."""
    from core.watermark_manager import normalized_watermark
    return normalized_watermark({**dict(value or {}), **BALANCED_WATERMARK,
                                 "page_positions": {}, "positions": None})
