"""Clipboard formatting for OCR and manual chapter text queues."""
from __future__ import annotations

import re
from collections.abc import Iterable


_NUMBERED_LINE = re.compile(r"^\s*(?:#\s*)?(\d{1,5})\s*[.\-:)]\s*(.*)$")
_HASH_LINE = re.compile(r"^\s*#\s*(\d{1,5})\s+(.*)$")
_BRACKET_LINE = re.compile(r"^\s*\[\s*(\d{1,5})\s*\]\s*(.*)$")
_STANDALONE_NUMBER = re.compile(r"^\s*(?:#\s*|\[\s*)?(\d{1,5})\s*\]?\s*$")
_PAGE_HEADER = re.compile(r"^\s*={2,}\s*(.*?)\s*={2,}\s*$")
_ANY_NUMBER_PREFIX = re.compile(
    r"^\s*(?:(?:#\s*|\[\s*)\d{1,5}\s*\]?|\d{1,5})"
    r"(?:\s*[.\-:)|]\s*|\s+)(?=\S)"
)


def strip_numeric_prefix(value: object) -> str:
    """Remove a box number used for transfer, never meaningful dialogue."""
    return _ANY_NUMBER_PREFIX.sub("", str(value or ""), count=1).strip()


def compact_entry(value: object) -> str:
    """Make one clipboard row while preserving paragraph boundaries compactly."""
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n+", str(value or "").strip()):
        words = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if words:
            paragraphs.append(" ".join(words))
    return " / ".join(paragraphs)


def format_numbered_entries(entries: Iterable[tuple[int, object]]) -> str:
    rows = []
    for number, value in entries:
        text = compact_entry(value)
        if text:
            rows.append(f"{max(1, int(number))}. {text}")
    return "\n".join(rows)


def parse_numbered_entries(value: str) -> list[str]:
    """Read numbered boxes or blank-separated paragraphs in paste order."""
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    has_numbers = any(
        _NUMBERED_LINE.match(line) or _HASH_LINE.match(line) or _BRACKET_LINE.match(line)
        or _STANDALONE_NUMBER.match(line)
        for line in lines
    )
    if not has_numbers:
        if any(not line.strip() for line in lines[1:-1]):
            blocks = re.split(r"\n\s*\n+", "\n".join(lines).strip())
            return [block.strip() for block in blocks if block.strip()]
        return [strip_numeric_prefix(line) for line in lines if strip_numeric_prefix(line)]

    entries: list[str] = []
    current: list[str] = []
    for line in lines:
        numbered = (
            _NUMBERED_LINE.match(line) or _HASH_LINE.match(line)
            or _BRACKET_LINE.match(line) or _STANDALONE_NUMBER.match(line)
        )
        if numbered:
            if current:
                entries.append("\n".join(current).strip())
            current = [strip_numeric_prefix(line)] if not _STANDALONE_NUMBER.match(line) else []
        elif line.strip() and current:
            current.append(line.strip())
        elif line.strip() and has_numbers:
            current = [line.strip()]
    if current:
        entries.append("\n".join(current).strip())
    return [entry for entry in entries if entry]


def format_chapter_sections(sections: Iterable[tuple[str, str]]) -> str:
    blocks = []
    for name, content in sections:
        body = str(content or "").strip()
        if body:
            blocks.append(f"=== {name} ===\n{body}")
    return "\n\n".join(blocks)


def parse_chapter_sections(value: str) -> dict[str, list[str]]:
    """Parse text produced by :func:`format_chapter_sections`."""
    sections: dict[str, list[str]] = {}
    name = ""
    body: list[str] = []
    for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        header = _PAGE_HEADER.match(line)
        if header:
            if name:
                sections[name] = parse_numbered_entries("\n".join(body))
            name = header.group(1).strip()
            body = []
        elif name:
            body.append(line)
    if name:
        sections[name] = parse_numbered_entries("\n".join(body))
    return {key: entries for key, entries in sections.items() if entries}
