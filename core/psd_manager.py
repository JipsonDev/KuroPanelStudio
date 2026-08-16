"""Non-destructive PSD inspection and compositing."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def is_psd(path: Path | None) -> bool:
    return bool(path and path.suffix.lower() in {".psd", ".psb"})


def _psd_image():
    try:
        from psd_tools import PSDImage
        return PSDImage
    except ImportError as error:
        raise RuntimeError(
            "Para abrir PSD instala las dependencias con: pip install -r requirements.txt"
        ) from error


def inspect_psd(path: Path) -> tuple[int, int, list[dict]]:
    psd = _psd_image().open(path)
    layers: list[dict] = []

    def visit(group, prefix: tuple[int, ...] = (), depth: int = 0) -> None:
        for index, layer in enumerate(group):
            identity = "/".join(str(value) for value in (*prefix, index))
            bbox = tuple(int(value) for value in getattr(layer, "bbox", (0, 0, 0, 0)))
            kind = str(getattr(layer, "kind", "layer"))
            blend = getattr(layer, "blend_mode", "normal")
            blend_value = getattr(blend, "value", blend)
            if isinstance(blend_value, bytes):
                blend_value = blend_value.decode("ascii", "replace").strip()
            layers.append({
                "id": identity,
                "name": str(getattr(layer, "name", "") or f"Capa {len(layers) + 1}"),
                "kind": kind,
                "group": bool(getattr(layer, "is_group", lambda: False)()),
                "depth": depth,
                "visible": bool(getattr(layer, "visible", True)),
                "opacity": round(int(getattr(layer, "opacity", 255)) * 100 / 255),
                "bbox": bbox,
                "blend_mode": str(blend_value or "normal"),
            })
            if bool(getattr(layer, "is_group", lambda: False)()):
                visit(layer, (*prefix, index), depth + 1)

    visit(psd)
    return int(psd.width), int(psd.height), layers


def _layer_map(psd) -> dict[str, object]:
    result: dict[str, object] = {}

    def visit(group, prefix: tuple[int, ...] = ()) -> None:
        for index, layer in enumerate(group):
            identity = "/".join(str(value) for value in (*prefix, index))
            result[identity] = layer
            if bool(getattr(layer, "is_group", lambda: False)()):
                visit(layer, (*prefix, index))

    visit(psd)
    return result


def render_psd(path: Path, states: dict[str, dict] | None = None) -> Image.Image:
    """Composite a PSD in memory; the source file is never modified."""
    psd = _psd_image().open(path)
    layers = _layer_map(psd)
    for identity, state in (states or {}).items():
        layer = layers.get(str(identity))
        if layer is None:
            continue
        if "visible" in state:
            layer.visible = bool(state["visible"])
        if "opacity" in state:
            layer.opacity = round(max(0, min(100, int(state["opacity"]))) * 255 / 100)
    # The embedded preview makes initial load and thumbnails much faster. It
    # is bypassed only when layer overrides must be reflected.
    image = psd.composite(ignore_preview=bool(states))
    if image.mode == "RGBA":
        backdrop = Image.new("RGBA", image.size, (255, 255, 255, 255))
        backdrop.alpha_composite(image)
        return backdrop.convert("RGB")
    return image.convert("RGB")


def load_source_image(path: Path, states: dict[str, dict] | None = None) -> Image.Image:
    if is_psd(path):
        return render_psd(path, states)
    with Image.open(path) as opened:
        return opened.convert("RGB").copy()


def state_signature(states: dict[str, dict] | None) -> tuple:
    return tuple(sorted(
        (str(identity), bool(state.get("visible", True)), int(state.get("opacity", 100)))
        for identity, state in (states or {}).items()
    ))
