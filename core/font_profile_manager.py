from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

from core.translation_manager import DEFAULT_TRANSLATION_PROMPT, normalize_glossary, upgrade_translation_prompt


class FontProfileManager:
    """Internal hierarchy of project types, projects and named font roles."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str, label: str = "El nombre") -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name).strip().strip(".")
        if not cleaned:
            raise ValueError(f"{label} no puede estar vacío.")
        return cleaned

    def project_types(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            (path.name for path in self.root.iterdir() if path.is_dir() and not path.name.startswith(".")),
            key=str.casefold,
        )

    def type_path(self, project_type: str) -> Path:
        return self.root / self._safe_name(project_type, "El tipo de proyecto")

    def create_type(self, name: str) -> str:
        project_type = self._safe_name(name, "El tipo de proyecto")
        destination = self.type_path(project_type)
        if destination.exists():
            raise ValueError(f"Ya existe el tipo de proyecto '{project_type}'.")
        destination.mkdir(parents=True)
        return project_type

    def rename_type(self, current: str, new_name: str) -> str:
        renamed = self._safe_name(new_name, "El tipo de proyecto")
        source = self.type_path(current)
        destination = self.type_path(renamed)
        if not source.is_dir():
            raise ValueError(f"No existe el tipo de proyecto '{current}'.")
        if source.resolve() != destination.resolve() and destination.exists():
            raise ValueError(f"Ya existe el tipo de proyecto '{renamed}'.")
        if source.resolve() != destination.resolve():
            source.rename(destination)
        for project in self.projects(renamed):
            profile = self.load(renamed, project)
            profile["type"] = renamed
            self._save(renamed, project, profile)
        return renamed

    def delete_type(self, project_type: str) -> None:
        destination = self.type_path(project_type)
        if not destination.is_dir():
            raise ValueError(f"No existe el tipo de proyecto '{project_type}'.")
        shutil.rmtree(destination)

    def projects(self, project_type: str) -> list[str]:
        folder = self.type_path(project_type) if project_type else None
        if folder is None or not folder.is_dir():
            return []
        return sorted(
            (path.name for path in folder.iterdir() if path.is_dir() and not path.name.startswith(".")),
            key=str.casefold,
        )

    def project_path(self, project_type: str, project: str) -> Path:
        return self.type_path(project_type) / self._safe_name(project, "El nombre del proyecto")

    def _profile_path(self, project_type: str, project: str) -> Path:
        return self.project_path(project_type, project) / "profile.json"

    def has_project(self, project_type: str, project: str) -> bool:
        return bool(project_type and project and self.project_path(project_type, project).is_dir())

    def load(self, project_type: str, project: str) -> dict:
        path = self._profile_path(project_type, project)
        if not path.is_file():
            return self._empty_profile(project_type, project)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_profile(project_type, project)
        data.setdefault("type", project_type)
        data.setdefault("name", project)
        data.setdefault("fonts", {})
        translation = data.setdefault("translation", {})
        translation.setdefault("prompt", DEFAULT_TRANSLATION_PROMPT)
        translation["prompt"] = upgrade_translation_prompt(translation.get("prompt"))
        translation["glossary"] = normalize_glossary(translation.get("glossary", []))
        return data

    @staticmethod
    def _empty_profile(project_type: str, project: str) -> dict:
        return {
            "type": project_type, "name": project, "fonts": {},
            "translation": {"prompt": DEFAULT_TRANSLATION_PROMPT, "glossary": []},
        }

    def _save(self, project_type: str, project: str, data: dict) -> None:
        folder = self.project_path(project_type, project)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / "profile.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)

    def create_project(self, project_type: str, name: str) -> str:
        if not project_type:
            raise ValueError("Crea o selecciona primero un tipo de proyecto.")
        project = self._safe_name(name, "El nombre del proyecto")
        if self.project_path(project_type, project).exists():
            raise ValueError(f"Ya existe el proyecto '{project}'.")
        self._save(project_type, project, self._empty_profile(project_type, project))
        return project

    def rename_project(self, project_type: str, current: str, new_name: str) -> str:
        renamed = self._safe_name(new_name, "El nombre del proyecto")
        source = self.project_path(project_type, current)
        destination = self.project_path(project_type, renamed)
        if not source.is_dir():
            raise ValueError(f"No existe el proyecto '{current}'.")
        if source.resolve() != destination.resolve() and destination.exists():
            raise ValueError(f"Ya existe el proyecto '{renamed}'.")
        if source.resolve() != destination.resolve():
            source.rename(destination)
        profile = self.load(project_type, renamed)
        profile["name"] = renamed
        profile["type"] = project_type
        self._save(project_type, renamed, profile)
        return renamed

    def delete_project(self, project_type: str, project: str) -> None:
        destination = self.project_path(project_type, project)
        if not destination.is_dir():
            raise ValueError(f"No existe el proyecto '{project}'.")
        shutil.rmtree(destination)

    def font_entries(self, project_type: str, project: str) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        if not project_type or not project:
            return entries
        for alias, value in self.load(project_type, project).get("fonts", {}).items():
            entries[str(alias)] = {"family": value, "file": "", "style": {}} if isinstance(value, str) else {
                "family": str(value.get("family", "")), "file": str(value.get("file", "")),
                "style": dict(value.get("style", {})) if isinstance(value.get("style", {}), dict) else {},
            }
        return entries

    def dialogue_font(self, project_type: str, project: str) -> tuple[str, dict] | None:
        """Return the project's dialogue role independent of accents/case."""
        for alias, entry in self.font_entries(project_type, project).items():
            normalized = "".join(
                character for character in unicodedata.normalize("NFKD", alias)
                if not unicodedata.combining(character)
            ).strip().casefold()
            if normalized in {"dialogo", "dialogue"}:
                return alias, entry
        return None

    def set_font(
        self,
        project_type: str,
        project: str,
        alias: str,
        family: str,
        file_name: str = "",
        previous_alias: str = "",
        style: dict | None = None,
    ) -> None:
        alias = alias.strip()
        if not alias or not family.strip():
            raise ValueError("Indica el nombre de uso y la familia tipográfica.")
        data = self.load(project_type, project)
        collision = next(
            (name for name in data["fonts"] if name.casefold() == alias.casefold()), "",
        )
        if collision and collision.casefold() != str(previous_alias or "").casefold():
            raise ValueError(f"Ya existe una asignación llamada '{collision}'. Selecciónala para editarla.")
        previous_entry = data["fonts"].get(previous_alias, {}) if previous_alias else {}
        if previous_alias and previous_alias != alias:
            data["fonts"].pop(previous_alias, None)
        stored_style = style if style is not None else (
            previous_entry.get("style", {}) if isinstance(previous_entry, dict) else {}
        )
        data["fonts"][alias] = {
            "family": family.strip(), "file": file_name,
            "style": dict(stored_style or {}),
        }
        self._save(project_type, project, data)

    def remove_font(self, project_type: str, project: str, alias: str) -> None:
        data = self.load(project_type, project)
        data["fonts"].pop(alias, None)
        self._save(project_type, project, data)

    def translation_profile(self, project_type: str, project: str) -> dict:
        data = self.load(project_type, project).get("translation", {})
        return {
            "prompt": str(data.get("prompt") or DEFAULT_TRANSLATION_PROMPT),
            "glossary": normalize_glossary(data.get("glossary", [])),
        }

    def set_translation_profile(
        self, project_type: str, project: str, prompt: str, glossary,
    ) -> None:
        if not self.has_project(project_type, project):
            raise ValueError("Selecciona un proyecto válido para guardar su traducción.")
        data = self.load(project_type, project)
        data["translation"] = {
            "prompt": str(prompt).strip() or DEFAULT_TRANSLATION_PROMPT,
            "glossary": normalize_glossary(glossary),
        }
        self._save(project_type, project, data)

    def merge_glossary(self, project_type: str, project: str, terms) -> list[dict]:
        profile = self.translation_profile(project_type, project)
        combined = normalize_glossary([*profile["glossary"], *normalize_glossary(terms)])
        self.set_translation_profile(project_type, project, profile["prompt"], combined)
        return combined

    def import_font(self, project_type: str, project: str, source: Path) -> str:
        if source.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
            raise ValueError("Selecciona una fuente TTF, OTF o TTC.")
        project_folder = self.project_path(project_type, project)
        destination_folder = project_folder / "fonts"
        destination_folder.mkdir(parents=True, exist_ok=True)
        destination = destination_folder / source.name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return destination.relative_to(project_folder).as_posix()

    def font_file(self, project_type: str, project: str, file_name: str) -> Path | None:
        if not file_name:
            return None
        candidate = self.project_path(project_type, project) / file_name
        return candidate if candidate.is_file() else None

    def import_legacy_root(self, legacy_root: Path, default_type: str = "Manhwas") -> list[str]:
        """Copy old flat profiles into the internal hierarchy without deleting originals."""
        if not legacy_root.is_dir() or legacy_root.resolve() == self.root.resolve():
            return []
        legacy_projects = [
            path for path in legacy_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
        if not legacy_projects:
            return []
        target_type = self.type_path(default_type)
        target_type.mkdir(parents=True, exist_ok=True)
        imported: list[str] = []
        for source in legacy_projects:
            destination = target_type / source.name
            if destination.exists():
                continue
            shutil.copytree(source, destination)
            imported.append(source.name)
        return imported
