"""Derived production state for each chapter page."""
from __future__ import annotations


def page_workflow_status(regions: list[dict], clean_result: dict | None = None) -> dict[str, bool]:
    has_regions = bool(regions)
    ocr_done = has_regions and all(str(region.get("text", "")).strip() for region in regions)
    translated = has_regions and all(
        bool(region.get("translation_completed", False))
        or (
            bool(str(region.get("translation", "")).strip())
            and str(region.get("translation", "")).strip() != str(region.get("text", "")).strip()
        )
        for region in regions
    )
    typeset = has_regions and all(
        bool(region.get("typeset_completed", False))
        or (bool(str(region.get("applied_text", "")).strip()) and bool(region.get("style")))
        for region in regions
    )
    return {
        "detected": has_regions,
        "ocr": ocr_done,
        "cleaned": bool((clean_result or {}).get("patches")),
        "translated": translated,
        "typeset": typeset,
    }
