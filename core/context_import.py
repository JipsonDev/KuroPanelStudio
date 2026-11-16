"""Import a series' glossary and context from a .txt file into KuroPanel.

The user asks the AI chat where the series is translated for its glossary
and context with the prompt below (``CONTEXT_REQUEST``), saves the answer as
a .txt (or pastes it), and it becomes the glossary and permanent
instructions of the matching translation project. The text is parsed leniently: headings may
come as Markdown, bold or plain «GLOSARIO:», and a JSON answer works too.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from core.translation_manager import normalize_glossary

CONTEXT_REQUEST = """Con todo lo que sabes de esta obra en este proyecto (capítulos traducidos, nombres, decisiones de traducción), prepárame su ficha para mi programa de traducción. Responde SOLO con este formato, sin comentarios antes ni después:

OBRA: <título de la obra>

## CONTEXTO
<De 5 a 15 líneas: de qué trata, situación actual de la historia, personajes principales y cómo se relacionan entre ellos.>

## ESTILO
<Reglas de traducción que usamos: tono de cada personaje, tú/usted/vos entre quiénes, honoríficos y apodos, cómo se escriben técnicas, rangos y lugares, y cualquier decisión fija.>

## GLOSARIO
original | traducción | categoría | nota
<una línea por término>

Reglas del glosario:
- «original» es el término tal como aparece en el texto que se traduce (chino, coreano o japonés si lo conoces; si no, el nombre en inglés).
- «categoría» es una de: personaje, lugar, organización, técnica, objeto, título, término.
- «nota» es opcional y breve (por ejemplo: «hermana mayor del protagonista», «siempre en mayúsculas»).
- Incluye todos los nombres de personajes, lugares, sectas/clanes, técnicas, objetos y títulos que ya decidimos."""

CONTEXT_START = "[Contexto de la obra · importado]"
CONTEXT_END = "[Fin del contexto de la obra]"

_SECTIONS = {
    "context": ("contexto", "historia", "resumen", "sinopsis", "context", "story", "summary"),
    "style": ("estilo", "reglas", "tono", "convenciones", "style", "rules"),
    "glossary": ("glosario", "terminos", "nombres", "glossary", "terms"),
}


@dataclass
class ImportedContext:
    title: str = ""
    context: str = ""
    style: str = ""
    glossary: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.context or self.style or self.glossary)


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(char for char in value if not unicodedata.combining(char)).casefold()


def _heading(line: str) -> tuple[str | None, str]:
    """Section name of a heading line, and any text written after it."""
    raw = line.strip()
    if not raw:
        return None, ""
    marked = raw.startswith(("#", "**", "__"))
    body = raw.lstrip("#").strip()
    match = re.match(r"^(.*?)[:：](.*)$", body)
    head, rest = (match.group(1), match.group(2)) if match else (body, "")
    head = head.strip(" *_#")
    word = _plain(head).strip()
    words = word.split()
    if not words or len(word) > 30:
        return None, ""
    for section, names in _SECTIONS.items():
        if word in names or (words[0] in names and len(words) <= 3):
            if marked or match or body.strip(" *_#") == head:
                return section, rest.strip(" *_")
    return None, ""


def _without_fences(text: str) -> str:
    return re.sub(r"^\s*```[\w-]*\s*$", "", str(text or ""), flags=re.MULTILINE).strip()


def _from_json(text: str) -> ImportedContext | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    lowered = {_plain(str(key)): value for key, value in data.items()}

    def pick(*names):
        for name in names:
            value = lowered.get(name)
            if value:
                return value
        return ""

    def text_of(value) -> str:
        if isinstance(value, list):
            return "\n".join(f"- {item}" for item in value)
        return str(value or "").strip()

    return ImportedContext(
        title=text_of(pick("obra", "titulo", "title")),
        context=text_of(pick("contexto", "context", "historia", "resumen")),
        style=text_of(pick("estilo", "style", "reglas", "rules")),
        glossary=normalize_glossary(pick("glosario", "glossary", "terminos", "terms") or []),
    )


def parse_context(text: str) -> ImportedContext:
    text = _without_fences(text)
    parsed = _from_json(text)
    if parsed is not None:
        return parsed
    result = ImportedContext()
    buckets: dict[str, list[str]] = {"context": [], "style": [], "glossary": []}
    current: str | None = None
    for line in text.splitlines():
        title = re.match(r"^\s*(?:\*\*)?\s*(?:obra|t[ií]tulo|title)\s*(?:\*\*)?\s*[:：]\s*(.+)$", line, re.IGNORECASE)
        if title and current is None:
            result.title = title.group(1).strip(" *")
            continue
        section, rest = _heading(line)
        if section:
            current = section
            if rest:
                buckets[section].append(rest)
            continue
        if current:
            buckets[current].append(line)
    if not any(buckets.values()):
        # No headings: a bare glossary pasted on its own.
        buckets["glossary"] = text.splitlines()
    result.context = "\n".join(buckets["context"]).strip()
    result.style = "\n".join(buckets["style"]).strip()
    rows = [line for line in buckets["glossary"]
            if line.strip() and not re.match(r"^\s*\|?\s*original\s*\|", line, re.IGNORECASE)]
    result.glossary = normalize_glossary("\n".join(rows))
    return result


def merged_prompt(prompt: str, imported: ImportedContext) -> str:
    """Put the imported context in the permanent prompt, replacing an earlier import."""
    pattern = re.compile(re.escape(CONTEXT_START) + r".*?" + re.escape(CONTEXT_END), re.DOTALL)
    base = pattern.sub("", str(prompt or "")).strip()
    parts = []
    if imported.title:
        parts.append(f"Obra: {imported.title}")
    if imported.context:
        parts.append(f"Contexto de la historia:\n{imported.context}")
    if imported.style:
        parts.append(f"Reglas de estilo que se deben respetar:\n{imported.style}")
    if not parts:
        return base
    block = "\n".join([CONTEXT_START, *parts, CONTEXT_END])
    return f"{base}\n\n{block}" if base else block


def merged_glossary(existing, imported: ImportedContext) -> list[dict]:
    """Imported terms win over older entries with the same original."""
    return normalize_glossary([*imported.glossary, *normalize_glossary(existing)])
