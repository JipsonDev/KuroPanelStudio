"""Derived production state for each chapter page."""
from __future__ import annotations


def page_workflow_status(regions: list[dict], clean_result: dict | None = None) -> dict[str, bool]:
    # The half of a cut balloon whose text lives in the other page counts as done.
    # A repeated watermark caption is cleaned but has nothing to read or translate.
    everything = bool(regions)
    regions = [region for region in regions
               if not region.get("seam_child") and not region.get("repeated_caption")]
    ocr_done = everything and all(str(region.get("text", "")).strip() for region in regions)
    translated = everything and all(
        bool(region.get("translation_completed", False))
        or (
            bool(str(region.get("translation", "")).strip())
            and str(region.get("translation", "")).strip() != str(region.get("text", "")).strip()
        )
        for region in regions
    )
    typeset = everything and all(
        bool(region.get("typeset_completed", False))
        or (bool(str(region.get("applied_text", "")).strip()) and bool(region.get("style")))
        for region in regions
    )
    return {
        "detected": everything,
        "ocr": ocr_done,
        "cleaned": bool((clean_result or {}).get("patches")),
        "translated": translated,
        "typeset": typeset,
    }
