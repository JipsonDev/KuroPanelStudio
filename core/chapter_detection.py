"""Run local detection serially without decoding the whole chapter at once."""
from core.project_io import page_key


def detect_chapter(detector, pages, source_states, progress, cancelled):
    results = {}
    count = len(pages)
    for index, page in enumerate(pages):
        if cancelled():
            return {}
        key = page_key(page)
        stage = f"Página {index + 1} de {count} · YOLO"
        progress(int(index * 100 / count), stage, key)
        results[key] = detector.detect(
            page.path,
            lambda value, base=index, label=stage, current=key: progress(
                min(99, int((base + value / 100) * 100 / count)), label, current),
            cancelled, source_states.get(key, {}),
        )
        if cancelled():
            return {}
    progress(100)
    return results


REPEATED_CAPTION_KEY = "repeated_caption"


def mark_repeated_captions(pages: dict[str, list[dict]], minimum: int = 3, tolerance: float = .05) -> int:
    """Flag one-line boxes of the same size repeated through the chapter.

    A scan site's caption («lee primero en…») is detected at every repeat
    of its watermark: each one cost an OCR request and went to translation.
    Flagged boxes stay (they are still cleaned) but are never read. Only
    wide single lines found by the detector with modest confidence qualify,
    so equal-sized dialogue is left alone.
    """
    candidates = [
        region for regions in pages.values() for region in regions
        if not region.get(REPEATED_CAPTION_KEY) and not str(region.get("text", "")).strip()
        and not region.get("manual") and not str(region.get("id", "")).startswith("manual-")
        and int(region.get("height", 0)) > 0
        and int(region.get("width", 0)) >= 4 * int(region.get("height", 0))
        and float(region.get("confidence", 1.0)) < .5
    ]
    marked = 0
    remaining = sorted(candidates, key=lambda region: (int(region["width"]), int(region["height"])))
    while remaining:
        first = remaining[0]
        width, height = int(first["width"]), int(first["height"])
        group = [region for region in remaining
                 if abs(int(region["width"]) - width) <= width * tolerance
                 and abs(int(region["height"]) - height) <= max(2, height * tolerance * 2)]
        if len(group) >= minimum:
            for region in group:
                region[REPEATED_CAPTION_KEY] = True
                region["ocr_checked"] = True
            marked += len(group)
        chosen = {id(region) for region in group}
        remaining = [region for region in remaining if id(region) not in chosen]
    return marked
