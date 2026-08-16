"""Context-aware translation adapters with project glossaries."""
from __future__ import annotations

import json
import re
import csv
from io import StringIO
import threading
from time import sleep
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


ALIBABA_TRANSLATION_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
DEEPSEEK_TRANSLATION_URL = "https://api.deepseek.com/chat/completions"
_INTERNAL_BOX_MARKER = re.compile(
    r"(?i)(?:<<<\s*)?\b(?:MSE[\s_-]*)?BOX[\s_-]*\d{1,6}\b(?:\s*>>>)?"
)
_INTERNAL_TOKEN_MARKER = re.compile(
    r"(?i)\b(?:BEGIN|END|START|STOP)\b"
)


class TranslationConfigurationError(RuntimeError):
    pass


LEGACY_TRANSLATION_PROMPT = (
    "Traduce como editor profesional de manhwa. Mantén consistentes nombres propios, títulos, "
    "pronombres, relaciones, tratamientos, lugares, técnicas y terminología entre capítulos. "
    "Conserva la intención, el tono y la personalidad de cada personaje; usa español natural "
    "sin inventar información. No cambies un término que ya aparece en el glosario. Identifica "
    "personajes, lugares, organizaciones, objetos, técnicas y términos recurrentes nuevos que "
    "deban conservarse en capítulos posteriores."
)

DEFAULT_TRANSLATION_PROMPT = (
    "Traduce como editor profesional de manhwa, manhua y manga. Primero identifica el tipo de obra: "
    "manhwa coreano, manhua chino o manga japonés, y respeta sus convenciones culturales, tratamientos "
    "y orden narrativo sin mezclarlos. Mantén consistentes nombres propios, títulos, "
    "pronombres, relaciones, tratamientos, lugares, técnicas y terminología entre capítulos. "
    "Conserva la intención, el tono y la personalidad de cada personaje; usa español natural "
    "sin inventar información. No cambies un término que ya aparece en el glosario. Identifica "
    "personajes, lugares, organizaciones, objetos, técnicas y términos recurrentes nuevos que "
    "deban conservarse en capítulos posteriores."
)


def upgrade_translation_prompt(value: str | None) -> str:
    """Upgrade only the old built-in prompt; never overwrite user-authored instructions."""
    prompt = str(value or "").strip()
    return DEFAULT_TRANSLATION_PROMPT if not prompt or prompt == LEGACY_TRANSLATION_PROMPT else prompt

GLOSSARY_CATEGORIES = {
    "personaje", "lugar", "organización", "técnica", "objeto", "título", "término",
}


def _glossary_rows_from_json(value) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    for key in ("terms", "glossary", "entries", "items"):
        nested = value.get(key)
        if isinstance(nested, (list, dict)):
            return _glossary_rows_from_json(nested)
    if any(key in value for key in ("source", "original", "term", "name")):
        return [value]
    return [
        {"source": source, **target} if isinstance(target, dict)
        else {"source": source, "target": target}
        for source, target in value.items()
    ]


def _glossary_text_row(line: str) -> dict | None:
    line = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line.strip())
    if not line or line.startswith("#"):
        return None
    if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", line):
        return None
    if line.startswith("|") and line.endswith("|"):
        line = line[1:-1].strip()
    parts: list[str] = []
    if "\t" in line or "|" in line:
        parts = [part.strip() for part in re.split(r"\t|\s*\|\s*", line, maxsplit=3)]
    else:
        pair = re.split(r"\s*(?:=>|->|→|⟶|=|:|：)\s*", line, maxsplit=1)
        if len(pair) >= 2:
            parts = pair
        else:
            for delimiter in (",", ";"):
                try:
                    parsed = next(csv.reader(StringIO(line), delimiter=delimiter, skipinitialspace=True))
                except (csv.Error, StopIteration):
                    parsed = []
                if len(parsed) >= 2:
                    parts = [part.strip() for part in parsed[:4]]
                    break
            if not parts:
                spaced = [part.strip() for part in re.split(r"\s{2,}", line, maxsplit=3)]
                parts = spaced if len(spaced) >= 2 else [line, line]
    if not parts or not parts[0]:
        return None
    return {
        "source": parts[0],
        "target": parts[1] if len(parts) > 1 and parts[1] else parts[0],
        "category": parts[2] if len(parts) > 2 else "término",
        "note": parts[3] if len(parts) > 3 else "",
    }


def normalize_glossary(value) -> list[dict]:
    """Normalize JSON, CSV, Markdown, arrows and free-form glossary lines."""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            decoded = json.loads(raw)
            value = _glossary_rows_from_json(decoded)
        except json.JSONDecodeError:
            value = [row for line in raw.splitlines() if (row := _glossary_text_row(line))]
    elif isinstance(value, dict):
        value = _glossary_rows_from_json(value)
    if not isinstance(value, list):
        return []
    merged: dict[str, dict] = {}
    for item in value:
        if isinstance(item, str):
            item = _glossary_text_row(item)
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("original") or item.get("term") or item.get("name") or "").strip()
        target = str(
            item.get("target") or item.get("translation") or item.get("translated")
            or item.get("value") or item.get("es") or source
        ).strip()
        if not source or not target:
            continue
        category = str(item.get("category") or "término").strip().lower()
        if category not in GLOSSARY_CATEGORIES:
            category = "término"
        entry = {
            "source": source, "target": target, "category": category,
            "note": str(item.get("note") or "").strip(),
        }
        key = source.casefold()
        if key in merged:
            if not merged[key]["note"] and entry["note"]:
                merged[key]["note"] = entry["note"]
            continue
        merged[key] = entry
    return list(merged.values())


def glossary_to_text(value) -> str:
    return "\n".join(
        f"{item['source']}\t{item['target']}\t{item['category']}\t{item['note']}".rstrip()
        for item in normalize_glossary(value)
    )


class TranslationManager:
    _BATCH_MARKER = re.compile(r"<<<MSE_BOX_(\d{4})>>>")
    _shared_session = None
    _session_lock = threading.Lock()

    def __init__(self) -> None:
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_limit = 256

    @classmethod
    def _get_http_session(cls):
        if cls._shared_session is None:
            with cls._session_lock:
                if cls._shared_session is None:
                    s = requests.Session()
                    try:
                        from requests.adapters import HTTPAdapter
                        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=1)
                        s.mount("https://", adapter)
                        s.mount("http://", adapter)
                    except Exception:
                        pass
                    cls._shared_session = s
        return cls._shared_session

    def _cache_get(self, key: tuple) -> dict | None:
        with self._cache_lock:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return {
                "translations": [
                    self._sanitize_translation(item)
                    for item in value.get("translations", [])
                ],
                "terms": [dict(item) for item in value.get("terms", [])],
            }

    def _cache_put(self, key: tuple, value: dict) -> None:
        stored = {
            "translations": [
                self._sanitize_translation(item)
                for item in value.get("translations", [])
            ],
            "terms": [dict(item) for item in value.get("terms", [])],
        }
        with self._cache_lock:
            self._cache[key] = stored
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)

    @staticmethod
    def _sanitize_translation(value: object) -> str:
        """Remove OCR/batch transport labels from user-visible dialogue."""
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = _INTERNAL_BOX_MARKER.sub("", text)
        text = re.sub(r"(?im)^[ \t]*(?:BEGIN|END|START|STOP)[ \t]*$", "", text)
        text = re.sub(r"(?i)\b(?:BEGIN|END)\b", "", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @staticmethod
    def _translation_batches(texts: list[str], max_items: int = 16, max_chars: int = 6000) -> list[list[tuple[int, str]]]:
        batches: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        chars = 0
        for index, text in enumerate(texts):
            length = len(str(text)) + 24
            if current and (len(current) >= max_items or chars + length > max_chars):
                batches.append(current)
                current = []
                chars = 0
            current.append((index, str(text)))
            chars += length
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _marked_batch(batch: list[tuple[int, str]]) -> str:
        return "\n\n".join(
            f"<<<MSE_BOX_{slot:04d}>>>\n{text}" for slot, (_index, text) in enumerate(batch, 1)
        )

    @classmethod
    def _parse_marked_batch(cls, value: str, expected: int) -> list[str]:
        matches = list(cls._BATCH_MARKER.finditer(str(value or "")))
        if len(matches) != expected:
            raise TranslationConfigurationError("El proveedor alteró los separadores del lote.")
        mapped: dict[int, str] = {}
        for position, match in enumerate(matches):
            slot = int(match.group(1))
            end = matches[position + 1].start() if position + 1 < len(matches) else len(value)
            mapped[slot] = value[match.end():end].strip()
        if set(mapped) != set(range(1, expected + 1)):
            raise TranslationConfigurationError("El proveedor omitió una caja del lote.")
        return [mapped[index] for index in range(1, expected + 1)]

    def validate(self, provider: str, api_key: str) -> None:
        if provider not in {"Alibaba Cloud", "Gemini", "OpenAI", "DeepSeek", "DeepL"}:
            raise TranslationConfigurationError(f"Proveedor no compatible: {provider}")
        if not api_key:
            raise TranslationConfigurationError(f"Configura la credencial de {provider} en Configuración.")

    @classmethod
    def _request(
        cls,
        url: str, payload: dict, headers: dict[str, str], *, timeout: float = 35.0,
    ) -> dict:
        provider = (
            "Gemini" if "googleapis.com" in url else
            "DeepSeek" if "deepseek.com" in url else
            "DeepL" if "deepl.com" in url else
            "Alibaba Cloud" if "aliyuncs.com" in url else "El proveedor"
        )
        transient_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        attempts = 3
        session = cls._get_http_session()
        for attempt in range(attempts):
            try:
                response = session.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json", **headers},
                    timeout=max(5.0, float(timeout)),
                )
                if response.status_code in transient_codes and attempt + 1 < attempts:
                    retry_after = str(response.headers.get("Retry-After", "") or "").strip()
                    delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 0.5 * (attempt + 1)
                    sleep(min(3.0, max(0.2, delay)))
                    continue
                if response.status_code != 200:
                    detail = response.text[:500]
                    lower = detail.casefold()
                    if response.status_code == 402 or "insufficient balance" in lower:
                        message = f"{provider}: saldo insuficiente. Recarga saldo en la cuenta del proveedor."
                    elif response.status_code in {401, 403}:
                        message = f"{provider}: la API key no es válida o no tiene permiso para ese modelo."
                    elif response.status_code == 429:
                        message = f"{provider}: se agotó la cuota o hay demasiadas solicitudes. Intenta nuevamente en unos segundos."
                    elif response.status_code == 404 or "model_not_found" in lower:
                        message = f"{provider}: el modelo configurado no existe o no está disponible para esta cuenta."
                    else:
                        message = f"{provider} respondió HTTP {response.status_code}: {detail}"
                    raise TranslationConfigurationError(message)
                return response.json()
            except requests.RequestException as error:
                if attempt + 1 < attempts:
                    sleep(0.5 * (attempt + 1))
                    continue
                raise TranslationConfigurationError(
                    f"{provider} no respondió después de {attempts} intentos: {error}"
                ) from error
            except json.JSONDecodeError as error:
                raise TranslationConfigurationError(
                    f"{provider} devolvió una respuesta de red inválida."
                ) from error
        raise TranslationConfigurationError(f"{provider} no respondió.")

    def _translate_structured_batch(
        self, texts: list[str], provider: str, model: str, api_key: str,
        source: str, target: str, glossary, prompt: str, context: str,
    ) -> dict:
        """Translate one bounded batch through a JSON-capable chat provider."""
        instruction = self._prompt(texts, source, target, glossary, prompt, context)
        content = ""
        parse_error: TranslationConfigurationError | None = None
        for format_attempt in range(2):
            if provider == "Gemini":
                data = self._request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model or 'gemini-2.5-flash'}:generateContent",
                    {
                        "contents": [{"role": "user", "parts": [{"text": instruction}]}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            "temperature": 0.2,
                        },
                    },
                    {"x-goog-api-key": api_key}, timeout=32.0,
                )
                try:
                    content = str(data["candidates"][0]["content"]["parts"][0]["text"])
                except (KeyError, IndexError, TypeError) as error:
                    feedback = json.dumps(data.get("promptFeedback", {}), ensure_ascii=False)[:300]
                    raise TranslationConfigurationError(
                        f"Gemini no devolvió texto. Revisa filtros y modelo. {feedback}"
                    ) from error
            elif provider == "OpenAI":
                data = self._request(
                    "https://api.openai.com/v1/responses",
                    {"model": model or "gpt-5-mini", "input": instruction},
                    {"Authorization": f"Bearer {api_key}"}, timeout=32.0,
                )
                try:
                    content = str(data.get("output_text") or data["output"][0]["content"][0]["text"])
                except (KeyError, IndexError, TypeError) as error:
                    raise TranslationConfigurationError("OpenAI devolvió una respuesta sin contenido.") from error
            elif provider == "DeepSeek":
                data = self._request(
                    DEEPSEEK_TRANSLATION_URL,
                    {
                        "model": model or "deepseek-chat",
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Eres un traductor profesional de manhwa, manhua y manga. "
                                    "Responde solamente con el objeto JSON solicitado, sin Markdown."
                                ),
                            },
                            {"role": "user", "content": instruction},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                        "max_tokens": 4096,
                    },
                    {"Authorization": f"Bearer {api_key}"}, timeout=32.0,
                )
                try:
                    content = str(data["choices"][0]["message"]["content"] or "")
                except (KeyError, IndexError, TypeError) as error:
                    raise TranslationConfigurationError("DeepSeek devolvió una respuesta sin contenido.") from error
            else:
                data = self._request(
                    ALIBABA_TRANSLATION_URL,
                    {
                        "model": model or "qwen-plus",
                        "messages": [{"role": "user", "content": instruction}],
                        "response_format": {"type": "json_object"},
                    },
                    {"Authorization": f"Bearer {api_key}"}, timeout=32.0,
                )
                try:
                    content = str(data["choices"][0]["message"]["content"])
                except (KeyError, IndexError, TypeError) as error:
                    raise TranslationConfigurationError("Alibaba Cloud devolvió una respuesta sin contenido.") from error
            try:
                return self._json_result(content, len(texts))
            except TranslationConfigurationError as error:
                parse_error = error
                if format_attempt == 0 and provider in {"Gemini", "DeepSeek"}:
                    instruction += "\nIMPORTANTE: la respuesta anterior fue inválida. Devuelve JSON completo y una traducción por entrada."
                    continue
                raise
        raise parse_error or TranslationConfigurationError(f"{provider} no devolvió una traducción utilizable.")

    @staticmethod
    def _prompt(texts: list[str], source: str, target: str, glossary, custom: str, context: str = "") -> str:
        return (
            f"Traduce de {source or 'detección automática'} a {target}. "
            "El contenido puede ser manhwa coreano, manhua chino o manga japonés: identifica cuál es "
            "por el idioma y contexto, conserva sus convenciones y no mezcles tratamientos entre medios. "
            "Devuelve EXCLUSIVAMENTE un objeto JSON con esta forma exacta: "
            '{"translations":["..."],"terms":[{"source":"...","target":"...",'
            '"category":"personaje|lugar|organización|técnica|objeto|título|término","note":"..."}]}. '
            "translations debe tener el mismo número y orden de entradas. terms incluye solamente "
            "entidades o vocabulario recurrente nuevo que no figure en el glosario; no incluyas "
            "palabras comunes. Conserva signos y saltos internos.\n"
            f"Glosario obligatorio:\n{json.dumps(normalize_glossary(glossary), ensure_ascii=False)}\n"
            f"Contexto reciente:\n{context or '(sin contexto anterior)'}\n"
            f"Instrucciones permanentes del proyecto:\n{custom or DEFAULT_TRANSLATION_PROMPT}\n"
            f"Entradas:\n{json.dumps(texts, ensure_ascii=False)}"
        )

    @staticmethod
    def _json_list(value: str, expected: int) -> list[str]:
        return TranslationManager._json_result(value, expected)["translations"]

    @staticmethod
    def _json_result(value: str, expected: int) -> dict:
        value = value.strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise TranslationConfigurationError("El proveedor no devolvió el JSON solicitado.") from error
        if isinstance(decoded, list):
            translations, terms = decoded, []
        elif isinstance(decoded, dict):
            translations, terms = decoded.get("translations", []), decoded.get("terms", [])
        else:
            translations, terms = [], []
        if not isinstance(translations, list) or len(translations) != expected:
            raise TranslationConfigurationError("La cantidad de traducciones no coincide con las cajas.")
        return {
            "translations": [str(item).strip() for item in translations],
            "terms": normalize_glossary(terms),
        }

    @staticmethod
    def _protect_glossary(texts: list[str], glossary) -> tuple[list[str], dict[str, str]]:
        protected = list(texts)
        replacements: dict[str, str] = {}
        entries = sorted(normalize_glossary(glossary), key=lambda item: len(item["source"]), reverse=True)
        for index, item in enumerate(entries):
            marker = f"__MSE_TERM_{index:04d}__"
            changed = False
            for position, text in enumerate(protected):
                replaced = text.replace(item["source"], marker)
                changed = changed or replaced != text
                protected[position] = replaced
            if changed:
                replacements[marker] = item["target"]
        return protected, replacements

    @staticmethod
    def _alibaba_translation_memory(context: str, limit: int = 12) -> list[dict]:
        memory: list[dict] = []
        for line in str(context or "").splitlines():
            if "→" not in line:
                continue
            source, target = (part.strip() for part in line.split("→", 1))
            if source and target:
                memory.append({"source": source, "target": target})
            if len(memory) >= limit:
                break
        return memory

    def _translate_alibaba_mt(
        self, texts: list[str], model: str, api_key: str, source: str, target: str,
        glossary, prompt: str, context: str, progress, cancelled,
    ) -> dict:
        """Use Qwen-MT's native glossary, memory and domain controls."""
        terms = [
            {"source": item["source"], "target": item["target"]}
            for item in normalize_glossary(glossary)
        ]
        memory = self._alibaba_translation_memory(context)
        domain = (
            "Professional comic localization. The source is a Korean manhwa, Chinese manhua, or "
            "Japanese manga. Preserve character voice, honorifics, relationships, names, places, "
            "techniques, sound effects, continuity, and glossary terminology. Produce natural target-language dialogue."
            " When input contains markers like <<<MSE_BOX_0001>>>, preserve every marker "
            "exactly and translate only the text that follows it."
        )
        custom = str(prompt or "").strip()
        if custom:
            domain += " Project instructions: " + custom
        options: dict = {
            "source_lang": str(source or "auto").lower(),
            "target_lang": str(target or "es").lower(),
            "domains": domain[:2000],
        }
        if terms:
            options["terms"] = terms[:100]
        if memory:
            options["tm_list"] = memory

        results = [""] * len(texts)
        batches = self._translation_batches(texts, max_items=32, max_chars=12000)
        workers = min(6, max(1, len(batches)))

        def request_translation(value: str) -> str:
            if cancelled and cancelled():
                return ""
            data = self._request(
                ALIBABA_TRANSLATION_URL,
                {
                    "model": model or "qwen-mt-flash",
                    "messages": [{"role": "user", "content": value}],
                    "translation_options": options,
                },
                {"Authorization": f"Bearer {api_key}"},
            )
            try:
                return str(data["choices"][0]["message"]["content"]).strip()
            except (KeyError, IndexError, TypeError) as error:
                raise TranslationConfigurationError("Alibaba Cloud devolvió una respuesta sin traducción.") from error

        def translate_batch(batch: list[tuple[int, str]]) -> list[tuple[int, str]]:
            if len(batch) == 1:
                index, value = batch[0]
                return [(index, request_translation(value))]
            translated = request_translation(self._marked_batch(batch))
            try:
                values = self._parse_marked_batch(translated, len(batch))
                return [(item[0], value) for item, value in zip(batch, values)]
            except TranslationConfigurationError:
                return [(index, request_translation(value)) for index, value in batch]

        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(translate_batch, batch) for batch in batches]
            for future in as_completed(futures):
                if cancelled and cancelled():
                    return {"translations": [], "terms": []}
                translated_items = future.result()
                for index, translated in translated_items:
                    results[index] = translated
                completed += len(translated_items)
                if progress:
                    progress(min(95, int(completed * 95 / max(1, len(texts)))))

        discovered_terms: list[dict] = []
        if not (cancelled and cancelled()):
            extraction_prompt = (
                "You are maintaining a localization glossary for Korean manhwa, Chinese manhua, and Japanese manga. "
                "From the aligned source/translation pairs, return ONLY JSON as "
                '{"terms":[{"source":"...","target":"...","category":"personaje|lugar|organización|técnica|objeto|título|término","note":"..."}]}. '
                "Include only recurring proper names, places, organizations, titles, techniques, objects, or special terms "
                "not already present in the glossary; never include common words.\n"
                f"Existing glossary: {json.dumps(normalize_glossary(glossary), ensure_ascii=False)}\n"
                f"Pairs: {json.dumps([{'source': a, 'target': b} for a, b in zip(texts, results)], ensure_ascii=False)}"
            )
            try:
                extracted = self._request(
                    ALIBABA_TRANSLATION_URL,
                    {
                        "model": "qwen-plus", "messages": [{"role": "user", "content": extraction_prompt}],
                        "response_format": {"type": "json_object"},
                    },
                    {"Authorization": f"Bearer {api_key}"},
                    timeout=15.0,
                )
                content = str(extracted["choices"][0]["message"]["content"]).strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
                decoded = json.loads(content)
                discovered_terms = normalize_glossary(decoded.get("terms", []) if isinstance(decoded, dict) else [])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, TranslationConfigurationError):
                discovered_terms = []
        return {"translations": results, "terms": discovered_terms}

    def translate_with_context(
        self, texts: list[str], provider: str, model: str, api_key: str,
        source: str, target: str, glossary=None, prompt: str = "", context: str = "",
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        self.validate(provider, api_key)
        if not texts or (cancelled and cancelled()):
            return {"translations": [], "terms": []}
        glossary = normalize_glossary(glossary)
        cache_key = (
            provider, str(model or ""), str(source or ""), str(target or ""), tuple(map(str, texts)),
            json.dumps(glossary, ensure_ascii=False, sort_keys=True), str(prompt or ""), str(context or ""),
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            if progress:
                progress(100)
            return cached
        if progress:
            progress(3)
        if provider == "Alibaba Cloud" and str(model or "").lower().startswith("qwen-mt"):
            result = self._translate_alibaba_mt(
                texts, model, api_key, source, target, glossary, prompt, context,
                progress, cancelled,
            )
        elif provider == "DeepL":
            protected, replacements = self._protect_glossary(texts, glossary)
            endpoint = "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"
            data = self._request(
                endpoint,
                {"text": protected, "target_lang": target.upper(), **({"source_lang": source.upper()} if source else {})},
                {"Authorization": f"DeepL-Auth-Key {api_key}"},
            )
            translations = [str(item.get("text", "")) for item in data.get("translations", [])]
            for index, translated in enumerate(translations):
                for marker, replacement in replacements.items():
                    translated = translated.replace(marker, replacement).replace(marker.lower(), replacement)
                translations[index] = translated
            result = {"translations": translations, "terms": []}
        else:
            batches = self._translation_batches(texts, max_items=28, max_chars=10000)
            combined = [""] * len(texts)
            discovered: list[dict] = []

            def translate_batch(batch: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[dict]]:
                values = [value for _index, value in batch]
                translated = self._translate_structured_batch(
                    values, provider, model, api_key, source, target,
                    glossary, prompt, context,
                )
                return (
                    [(entry[0], value) for entry, value in zip(batch, translated["translations"])],
                    list(translated.get("terms", [])),
                )

            completed = 0
            workers = min(6, max(1, len(batches)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(translate_batch, batch) for batch in batches]
                for future in as_completed(futures):
                    if cancelled and cancelled():
                        return {"translations": [], "terms": []}
                    translated_items, terms = future.result()
                    for index, translated in translated_items:
                        combined[index] = translated
                    discovered.extend(terms)
                    completed += len(translated_items)
                    if progress:
                        progress(min(98, 3 + int(completed * 95 / max(1, len(texts)))))
            result = {"translations": combined, "terms": normalize_glossary(discovered)}
        if len(result["translations"]) != len(texts):
            raise TranslationConfigurationError("El proveedor devolvió una cantidad incorrecta de traducciones.")
        result["translations"] = [
            self._sanitize_translation(value) for value in result["translations"]
        ]
        if not (cancelled and cancelled()):
            self._cache_put(cache_key, result)
        if progress:
            progress(100)
        return result

    def translate(
        self, texts: list[str], provider: str, model: str, api_key: str,
        source: str, target: str, glossary="", prompt: str = "",
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[str]:
        return self.translate_with_context(
            texts, provider, model, api_key, source, target, glossary, prompt, "", progress, cancelled,
        )["translations"]
