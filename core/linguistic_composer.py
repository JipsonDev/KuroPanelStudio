"""Language-aware line breaking for translated comics."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata
from typing import Callable


CJK_CLOSING = set(
    "、。，．！？：；％‰℃)]}〉》」』】〕〗〙〛…—～ー"
    "ぁぃぅぇぉゃゅょっゎァィゥェォャュョッヮヵヶ"
    "々〻ゝゞヽヾ〆〳〴〵・:;!?%”’»"
)
CJK_OPENING = set("([{〈《「『【〔〖〘〚（［｛“‘«¿¡")
HANGING_PUNCTUATION = set(",.;:!?…，。！？、；：)]}》」』】”’»")
SPANISH_VOWELS = "aeiouáéíóúüAEIOUÁÉÍÓÚÜ"


def detect_language(text: str, configured: str = "auto") -> str:
    selected = str(configured or "auto").lower()
    if selected in {"es", "zh", "ja", "ko"}:
        return selected
    value = str(text)
    if re.search(r"[\u3040-\u30ff]", value):
        return "ja"
    if re.search(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]", value):
        return "ko"
    if re.search(r"[\u3400-\u9fff\uf900-\ufaff]", value):
        return "zh"
    return "es"


@lru_cache(maxsize=2048)
def spanish_hyphen_points(word: str) -> tuple[int, ...]:
    """Return conservative syllable boundaries, preferring Pyphen if present."""
    clean = word.strip("¿¡.,;:!?…()[]{}\"'“”‘’")
    offset = word.find(clean)
    if len(clean) < 7:
        return ()
    try:
        import pyphen
        inserted = pyphen.Pyphen(lang="es_ES").inserted(clean)
        points, position = [], 0
        for piece in inserted.split("-")[:-1]:
            position += len(piece)
            points.append(offset + position)
        return tuple(point for point in points if 2 <= point <= len(word) - 3)
    except ImportError:
        pass
    points = []
    for index in range(2, len(clean) - 2):
        left, current = clean[index - 1], clean[index]
        if left in SPANISH_VOWELS and current not in SPANISH_VOWELS:
            # Keep common consonant clusters together.
            cluster = clean[index:index + 2].lower()
            if cluster not in {"ch", "ll", "rr", "br", "bl", "cr", "cl", "dr", "fr", "fl", "gr", "gl", "pr", "pl", "tr"}:
                points.append(offset + index)
    return tuple(points)


def _emergency_hyphen_points(word: str) -> range:
    """Fallback for long unrecognized words that cannot fit any legal split."""
    clean = str(word)
    if len(clean) < 9 or not clean.isalpha():
        return range(0)
    return range(3, len(clean) - 2)


def _visible_width(text: str, measure: Callable[[str], float], hanging: bool) -> float:
    width = float(measure(text))
    if hanging and text and text[-1] in HANGING_PUNCTUATION:
        width -= float(measure(text[-1])) * 0.42
    return max(0.0, width)


def _break_allowed_cjk(text: str, end: int) -> bool:
    if not 0 < end < len(text):
        return True
    previous, following = text[end - 1], text[end]
    if previous in CJK_OPENING or following in CJK_CLOSING:
        return False
    # UAX #14: a combining mark stays attached to its base character.
    if unicodedata.category(following).startswith("M"):
        return False
    return True


def _cjk_wrap(
    text: str, widths: list[float], measure: Callable[[str], float],
    orphan_control: bool, hanging: bool,
) -> list[str] | None:
    chars = [char for char in str(text) if not char.isspace()]
    count = len(widths)

    @lru_cache(maxsize=None)
    def solve(position: int, line: int):
        if position == len(chars):
            return (0.0, ()) if line == count else None
        if line >= count or len(chars) - position < count - line:
            return None
        best = None
        for end in range(position + 1, len(chars) + 1):
            candidate = "".join(chars[position:end])
            used = _visible_width(candidate, measure, hanging)
            if used > widths[line]:
                break
            if not _break_allowed_cjk("".join(chars), end):
                continue
            tail = solve(end, line + 1)
            if tail is None:
                continue
            empty = (widths[line] - used) / max(1.0, widths[line])
            cost = tail[0] + empty * empty
            if orphan_control and line == count - 1 and len(candidate) <= 2:
                cost += 1.8
            result = (cost, (candidate, *tail[1]))
            if best is None or result[0] < best[0]:
                best = result
        return best

    result = solve(0, 0)
    return list(result[1]) if result else None


def _word_wrap(
    text: str, widths: list[float], measure: Callable[[str], float], language: str,
    hyphenate: bool, orphan_control: bool, hanging: bool,
) -> list[str] | None:
    initial = tuple(" ".join(str(text).replace("\n", " ").split()).split())
    count = len(widths)

    @lru_cache(maxsize=4096)
    def solve(words: tuple[str, ...], line: int):
        if not words:
            return (0.0, ()) if line == count else None
        if line >= count:
            return None
        best = None
        for end in range(1, len(words) + 1):
            candidate = " ".join(words[:end])
            used = _visible_width(candidate, measure, hanging)
            if used > widths[line]:
                break
            if language == "ko" and end < len(words):
                # Korean uses spaces like a word language, while its paired
                # punctuation follows the same non-starter rules as CJK.
                if candidate[-1] in CJK_OPENING or words[end][:1] in CJK_CLOSING:
                    continue
            tail = solve(words[end:], line + 1)
            if tail is not None:
                empty = (widths[line] - used) / max(1.0, widths[line])
                cost = tail[0] + empty * empty
                if orphan_control and line == count - 1 and len(words[:end]) == 1 and len(candidate) < 8:
                    cost += 2.2
                result = (cost, (candidate, *tail[1]))
                if best is None or result[0] < best[0]:
                    best = result
        if best is None and hyphenate and language == "es":
            prefix_words = []
            for word_index, word in enumerate(words):
                base = " ".join(prefix_words)
                for point in reversed(spanish_hyphen_points(word)):
                    prefix = word[:point] + "-"
                    candidate = f"{base} {prefix}".strip()
                    used = _visible_width(candidate, measure, hanging)
                    if used > widths[line]:
                        continue
                    remainder = (word[point:], *words[word_index + 1:])
                    tail = solve(tuple(remainder), line + 1)
                    if tail is None:
                        continue
                    empty = (widths[line] - used) / max(1.0, widths[line])
                    result = (tail[0] + empty * empty + 0.08, (candidate, *tail[1]))
                    if best is None or result[0] < best[0]:
                        best = result
                prefix_words.append(word)
        if best is None and hyphenate and language == "es":
            prefix_words = []
            for word_index, word in enumerate(words):
                base = " ".join(prefix_words)
                for point in reversed(_emergency_hyphen_points(word)):
                    if unicodedata.category(word[point]).startswith("M"):
                        continue
                    candidate = f"{base} {word[:point]}-".strip()
                    used = _visible_width(candidate, measure, hanging)
                    if used > widths[line]:
                        continue
                    remainder = (word[point:], *words[word_index + 1:])
                    tail = solve(tuple(remainder), line + 1)
                    if tail is None:
                        continue
                    empty = (widths[line] - used) / max(1.0, widths[line])
                    result = (tail[0] + empty * empty + 0.5, (candidate, *tail[1]))
                    if best is None or result[0] < best[0]:
                        best = result
                prefix_words.append(word)
        return best

    result = solve(initial, 0)
    return list(result[1]) if result else None


def linguistic_wrap(
    text: str, widths: list[float], measure: Callable[[str], float], language: str = "auto",
    hyphenate: bool = True, orphan_control: bool = True, hanging: bool = True,
) -> list[str] | None:
    """Wrap text without ever removing an explicit line break.

    ``widths`` describes the exact number of visual rows requested by the
    layout engine. Hard breaks partition those rows; the dynamic allocator may
    wrap inside each paragraph but can never join two user-authored lines.
    """
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    selected = detect_language(normalized, language)
    paragraphs = normalized.split("\n")
    if len(widths) < len(paragraphs):
        return None

    def wrap_paragraph(paragraph: str, paragraph_widths: list[float]) -> list[str] | None:
        if not paragraph:
            return [""] if len(paragraph_widths) == 1 else None
        if selected in {"zh", "ja"}:
            return _cjk_wrap(paragraph, paragraph_widths, measure, orphan_control, hanging)
        if selected == "ko" and not re.search(r"\s", paragraph.strip()):
            return _cjk_wrap(paragraph, paragraph_widths, measure, orphan_control, hanging)
        return _word_wrap(
            paragraph, paragraph_widths, measure, selected,
            hyphenate, orphan_control, hanging,
        )

    @lru_cache(maxsize=4096)
    def allocate(paragraph_index: int, width_index: int):
        if paragraph_index == len(paragraphs):
            return (0.0, ()) if width_index == len(widths) else None
        remaining_paragraphs = len(paragraphs) - paragraph_index - 1
        maximum = len(widths) - width_index - remaining_paragraphs
        if maximum < 1:
            return None
        paragraph = paragraphs[paragraph_index]
        counts = (1,) if not paragraph else range(1, maximum + 1)
        best = None
        for count in counts:
            selected_widths = list(widths[width_index:width_index + count])
            lines = wrap_paragraph(paragraph, selected_widths)
            if lines is None:
                continue
            tail = allocate(paragraph_index + 1, width_index + count)
            if tail is None:
                continue
            ragged = sum(
                ((line_width - _visible_width(line, measure, hanging)) / max(1.0, line_width)) ** 2
                for line, line_width in zip(lines, selected_widths)
            )
            result = (ragged + tail[0], (tuple(lines), *tail[1]))
            if best is None or result[0] < best[0]:
                best = result
        return best

    result = allocate(0, 0)
    if result is None:
        return None
    return [line for paragraph_lines in result[1] for line in paragraph_lines]


def font_supports_text(family, text: str) -> bool:
    try:
        from PySide6.QtGui import QFont, QRawFont
        font = QFont(family) if isinstance(family, QFont) else QFont(str(family))
        raw = QRawFont.fromFont(font)
        if not raw.isValid():
            return True

        def supported(char: str) -> bool:
            category = unicodedata.category(char)
            if char.isspace() or category.startswith("C") or raw.supportsCharacter(char):
                return True
            decomposed = unicodedata.normalize("NFD", char)
            if decomposed != char:
                return all(
                    unicodedata.category(part).startswith("M") or raw.supportsCharacter(part)
                    for part in decomposed
                )
            return category.startswith("M")

        return all(supported(char) for char in str(text))
    except Exception:
        return True
