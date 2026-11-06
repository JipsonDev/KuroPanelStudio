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
