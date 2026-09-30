"""Full-resolution, decoration-free page composition and export."""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path

import numpy as np

from core.retouch_layers import normalized_layer_states, patch_layer
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from core.project_manager import Page
from core.psd_manager import load_source_image
from core.balloon_typesetter import (
    balloon_search_rect, effective_balloon_padding, fit_balanced_text,
    text_layout_geometry,
)
from core.sfx_layout import automatic_sfx_lines
from core.sfx_transform import bezier_baseline, build_warp
from core.text_layout import fit_rectangular_text, layout_signature, valid_balloon_snapshot, valid_snapshot
from core.typography_manager import TypographyManager
from core.watermark_manager import compose_watermark


@lru_cache(maxsize=1)
def _installed_windows_fonts() -> tuple[tuple[str, Path], ...]:
    """Resolve Windows family/style names to files once per process."""
    try:
        import winreg
    except ImportError:
        return ()
    entries: list[tuple[str, Path]] = []
    fonts_root = Path("C:/Windows/Fonts")
    for hive, key_name in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ):
        try:
            with winreg.OpenKey(hive, key_name) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    display_name, filename, _kind = winreg.EnumValue(key, index)
                    path = Path(filename)
                    if not path.is_absolute():
                        path = fonts_root / path
                    if path.is_file():
                        entries.append((display_name.casefold(), path))
        except OSError:
            continue
    return tuple(entries)


def _font_path(family: str | None = None, weight: int = 400, italic: bool = False) -> Path | None:
    fonts = Path("C:/Windows/Fonts")
    family_key = (family or "").casefold().strip()
    if family_key:
        matches = [(name, path) for name, path in _installed_windows_fonts() if name.startswith(family_key)]
        if matches:
            wants_bold = weight >= 600
            wants_black = weight >= 850

            def score(entry: tuple[str, Path]) -> int:
                name = entry[0]
                is_italic = "italic" in name or "oblique" in name
                is_bold = any(token in name for token in ("semibold", "demibold", "bold", "black", "heavy"))
                is_black = "black" in name or "heavy" in name
                value = 40 if name.startswith(family_key + " ") or name.startswith(family_key + "(") else 20
                value += 25 if is_italic == italic else -25
                value += 20 if is_bold == wants_bold else -15
                value += 12 if is_black == wants_black else 0
                return value

            return max(matches, key=score)[1]
    candidates = []
    if family:
        candidates.extend([fonts / family, fonts / f"{family}.ttf", fonts / f"{family}.ttc"])
    if weight >= 600 and italic:
        candidates.extend([fonts / "segoeuiz.ttf", fonts / "arialbi.ttf"])
    elif weight >= 600:
        candidates.extend([fonts / "segoeuib.ttf", fonts / "arialbd.ttf"])
    elif italic:
        candidates.extend([fonts / "segoeuii.ttf", fonts / "ariali.ttf"])
    else:
        candidates.extend([fonts / "segoeui.ttf", fonts / "arial.ttf", fonts / "msyh.ttc"])
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _font(
    size: int,
    family: str | None = None,
    font_file: str | None = None,
    weight: int = 400,
    italic: bool = False,
):
    custom = Path(font_file) if font_file else None
    path = custom if custom and custom.is_file() else _font_path(family, weight, italic)
    if path:
        return ImageFont.truetype(str(path), size=max(6, size))
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        units = words if len(words) > 1 else list(paragraph)
        current = ""
        separator = " " if len(words) > 1 else ""
        for unit in units:
            candidate = unit if not current else current + separator + unit
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = unit
        if current:
            lines.append(current)
    return "\n".join(lines)


def _gradient_image(width: int, height: int, start: str, end: str, angle: float) -> Image.Image:
    radians = math.radians(float(angle))
    direction_x, direction_y = math.cos(radians), math.sin(radians)
    yy, xx = np.mgrid[0:max(1, height), 0:max(1, width)]
    projection = xx * direction_x + yy * direction_y
    minimum, maximum = float(projection.min()), float(projection.max())
    ratio = (projection - minimum) / max(1e-6, maximum - minimum)
    first = np.asarray(ImageColor.getrgb(start), dtype=np.float32)
    second = np.asarray(ImageColor.getrgb(end), dtype=np.float32)
    rgb = first[None, None, :] * (1.0 - ratio[:, :, None]) + second[None, None, :] * ratio[:, :, None]
    rgba = np.dstack((np.clip(rgb, 0, 255).astype(np.uint8), np.full((height, width), 255, np.uint8)))
    return Image.fromarray(rgba, "RGBA")


def _paste_with_blend(image: Image.Image, overlay: Image.Image, position: tuple[int, int], mode: str) -> None:
    """Alpha composite an RGBA text layer using common professional blend modes."""
    x, y = position
    left, top = max(0, x), max(0, y)
    right, bottom = min(image.width, x + overlay.width), min(image.height, y + overlay.height)
    if right <= left or bottom <= top:
        return
    layer = overlay.crop((left - x, top - y, right - x, bottom - y)).convert("RGBA")
    if mode == "normal":
        image.paste(layer, (left, top), layer)
        return
    base = np.asarray(image.crop((left, top, right, bottom)).convert("RGB"), dtype=np.float32) / 255.0
    top_rgb = np.asarray(layer.convert("RGB"), dtype=np.float32) / 255.0
    alpha = np.asarray(layer.getchannel("A"), dtype=np.float32)[:, :, None] / 255.0
    if mode == "multiply":
        blended = base * top_rgb
    elif mode == "screen":
        blended = 1.0 - (1.0 - base) * (1.0 - top_rgb)
    elif mode == "overlay":
        blended = np.where(base <= 0.5, 2.0 * base * top_rgb, 1.0 - 2.0 * (1.0 - base) * (1.0 - top_rgb))
    else:
        blended = top_rgb
    result = np.clip((base * (1.0 - alpha) + blended * alpha) * 255.0, 0, 255).astype(np.uint8)
    image.paste(Image.fromarray(result, "RGB"), (left, top))


def _draw_sfx_text(image: Image.Image, region: dict, style: dict, text: str) -> None:
    """Rasterize the same per-glyph vector trajectory used by the canvas."""
    x, y = int(region["x"]), int(region["y"])
    width, height = max(1, int(region["width"])), max(1, int(region["height"]))
    primary = str(style.get("font_family", "Segoe UI"))
    family = primary
    font = _font(
        max(6, int(style.get("font_size", 64))), family, style.get("font_file"),
        int(style.get("font_weight", 900)), bool(style.get("italic", False)),
    )
    scratch = ImageDraw.Draw(image)
    margin = max(0, int(style.get("text_margin", 8)))
    pad = max(48, int(style.get("font_size", 64)))
    layer = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    fill_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    probe = scratch.textbbox((0, 0), "Ag", font=font, stroke_width=0)
    line_gap = max(1.0, float(probe[3] - probe[1]) * 1.18)
    saved_lines = region.get("sfx_layout_lines")
    if region.get("sfx_layout_source") == text and isinstance(saved_lines, list):
        text_lines = [str(line) for line in saved_lines]
    else:
        text_lines = (
            automatic_sfx_lines(
                text, max(1, width - margin * 2), max(1, height - margin * 2),
                lambda value: float(scratch.textlength(value, font=font)), line_gap,
                str(style.get("language", "auto")),
            )
            if style.get("sfx_auto_layout", True)
            else text.split("\n")
        )
    wave = float(style.get("sfx_wave", 0)) / 100.0 * height * 0.22
    cycles = max(1, int(style.get("sfx_wave_cycles", 2)))
    expansion = float(style.get("sfx_expansion", 0)) / 100.0
    impact = float(style.get("sfx_impact", 0)) / 100.0
    warp = build_warp(style, width, height)
    line_count = max(1, len(text_lines))
    line_advances = [
        [max(1.0, float(scratch.textlength(char or " ", font=font))) for char in line]
        for line in text_lines
    ]
    line_widths = [max(1.0, sum(advances)) for advances in line_advances]
    block_width = max(line_widths, default=1.0)
    block_height = line_gap * line_count
    layout_scale = min(
        1.0,
        max(1, width - margin * 2) / block_width,
        max(1, height - margin * 2) / block_height,
    )
    block_top = margin + (max(1, height - margin * 2) - block_height * layout_scale) / 2.0
    for line_index, line_text in enumerate(text_lines):
        chars = list(line_text)
        advances = line_advances[line_index]
        total = max(1.0, sum(advances))
        cursor = 0.0
        line_left = margin + (max(1, width - margin * 2) - total * layout_scale) / 2.0
        baseline_y = block_top + (line_index + 0.5) * line_gap * layout_scale
        for char, advance in zip(chars, advances):
            t = (cursor + advance / 2.0) / total
            bbox = scratch.textbbox((0, 0), char or " ", font=font, stroke_width=0)
            glyph_w = max(2, bbox[2] - bbox[0] + pad)
            glyph_h = max(2, bbox[3] - bbox[1] + pad)
            glyph = Image.new("RGBA", (glyph_w, glyph_h), (0, 0, 0, 0))
            glyph_fill = Image.new("RGBA", glyph.size, (0, 0, 0, 0))
            gd = ImageDraw.Draw(glyph); fd = ImageDraw.Draw(glyph_fill)
            origin = (pad // 2 - bbox[0], pad // 2 - bbox[1])
            stroke = max(0, int(style.get("stroke_width", 0)))
            outer = stroke + (max(1, int(style.get("stroke2_width", 6))) if style.get("stroke2_enabled", False) else 0)
            if outer > stroke:
                gd.text(origin, char, font=font, fill=(0, 0, 0, 0), stroke_width=outer, stroke_fill=style.get("stroke2_color", "#111111"))
            gd.text(origin, char, font=font, fill=style.get("text_color", "#111111"), stroke_width=stroke, stroke_fill=style.get("stroke_color", "#FFFFFF"))
            fd.text(origin, char, font=font, fill=(255, 255, 255, 255))

            local_x = line_left + (cursor + advance / 2.0) * layout_scale
            curve_x, curve_y, tangent_angle = bezier_baseline(style, t, width, height)
            local_x += (curve_x - t) * total * layout_scale
            offset_y = (
                curve_y
                + wave * math.sin(t * cycles * math.tau)
            ) * layout_scale
            local_y = baseline_y + offset_y
            u = max(-5.0, min(5.0, local_x / width))
            v = max(-5.0, min(5.0, local_y / height))
            center = warp(u, v)
            epsilon = 0.002
            right = warp(u + epsilon, v)
            down = warp(u, v + epsilon)
            horizontal_extent = max(1.0, math.hypot(right[0] - center[0], right[1] - center[1]) / epsilon)
            vertical_extent = max(1.0, math.hypot(down[0] - center[0], down[1] - center[1]) / epsilon)
            sx = max(0.08, layout_scale * horizontal_extent / width * (1.0 + expansion * (t - 0.5)))
            sy = max(0.08, layout_scale * vertical_extent / height)
            target = (max(1, round(glyph.width * sx)), max(1, round(glyph.height * sy)))
            glyph = glyph.resize(target, Image.Resampling.LANCZOS)
            glyph_fill = glyph_fill.resize(target, Image.Resampling.LANCZOS)
            edge_angle = math.degrees(math.atan2(right[1] - center[1], right[0] - center[0]))
            angle = -edge_angle - tangent_angle - (t - 0.5) * impact * 34.0
            if abs(angle) > 0.1:
                glyph = glyph.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
                glyph_fill = glyph_fill.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            gx = round(pad + center[0] - glyph.width / 2.0)
            gy = round(pad + center[1] - glyph.height / 2.0)
            layer.alpha_composite(glyph, (gx, gy)); fill_layer.alpha_composite(glyph_fill, (gx, gy))
            cursor += advance
    if style.get("gradient_enabled", False):
        gradient = _gradient_image(
            layer.width, layer.height, style.get("gradient_start", "#FFFFFF"),
            style.get("gradient_end", "#00CFE8"), style.get("gradient_angle", 90),
        )
        layer.paste(gradient, (0, 0), fill_layer.getchannel("A"))
    skew = math.tan(math.radians(float(style.get("sfx_skew", 0))))
    if abs(skew) > 0.001:
        layer = layer.transform(
            layer.size, Image.Transform.AFFINE, (1, -skew, skew * layer.height / 2, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
    rotation = int(style.get("rotation", 0))
    if rotation:
        layer = layer.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    opacity = max(0, min(100, int(region.get("opacity", 100))))
    if opacity < 100:
        layer.putalpha(layer.getchannel("A").point(lambda value: value * opacity // 100))
    _paste_with_blend(
        image, layer, (round(x - pad), round(y - pad)), str(style.get("blend_mode", "normal")),
    )


def _draw_region_text(image: Image.Image, region: dict, default_style: dict) -> None:
    text = str(region.get("applied_text", "")).strip()
    if not text:
        return
    style = TypographyManager.normalized({**default_style, **dict(region.get("style", {}))})
    if style.get("text_case") == "upper":
        text = text.upper()
    elif style.get("text_case") == "lower":
        text = text.lower()
    x, y = int(region["x"]), int(region["y"])
    width, height = max(1, int(region["width"])), max(1, int(region["height"]))
    if style.get("sfx_enabled", False):
        _draw_sfx_text(image, region, style, text)
        return
    margin = max(0, int(style.get("text_margin", style.get("margin", 8))))
    available_width, available_height = max(1, width - margin * 2), max(1, height - margin * 2)
    vertical = bool(style.get("vertical_text", False))
    if vertical:
        text = "\n".join(character for character in text.replace("\n", "") if character)
    alignment = style.get("alignment", "center")
    vertical_alignment = style.get("vertical_alignment", "center")
    draw = ImageDraw.Draw(image)
    requested = int(style.get("font_size", min(52, max(12, height // 3))))
    font_weight = int(style.get("font_weight", 400))
    italic = bool(style.get("italic", False))
    stroke_width = max(0, int(style.get("stroke_width", 0)))
    stroke2_width = max(0, int(style.get("stroke2_width", 0))) if style.get("stroke2_enabled", False) else 0
    outer_stroke_width = stroke_width + stroke2_width if stroke2_width else 0
    render_stroke = max(stroke_width, outer_stroke_width)
    spacing = max(-10, int(style.get("line_spacing", 3)))
    manual_scale_x = max(0.5, min(2.0, int(style.get("scale_x", 100)) / 100.0))
    manual_scale_y = max(0.5, min(2.0, int(style.get("scale_y", 100)) / 100.0))
    primary_family = str(style.get("font_family", "Segoe UI"))
    resolved_family = primary_family
    wrapped = text
    selected_font = _font(
        requested, resolved_family, style.get("font_file"), font_weight, italic,
    )
    balloon_layout = None
    rectangle_layout = None
    if style.get("balloon_fit", False) and not vertical:
        def font_for_size(size: int):
            return _font(size, resolved_family, style.get("font_file"), font_weight, italic)

        balloon_layout = valid_balloon_snapshot(region, text, style)
        if balloon_layout is not None:
            snapshot_font = font_for_size(int(balloon_layout["font_size"]))
            if any(
                draw.textlength(line, font=snapshot_font) * manual_scale_x
                * float(balloon_layout.get("scale_x", 1.0)) > right - left - 1
                for line, (left, right) in zip(balloon_layout["lines"], balloon_layout["intervals"])
            ):
                balloon_layout = None
        if balloon_layout is None:
            padding = effective_balloon_padding(
                width, height, int(style.get("balloon_padding", 10)), render_stroke,
            )
            search_x, search_y, search_width, search_height = balloon_search_rect(
                image.width, image.height, (x, y, width, height),
            )
            search_rgb = np.asarray(image.crop((
                search_x, search_y, search_x + search_width, search_y + search_height,
            )).convert("RGB"))
            local_rect, balloon_mask = text_layout_geometry(
                search_rgb, (x - search_x, y - search_y, width, height),
                padding, bool(region.get("typeset_box_manual", False)),
            )
            layout_rect = (
                search_x + local_rect[0], search_y + local_rect[1],
                local_rect[2], local_rect[3],
            )

            def line_height_for_size(size: int) -> float:
                candidate_font = font_for_size(size)
                bounds = draw.textbbox((0, 0), "Ag", font=candidate_font, stroke_width=render_stroke)
                return max(1, bounds[3] - bounds[1] + spacing)

            balloon_layout = fit_balanced_text(
                text, balloon_mask, requested, 6,
                bool(style.get("auto_fit", False) or style.get("fit_once", False)),
                line_height_for_size,
                lambda size, value: draw.textlength(value, font=font_for_size(size)) * manual_scale_x,
                language=str(style.get("language", "auto")),
                hyphenate=bool(style.get("hyphenation", True)),
                orphan_control=bool(style.get("orphan_control", True)),
                hanging_punctuation=bool(style.get("hanging_punctuation", True)),
                max_horizontal_compression=int(style.get("max_horizontal_compression", 12)),
                auto_scale=bool(style.get("auto_scale", True)),
                shape_type=str(style.get("balloon_shape", "auto")),
                padding=padding,
            )
            if not balloon_layout["overflow"]:
                balloon_layout["layout_rect"] = layout_rect
                balloon_layout["center_y"] = (
                    float(balloon_layout["center_y"]) + layout_rect[1] - y
                )
        if not balloon_layout["overflow"]:
            wrapped = str(balloon_layout["text"])
            selected_font = font_for_size(int(balloon_layout["font_size"]))
        else:
            region["text_overflow"] = True
    elif not vertical:
        rectangle_layout = valid_snapshot(region, text, style)
        if rectangle_layout is None:
            signature = layout_signature(text, width, height, style)

            def rectangle_font(size: int):
                return _font(size, resolved_family, style.get("font_file"), font_weight, italic)

            rectangle_layout = fit_rectangular_text(
                text, available_width, available_height / manual_scale_y,
                requested, 6, bool(style.get("auto_fit", False)),
                lambda size: max(
                    1.0,
                    float(draw.textbbox((0, 0), "Ag", font=rectangle_font(size), stroke_width=render_stroke)[3]
                          - draw.textbbox((0, 0), "Ag", font=rectangle_font(size), stroke_width=render_stroke)[1]
                          + spacing),
                ),
                lambda size, value: float(draw.textlength(value, font=rectangle_font(size))) * manual_scale_x,
                language=str(style.get("language", "auto")),
                hyphenate=bool(style.get("hyphenation", True)),
                orphan_control=bool(style.get("orphan_control", True)),
                hanging_punctuation=bool(style.get("hanging_punctuation", True)),
                signature=signature,
            )
            region["layout_snapshot"] = dict(rectangle_layout)
        wrapped = str(rectangle_layout["text"])
        selected_font = _font(
            int(rectangle_layout["font_size"]), resolved_family, style.get("font_file"),
            font_weight, italic,
        )
        region["text_overflow"] = bool(rectangle_layout.get("overflow", False))
    sizes = range(requested, 5, -1) if style.get("auto_fit", False) else (requested,)
    for size in (() if ((balloon_layout and not balloon_layout["overflow"]) or rectangle_layout) else sizes):
        candidate_font = _font(
            size, resolved_family, style.get("font_file"), font_weight, italic,
        )
        candidate = text if vertical else _wrap_text(draw, text, candidate_font, available_width)
        bbox = draw.multiline_textbbox((0, 0), candidate, font=candidate_font, spacing=spacing, stroke_width=render_stroke, align=alignment)
        if bbox[2] - bbox[0] <= available_width and bbox[3] - bbox[1] <= available_height:
            wrapped, selected_font = candidate, candidate_font
            break
    balloon_specs: list[tuple[str, tuple[float, float]]] = []
    balloon_success = bool(balloon_layout and not balloon_layout["overflow"])
    if balloon_success:
        layout_rect = balloon_layout.get("layout_rect", (x, y, width, height))
        text_width = int(layout_rect[2])
        text_height = int(math.ceil(float(balloon_layout["line_height"]) * len(balloon_layout["lines"])))
        bbox = (0, 0, text_width, text_height)
    else:
        bbox = draw.multiline_textbbox(
            (0, 0), wrapped, font=selected_font, spacing=spacing,
            stroke_width=render_stroke, align=alignment,
        )
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_layer = Image.new(
        "RGBA",
        (
            max(1, int(math.ceil(text_width + render_stroke * 4 + 4))),
            max(1, int(math.ceil(text_height + render_stroke * 4 + 4))),
        ),
        (0, 0, 0, 0),
    )
    layer_draw = ImageDraw.Draw(text_layer)
    origin = (render_stroke * 2 + 2 - bbox[0], render_stroke * 2 + 2 - bbox[1])
    if balloon_success:
        pad = render_stroke * 2 + 2
        for line_index, (line, interval) in enumerate(zip(balloon_layout["lines"], balloon_layout["intervals"])):
            line_bbox = layer_draw.textbbox((0, 0), line or " ", font=selected_font, stroke_width=render_stroke)
            line_width = float(layer_draw.textlength(line or " ", font=selected_font))
            left, right = interval
            if alignment == "left":
                line_x = float(left)
            elif alignment == "right":
                line_x = float(right) - line_width
            else:
                line_x = (float(left) + float(right) - line_width) / 2.0
            line_y = line_index * float(balloon_layout["line_height"])
            balloon_specs.append((line, (pad + line_x - line_bbox[0], pad + line_y - line_bbox[1])))
    if outer_stroke_width:
        if balloon_success:
            for line, position in balloon_specs:
                layer_draw.text(
                    position, line, font=selected_font, fill=(0, 0, 0, 0),
                    stroke_width=outer_stroke_width, stroke_fill=style.get("stroke2_color", "#111111"),
                )
        else:
            layer_draw.multiline_text(
                origin, wrapped, font=selected_font, anchor=None, align=alignment, spacing=spacing,
                fill=(0, 0, 0, 0), stroke_width=outer_stroke_width,
                stroke_fill=style.get("stroke2_color", "#111111"),
            )
    if stroke_width:
        if balloon_success:
            for line, position in balloon_specs:
                layer_draw.text(
                    position, line, font=selected_font, fill=(0, 0, 0, 0),
                    stroke_width=stroke_width, stroke_fill=style.get("stroke_color", "#FFFFFF"),
                )
        else:
            layer_draw.multiline_text(
                origin, wrapped, font=selected_font, anchor=None, align=alignment, spacing=spacing,
                fill=(0, 0, 0, 0), stroke_width=stroke_width,
                stroke_fill=style.get("stroke_color", "#FFFFFF"),
            )
    if balloon_success:
        for line, position in balloon_specs:
            layer_draw.text(
                position, line, font=selected_font, fill=style.get("text_color", "#111111"),
                stroke_width=0,
            )
    else:
        layer_draw.multiline_text(
            origin,
            wrapped, font=selected_font, anchor=None, align=alignment, spacing=spacing,
            fill=style.get("text_color", "#111111"), stroke_width=0,
        )
    if style.get("underline") or style.get("strikeout"):
        lines = wrapped.splitlines() or [wrapped]
        sample_bbox = layer_draw.textbbox((0, 0), "Ag", font=selected_font, stroke_width=stroke_width)
        line_height = max(1, sample_bbox[3] - sample_bbox[1])
        block_width = max(1, text_width)
        rendered_size = int(getattr(selected_font, "size", requested))
        decoration_width = max(1, rendered_size // 14)
        fill = style.get("text_color", "#111111")
        for line_index, line in enumerate(lines):
            line_bbox = layer_draw.textbbox((0, 0), line or " ", font=selected_font, stroke_width=stroke_width)
            line_width = max(1, line_bbox[2] - line_bbox[0])
            if balloon_success and line_index < len(balloon_specs):
                left = int(round(balloon_specs[line_index][1][0] + line_bbox[0]))
                top = int(round(balloon_specs[line_index][1][1] + line_bbox[1]))
            elif alignment == "right":
                offset_x = block_width - line_width
            elif alignment == "center":
                offset_x = (block_width - line_width) / 2
            else:
                offset_x = 0
            if not balloon_success:
                left = int(round(origin[0] + offset_x))
                top = int(round(origin[1] + line_index * (line_height + spacing)))
            right = left + line_width
            if style.get("underline"):
                underline_y = top + line_height + max(1, rendered_size // 14)
                layer_draw.line((left, underline_y, right, underline_y), fill=fill, width=decoration_width)
            if style.get("strikeout"):
                strike_y = top + int(line_height * 0.55)
                layer_draw.line((left, strike_y, right, strike_y), fill=fill, width=decoration_width)
    if style.get("gradient_enabled", False):
        fill_mask = Image.new("L", text_layer.size, 0)
        mask_draw = ImageDraw.Draw(fill_mask)
        if balloon_success:
            for line, position in balloon_specs:
                mask_draw.text(position, line, font=selected_font, fill=255, stroke_width=0)
        else:
            mask_draw.multiline_text(
                origin, wrapped, font=selected_font, anchor=None, align=alignment,
                spacing=spacing, fill=255, stroke_width=0,
            )
        gradient = _gradient_image(
            text_layer.width, text_layer.height,
            style.get("gradient_start", "#FFFFFF"),
            style.get("gradient_end", "#00CFE8"),
            style.get("gradient_angle", 90),
        )
        text_layer.paste(gradient, (0, 0), fill_mask)

    effective_scale_x = manual_scale_x * (
        float(balloon_layout.get("scale_x", 1.0)) if balloon_success else 1.0
    )
    effective_scale_y = manual_scale_y * (
        float(balloon_layout.get("scale_y", 1.0)) if balloon_success else 1.0
    )
    if abs(effective_scale_x - 1.0) > 0.005 or abs(effective_scale_y - 1.0) > 0.005:
        text_layer = text_layer.resize(
            (
                max(1, int(round(text_layer.width * effective_scale_x))),
                max(1, int(round(text_layer.height * effective_scale_y))),
            ),
            Image.Resampling.LANCZOS,
        )
        text_width, text_height = text_layer.size

    glow_active = style.get("glow_enabled", False) and int(style.get("glow_opacity", 65)) > 0
    shadow_active = style.get("shadow_enabled", False) and int(style.get("shadow_opacity", 65)) > 0
    glow_radius = max(1, int(style.get("glow_radius", 10))) if glow_active else 0
    shadow_radius = max(0, int(style.get("shadow_blur", 8))) if shadow_active else 0
    shadow_x = int(style.get("shadow_offset_x", 4)) if shadow_active else 0
    shadow_y = int(style.get("shadow_offset_y", 6)) if shadow_active else 0
    effect_pad = max(
        0,
        int(math.ceil(glow_radius * 2.2)) if glow_active else 0,
        int(math.ceil(shadow_radius * 2.2 + abs(shadow_x))) if shadow_active else 0,
        int(math.ceil(shadow_radius * 2.2 + abs(shadow_y))) if shadow_active else 0,
    )
    if effect_pad:
        expanded = Image.new(
            "RGBA", (text_layer.width + effect_pad * 2, text_layer.height + effect_pad * 2),
            (0, 0, 0, 0),
        )
        expanded.alpha_composite(text_layer, (effect_pad, effect_pad))
        stack = Image.new("RGBA", expanded.size, (0, 0, 0, 0))
        if shadow_active:
            shadow_alpha = expanded.getchannel("A").filter(ImageFilter.GaussianBlur(radius=shadow_radius))
            shadow_alpha = shadow_alpha.point(
                lambda value: value * max(0, min(100, int(style.get("shadow_opacity", 65)))) // 100
            )
            shifted = Image.new("L", expanded.size, 0)
            shifted.paste(shadow_alpha, (shadow_x, shadow_y))
            shadow = Image.new("RGBA", expanded.size, ImageColor.getrgb(style.get("shadow_color", "#000000")) + (0,))
            shadow.putalpha(shifted); stack.alpha_composite(shadow)
        if glow_active:
            glow_alpha = expanded.getchannel("A").filter(ImageFilter.GaussianBlur(radius=glow_radius))
            glow_alpha = glow_alpha.point(
                lambda value: value * max(0, min(100, int(style.get("glow_opacity", 65)))) // 100
            )
            glow = Image.new("RGBA", expanded.size, ImageColor.getrgb(style.get("glow_color", "#00CFE8")) + (0,))
            glow.putalpha(glow_alpha); stack.alpha_composite(glow)
        stack.alpha_composite(expanded)
        text_layer = stack
    rotation = int(style.get("rotation", 0))
    if rotation:
        text_layer = text_layer.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    opacity = max(0, min(100, int(region.get("opacity", 100))))
    if opacity < 100:
        alpha = text_layer.getchannel("A").point(lambda value: value * opacity // 100)
        text_layer.putalpha(alpha)
    if balloon_success:
        px = float(layout_rect[0]) + (float(layout_rect[2]) - text_layer.width) / 2
    elif alignment == "left":
        px = x + margin - effect_pad
    elif alignment == "right":
        px = x + width - margin - text_layer.width + effect_pad
    else:
        px = x + (width - text_layer.width) / 2
    if balloon_success:
        py = y + float(balloon_layout["center_y"]) - text_layer.height / 2
    elif vertical_alignment == "top":
        py = y + margin - effect_pad
    elif vertical_alignment == "bottom":
        py = y + height - margin - text_layer.height + effect_pad
    else:
        py = y + (height - text_layer.height) / 2
    if style.get("optical_center", False):
        ink = text_layer.getchannel("A").getbbox()
        if ink:
            limit = max(1.0, float(getattr(selected_font, "size", requested)) * 0.16)
            ink_center_x = (ink[0] + ink[2]) / 2.0
            ink_center_y = (ink[1] + ink[3]) / 2.0
            if alignment == "center":
                px += max(-limit, min(limit, text_layer.width / 2.0 - ink_center_x))
            if vertical_alignment == "center":
                py += max(-limit, min(limit, text_layer.height / 2.0 - ink_center_y))
    _paste_with_blend(
        image, text_layer, (int(round(px)), int(round(py))), str(style.get("blend_mode", "normal")),
    )


def compose_page(page: Page, regions: list[dict], clean_result: dict | None, styles: dict | None) -> Image.Image:
    if page.path is None:
        raise RuntimeError(f"La página {page.name} no tiene archivo de origen.")
    image = load_source_image(page.path, (styles or {}).get("source_layers", {}))
    retouch_states = normalized_layer_states((styles or {}).get("retouch_layers", {}))
    clean_state = (styles or {}).get("layers", {}).get("clean", {})
    clean_visible = bool(clean_state.get("visible", True))
    clean_opacity = max(0, min(100, int(clean_state.get("opacity", 100)))) / 100.0
    for patch in (clean_result or {}).get("patches", []):
        layer_state = retouch_states.get(patch_layer(patch), {})
        if not clean_visible or not bool(layer_state.get("visible", True)):
            continue
        pixels = patch["pixels"]
        patch_image = Image.fromarray(np.asarray(pixels, dtype=np.uint8), "RGB")
        alpha = patch.get("mask")
        if alpha is not None:
            effective = np.asarray(alpha, dtype=np.float32) * clean_opacity * (
                max(0, min(100, int(layer_state.get("opacity", 100)))) / 100.0
            )
            image.paste(
                patch_image, (int(patch["x"]), int(patch["y"])),
                Image.fromarray(np.clip(effective, 0, 255).astype(np.uint8), "L"),
            )
        else:
            # Older brush patches did not persist their stroke alpha. Rebuild
            # it from the difference with the composition underneath instead
            # of pasting their rectangular crop over nearby artwork.
            x, y = int(patch["x"]), int(patch["y"])
            current = np.asarray(image.crop((x, y, x + patch_image.width, y + patch_image.height)).convert("RGB"))
            patch_array = np.asarray(patch_image)
            if current.shape == patch_array.shape:
                recovered = np.where(np.any(current != patch_array, axis=2), 255, 0).astype(np.uint8)
                if np.any(recovered):
                    recovered = np.clip(
                        recovered.astype(np.float32) * clean_opacity
                        * (max(0, min(100, int(layer_state.get("opacity", 100)))) / 100.0),
                        0, 255,
                    ).astype(np.uint8)
                    image.paste(patch_image, (x, y), Image.fromarray(recovered, "L"))
            else:
                fallback_opacity = int(round(
                    255 * clean_opacity
                    * (max(0, min(100, int(layer_state.get("opacity", 100)))) / 100.0)
                ))
                image.paste(
                    patch_image, (x, y),
                    Image.new("L", patch_image.size, max(0, min(255, fallback_opacity))),
                )
    default_style = dict((styles or {}).get("typography", (styles or {}).get("translation", {})))
    for region in regions:
        if not bool(region.get("visible", True)):
            continue
        _draw_region_text(image, region, default_style)
    # Watermarks are composed last, above cleaned artwork and typesetting, at
    # the original page resolution. They never mutate the source or patches.
    compose_watermark(image, (styles or {}).get("watermark"))
    return image


def export_layered_psd(
    destination: Path,
    page: Page,
    regions: list[dict],
    clean_result: dict | None,
    styles: dict | None,
) -> None:
    """Export page with clean base image and separate transparent layers for each text balloon."""
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer

    # 1. Base clean artwork without text boxes
    base_image = compose_page(page, [], clean_result, styles)
    psd = PSDImage.new(mode="RGB", size=base_image.size)
    bg_layer = PixelLayer.frompil(base_image.convert("RGB"), psd, name="Fondo Limpio")
    bg_layer.offset = (0, 0)
    psd.append(bg_layer)

    # 2. Add each dialogue text box as an individual named PSD layer
    default_style = dict((styles or {}).get("typography", (styles or {}).get("translation", {})))
    for idx, region in enumerate(regions):
        if not bool(region.get("visible", True)):
            continue
        text = str(region.get("applied_text") or region.get("translation") or region.get("text", "")).strip()
        if not text:
            continue
        w = max(10, int(region.get("width", 100)))
        h = max(10, int(region.get("height", 60)))
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))

        # Render dialogue text on transparent RGBA box
        text_box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        local_region = {**region, "x": 0, "y": 0}
        _draw_region_text(text_box, local_region, default_style)

        clean_snippet = text.replace("\n", " ")[:28]
        layer_name = f"[{idx + 1:02d}] {clean_snippet}"
        text_layer = PixelLayer.frompil(text_box, psd, name=layer_name)
        text_layer.offset = (x, y)
        psd.append(text_layer)

    destination.parent.mkdir(parents=True, exist_ok=True)
    psd.save(str(destination))


def export_page(destination: Path, page: Page, regions: list[dict], clean_result: dict | None, styles: dict | None, format_name: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    format_name = format_name.upper()
    if format_name == "PSD":
        export_layered_psd(destination, page, regions, clean_result, styles)
        return
    image = compose_page(page, regions, clean_result, styles)
    if format_name in {"JPG", "JPEG"}:
        image.save(destination, format="JPEG", quality=95, subsampling=0, optimize=True)
    elif format_name == "WEBP":
        image.save(destination, format="WEBP", quality=96, method=4)
    else:
        image.save(destination, format="PNG", compress_level=6)
