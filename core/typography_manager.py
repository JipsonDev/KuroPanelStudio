"""Typography defaults, reading order and reusable style helpers."""
from __future__ import annotations


DEFAULT_STYLE = {
    "font_family": "Segoe UI",
    "font_file": "",
    "font_size": 36,
    "font_weight": 400,
    "italic": False,
    "underline": False,
    "strikeout": False,
    "text_case": "original",
    # New dialogue boxes follow the detected interior. Explicitly disabling
    # either option keeps a manually composed layer under the user's control.
    "auto_fit": True,
    "balloon_fit": True,
    "balloon_shape": "auto",
    "balloon_padding": 10,
    "language": "auto",
    "hyphenation": True,
    "orphan_control": True,
    "hanging_punctuation": True,
    "auto_scale": True,
    "max_horizontal_compression": 12,
    "scale_x": 100,
    "scale_y": 100,
    "line_spacing": 4,
    "alignment": "center",
    "vertical_alignment": "center",
    "optical_center": True,
    "stroke_width": 0,
    "stroke_color": "#FFFFFF",
    "stroke2_enabled": False,
    "stroke2_width": 6,
    "stroke2_color": "#111111",
    "gradient_enabled": False,
    "gradient_start": "#FFFFFF",
    "gradient_end": "#00CFE8",
    "gradient_angle": 90,
    "glow_enabled": False,
    "glow_color": "#00CFE8",
    "glow_radius": 10,
    "glow_opacity": 65,
    "shadow_enabled": False,
    "shadow_color": "#000000",
    "shadow_blur": 8,
    "shadow_offset_x": 4,
    "shadow_offset_y": 6,
    "shadow_opacity": 65,
    "blend_mode": "normal",
    "text_color": "#111111",
    "rotation": 0,
    "vertical_text": False,
    "text_margin": 8,
    "sfx_enabled": False,
    "sfx_auto_layout": True,
    "sfx_preset": "Personalizado",
    "sfx_curve": 0,
    "sfx_wave": 0,
    "sfx_wave_cycles": 2,
    "sfx_skew": 0,
    "sfx_expansion": 0,
    "sfx_impact": 0,
    "sfx_node_tl": 0,
    "sfx_node_tr": 0,
    "sfx_node_bl": 0,
    "sfx_node_br": 0,
    "sfx_node_tl_x": 0,
    "sfx_node_tr_x": 0,
    "sfx_node_bl_x": 0,
    "sfx_node_br_x": 0,
    "sfx_quad_tl_x": 0,
    "sfx_quad_tl_y": 0,
    "sfx_quad_tr_x": 100,
    "sfx_quad_tr_y": 0,
    "sfx_quad_bl_x": 0,
    "sfx_quad_bl_y": 100,
    "sfx_quad_br_x": 100,
    "sfx_quad_br_y": 100,
    "sfx_quad_version": 1,
    "sfx_bezier_enabled": False,
    "sfx_bezier_c1_x": 33,
    "sfx_bezier_c1_y": 0,
    "sfx_bezier_c2_x": 67,
    "sfx_bezier_c2_y": 0,
    "sfx_mesh_enabled": False,
    "sfx_mesh_r0c1_dx": 0,
    "sfx_mesh_r0c1_dy": 0,
    "sfx_mesh_r1c0_dx": 0,
    "sfx_mesh_r1c0_dy": 0,
    "sfx_mesh_r1c1_dx": 0,
    "sfx_mesh_r1c1_dy": 0,
    "sfx_mesh_r1c2_dx": 0,
    "sfx_mesh_r1c2_dy": 0,
    "sfx_mesh_r2c1_dx": 0,
    "sfx_mesh_r2c1_dy": 0,
    "sfx_shape": False,
}

QUICK_TYPOGRAPHY_PRESETS = {
    "dialogue": {
        "name": "Diálogo Normal",
        "font_family": "Segoe UI",
        "font_weight": 600,
        "font_size": 36,
        "italic": False,
        "text_case": "original",
        "alignment": "center",
        "stroke_width": 2,
        "stroke_color": "#FFFFFF",
        "text_color": "#111111",
        "auto_fit": True,
        "balloon_fit": True,
        "balloon_shape": "auto",
        "optical_center": True,
        "balloon_padding": 10,
        "stroke2_enabled": False,
        "gradient_enabled": False,
        "glow_enabled": False,
        "shadow_enabled": False,
    },
    "shout": {
        "name": "Grito / Acción",
        "font_family": "Arial",
        "font_weight": 900,
        "font_size": 46,
        "italic": False,
        "text_case": "upper",
        "alignment": "center",
        "stroke_width": 3,
        "stroke_color": "#111111",
        "text_color": "#FFFFFF",
        "stroke2_enabled": True,
        "stroke2_width": 7,
        "stroke2_color": "#FFFFFF",
        "auto_fit": True,
        "balloon_fit": True,
        "balloon_shape": "diamond",
        "optical_center": True,
        "balloon_padding": 8,
        "gradient_enabled": False,
        "glow_enabled": False,
        "shadow_enabled": False,
    },
    "thought": {
        "name": "Pensamiento",
        "font_family": "Georgia",
        "font_weight": 400,
        "font_size": 32,
        "italic": True,
        "text_case": "original",
        "alignment": "center",
        "stroke_width": 1,
        "stroke_color": "#FFFFFF",
        "text_color": "#222222",
        "glow_enabled": True,
        "glow_color": "#FFFFFF",
        "glow_radius": 6,
        "glow_opacity": 45,
        "auto_fit": True,
        "balloon_fit": True,
        "balloon_shape": "auto",
        "optical_center": True,
        "balloon_padding": 12,
        "stroke2_enabled": False,
        "gradient_enabled": False,
        "shadow_enabled": False,
    },
    "whisper": {
        "name": "Susurro",
        "font_family": "Segoe UI",
        "font_weight": 300,
        "font_size": 28,
        "italic": True,
        "text_case": "original",
        "alignment": "center",
        "stroke_width": 1,
        "stroke_color": "#FFFFFF",
        "text_color": "#555555",
        "auto_fit": True,
        "balloon_fit": True,
        "balloon_shape": "auto",
        "optical_center": True,
        "balloon_padding": 14,
        "stroke2_enabled": False,
        "gradient_enabled": False,
        "glow_enabled": False,
        "shadow_enabled": False,
    },
    "narrator": {
        "name": "Narración / Cuadro",
        "font_family": "Segoe UI",
        "font_weight": 700,
        "font_size": 32,
        "italic": False,
        "text_case": "original",
        "alignment": "center",
        "stroke_width": 0,
        "text_color": "#111111",
        "shadow_enabled": True,
        "shadow_color": "#000000",
        "shadow_blur": 3,
        "shadow_offset_x": 2,
        "shadow_offset_y": 3,
        "shadow_opacity": 40,
        "balloon_fit": False,
        "balloon_shape": "rectangle",
        "balloon_padding": 8,
        "stroke2_enabled": False,
        "gradient_enabled": False,
        "glow_enabled": False,
    },
}

EFFECT_STYLE_KEYS = (
    "stroke_width", "stroke_color", "stroke2_enabled", "stroke2_width", "stroke2_color",
    "gradient_enabled", "gradient_start",
    "gradient_end", "gradient_angle", "glow_enabled", "glow_color",
    "glow_radius", "glow_opacity", "shadow_enabled", "shadow_color", "shadow_blur",
    "shadow_offset_x", "shadow_offset_y", "shadow_opacity", "blend_mode",
)


class TypographyManager:
    @staticmethod
    def assign_font_role_defaults(
        regions: list[dict], alias: str, family: str, font_file: str = "",
        role_style: dict | None = None,
    ) -> list[dict]:
        """Assign a project role only to boxes without an explicit font.

        A manual font choice is represented by ``style.font_family`` even
        when ``font_role`` is empty, so it must never be replaced here.
        """
        if not alias or not family:
            return regions
        for region in regions:
            style = region.get("style")
            explicit_family = str(style.get("font_family", "")).strip() if isinstance(style, dict) else ""
            if str(region.get("font_role", "")).strip() or explicit_family:
                continue
            region["font_role"] = alias
            region.setdefault("style", {}).update({
                **dict(role_style or {}),
                "font_family": family,
                "font_file": font_file,
            })
        return regions

    @staticmethod
    def normalized(style: dict | None) -> dict:
        source = dict(style or {})
        result = {**DEFAULT_STYLE, **source}
        result["font_size"] = max(6, min(300, int(result["font_size"])))
        # Migrate the old canvas-only `bold` flag while keeping new styles explicit.
        if "font_weight" not in dict(style or {}) and "bold" in dict(style or {}):
            result["font_weight"] = 700 if bool(result.get("bold")) else 400
        result["font_weight"] = min(
            (100, 200, 300, 400, 500, 600, 700, 800, 900),
            key=lambda weight: abs(weight - int(result.get("font_weight", 400))),
        )
        result["italic"] = bool(result.get("italic", False))
        result["underline"] = bool(result.get("underline", False))
        result["strikeout"] = bool(result.get("strikeout", False))
        result["text_case"] = str(result.get("text_case", "original"))
        if result["text_case"] not in {"original", "upper", "lower"}:
            result["text_case"] = "original"
        result.pop("bold", None)
        result["line_spacing"] = max(-10, min(100, int(result["line_spacing"])))
        result["auto_fit"] = bool(result.get("auto_fit", False))
        result["balloon_fit"] = bool(result.get("balloon_fit", False))
        result["balloon_shape"] = str(result.get("balloon_shape", "auto")).lower()
        if result["balloon_shape"] not in {"auto", "ellipse", "diamond", "rectangle"}:
            result["balloon_shape"] = "auto"
        result["balloon_padding"] = max(2, min(80, int(result.get("balloon_padding", 10))))
        result["language"] = str(result.get("language", "auto")).lower()
        if result["language"] not in {"auto", "es", "zh", "ja", "ko"}:
            result["language"] = "auto"
        result["hyphenation"] = bool(result.get("hyphenation", False))
        result["orphan_control"] = bool(result.get("orphan_control", True))
        result["hanging_punctuation"] = bool(result.get("hanging_punctuation", True))
        result["auto_scale"] = bool(result.get("auto_scale", True))
        result["max_horizontal_compression"] = max(0, min(20, int(result.get("max_horizontal_compression", 12))))
        result["scale_x"] = max(50, min(200, int(result.get("scale_x", 100))))
        result["scale_y"] = max(50, min(200, int(result.get("scale_y", 100))))
        # A layer uses exactly the selected family. Discard the legacy field
        # when opening projects that previously stored alternative families.
        result.pop("fallback_fonts", None)
        result["stroke_width"] = max(0, min(20, int(result["stroke_width"])))
        result["gradient_enabled"] = bool(result.get("gradient_enabled", False))
        result["stroke2_enabled"] = bool(result.get("stroke2_enabled", False))
        result["stroke2_width"] = max(1, min(40, int(result.get("stroke2_width", 6))))
        result["gradient_angle"] = int(result.get("gradient_angle", 90)) % 360
        result["glow_enabled"] = bool(result.get("glow_enabled", False))
        result["glow_radius"] = max(1, min(80, int(result.get("glow_radius", 10))))
        result["glow_opacity"] = max(0, min(100, int(result.get("glow_opacity", 65))))
        result["shadow_enabled"] = bool(result.get("shadow_enabled", False))
        result["shadow_blur"] = max(0, min(80, int(result.get("shadow_blur", 8))))
        result["shadow_offset_x"] = max(-100, min(100, int(result.get("shadow_offset_x", 4))))
        result["shadow_offset_y"] = max(-100, min(100, int(result.get("shadow_offset_y", 6))))
        result["shadow_opacity"] = max(0, min(100, int(result.get("shadow_opacity", 65))))
        result["blend_mode"] = str(result.get("blend_mode", "normal")).lower()
        if result["blend_mode"] not in {"normal", "multiply", "screen", "overlay"}:
            result["blend_mode"] = "normal"
        for color_key, fallback in (
            ("gradient_start", "#FFFFFF"), ("gradient_end", "#00CFE8"),
            ("glow_color", "#00CFE8"), ("stroke_color", "#FFFFFF"),
            ("stroke2_color", "#111111"), ("shadow_color", "#000000"),
        ):
            color = str(result.get(color_key, fallback)).strip().upper()
            result[color_key] = color if len(color) in {4, 7, 9} and color.startswith("#") else fallback
        result["rotation"] = max(-180, min(180, int(result["rotation"])))
        result["text_margin"] = max(0, min(200, int(result["text_margin"])))
        result["optical_center"] = bool(result.get("optical_center", False))
        result["sfx_enabled"] = bool(result.get("sfx_enabled", False))
        result["sfx_auto_layout"] = bool(result.get("sfx_auto_layout", True))
        result["sfx_preset"] = str(result.get("sfx_preset", "Personalizado"))
        for key, low, high, fallback in (
            ("sfx_curve", -100, 100, 0), ("sfx_wave", 0, 100, 0),
            ("sfx_wave_cycles", 1, 8, 2), ("sfx_skew", -60, 60, 0),
            ("sfx_expansion", -80, 150, 0), ("sfx_impact", -100, 100, 0),
            ("sfx_node_tl", -100, 100, 0), ("sfx_node_tr", -100, 100, 0),
            ("sfx_node_bl", -100, 100, 0), ("sfx_node_br", -100, 100, 0),
            ("sfx_node_tl_x", -100, 100, 0), ("sfx_node_tr_x", -100, 100, 0),
            ("sfx_node_bl_x", -100, 100, 0), ("sfx_node_br_x", -100, 100, 0),
        ):
            result[key] = max(low, min(high, int(result.get(key, fallback))))
        quad_defaults = {
            "tl": (0, 0), "tr": (100, 0), "bl": (0, 100), "br": (100, 100),
        }
        # Migrate the former ±100 quarter-box offsets once. New coordinates
        # are direct percentages of the layer box and can extend far outside
        # it, matching a Photoshop-style free-transform quadrilateral.
        has_free_quad = int(source.get("sfx_quad_version", 0)) >= 1 or any(
            f"sfx_quad_{corner}_{axis}" in source
            for corner in quad_defaults for axis in ("x", "y")
        )
        for corner, (base_x, base_y) in quad_defaults.items():
            if has_free_quad:
                value_x = result.get(f"sfx_quad_{corner}_x", base_x)
                value_y = result.get(f"sfx_quad_{corner}_y", base_y)
            else:
                value_x = base_x + float(result.get(f"sfx_node_{corner}_x", 0)) * 0.25
                value_y = base_y + float(result.get(f"sfx_node_{corner}", 0)) * 0.25
            result[f"sfx_quad_{corner}_x"] = max(-5000, min(5000, int(round(float(value_x)))))
            result[f"sfx_quad_{corner}_y"] = max(-5000, min(5000, int(round(float(value_y)))))
        result["sfx_quad_version"] = 1
        result["sfx_bezier_enabled"] = bool(result.get("sfx_bezier_enabled", False))
        for key, fallback in (
            ("sfx_bezier_c1_x", 33), ("sfx_bezier_c1_y", 0),
            ("sfx_bezier_c2_x", 67), ("sfx_bezier_c2_y", 0),
        ):
            result[key] = max(-5000, min(5000, int(result.get(key, fallback))))
        result["sfx_mesh_enabled"] = bool(result.get("sfx_mesh_enabled", False))
        for row, column in ((0, 1), (1, 0), (1, 1), (1, 2), (2, 1)):
            for axis in ("dx", "dy"):
                key = f"sfx_mesh_r{row}c{column}_{axis}"
                result[key] = max(-5000, min(5000, int(result.get(key, 0))))
        result["sfx_shape"] = bool(result.get("sfx_shape", False))
        return result

    @staticmethod
    def optical_offset(font, text: str) -> tuple[float, float]:
        """Compensate actual glyph ink instead of centring the font's em box."""
        from PySide6.QtGui import QFontMetricsF

        sample = "".join(str(text).splitlines()).strip()
        if not sample:
            return 0.0, 0.0
        metrics = QFontMetricsF(font)
        ink = metrics.tightBoundingRect(sample[:256])
        logical_center_y = (metrics.descent() - metrics.ascent()) / 2.0
        dy = logical_center_y - ink.center().y()
        advance = metrics.horizontalAdvance(sample[:256])
        dx = advance / 2.0 - ink.center().x()
        # Side-bearing compensation is intentionally subtle; it must never
        # make a centred paragraph appear horizontally displaced.
        limit = max(1.0, metrics.height() * 0.16)
        return max(-limit, min(limit, dx)), max(-limit, min(limit, dy))

    @staticmethod
    def ordered(regions: list[dict], manga_mode: bool = False) -> list[dict]:
        """Stable panel reading order: top-to-bottom, then horizontal direction."""
        if not regions:
            return []
        typical_height = max(1, int(sorted(max(1, int(r["height"])) for r in regions)[len(regions) // 2]))
        row_height = max(12, typical_height // 2)
        top = min(int(region["y"]) for region in regions)
        return sorted(
            regions,
            key=lambda region: (
                (int(region["y"]) - top) // row_height,
                -int(region["x"]) if manga_mode else int(region["x"]),
                int(region["y"]),
            ),
        )

    @staticmethod
    def apply_preset(region: dict, preset: dict, preset_name: str = "") -> dict:
        updated = dict(region)
        updated["style"] = TypographyManager.normalized(preset)
        if preset_name:
            updated["style_preset"] = preset_name
        return updated
