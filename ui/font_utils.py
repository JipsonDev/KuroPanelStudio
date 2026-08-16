from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QTimer, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QComboBox


_FALLBACK_FONTS = ("Segoe UI", "Arial", "Times New Roman", "Comic Sans MS", "Impact")
_FONT_FAMILIES: tuple[str, ...] | None = None
_REGISTERED_FONT_FILES: set[str] = set()
_FONT_CACHE_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "ManhuaSuiteEditor" / "font_families.json"


def _font_directories_signature() -> list[list[int | str]]:
    roots = [Path("C:/Windows/Fonts")]
    local_fonts = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
    if local_fonts.is_dir():
        roots.append(local_fonts)
    signature: list[list[int | str]] = []
    for root in roots:
        try:
            # Directory mtime changes when a font file is added or removed;
            # avoid enumerating thousands of files just to validate the cache.
            signature.append([str(root), root.stat().st_mtime_ns])
        except OSError:
            continue
    return signature


def _read_disk_cache() -> tuple[str, ...] | None:
    try:
        data = json.loads(_FONT_CACHE_PATH.read_text(encoding="utf-8"))
        families = data.get("families")
        if (
            data.get("signature") == _font_directories_signature()
            and isinstance(families, list)
            and len(families) > len(_FALLBACK_FONTS)
        ):
            return tuple(str(family) for family in families if family)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def _write_disk_cache(families: tuple[str, ...]) -> None:
    if len(families) <= len(_FALLBACK_FONTS):
        return
    try:
        _FONT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _FONT_CACHE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "signature": _font_directories_signature(), "families": families,
        }, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_FONT_CACHE_PATH)
    except OSError:
        pass


def invalidate_font_cache() -> None:
    global _FONT_FAMILIES
    _FONT_FAMILIES = None
    try:
        _FONT_CACHE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def font_families() -> tuple[str, ...]:
    global _FONT_FAMILIES
    if _FONT_FAMILIES is None:
        cached = _read_disk_cache()
        if cached:
            _FONT_FAMILIES = cached
        else:
            unique = {
                family.strip() for family in QFontDatabase.families()
                if family.strip() and not family.startswith("@")
            }
            _FONT_FAMILIES = tuple(sorted(unique, key=str.casefold)) or _FALLBACK_FONTS
            _write_disk_cache(_FONT_FAMILIES)
    return _FONT_FAMILIES


class FontFamilyModel(QAbstractListModel):
    """Virtual model: QFont objects are created only for rows Qt actually paints."""

    BATCH_SIZE = 96

    def __init__(
        self, families: tuple[str, ...], parent=None, selected: str = "", max_rows: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.all_families = families
        self.selected = selected
        self.max_rows = max_rows
        self.families: tuple[str, ...] = ()
        self._matches: tuple[str, ...] = ()
        self._indices: dict[str, int] = {}
        self._query_key: str | None = None
        self.set_filter("")

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.families)

    def set_filter(self, query: str) -> None:
        query_key = query.strip().casefold()
        if query_key == self._query_key and self.families:
            return
        if query_key:
            # Rank useful matches first. A picker that puts an exact family
            # below unrelated contains-matches feels broken to the user.
            exact: list[str] = []
            starts: list[str] = []
            contains: list[str] = []
            # Classify in one pass. The previous list-membership checks became
            # quadratic for broad searches such as a single "a".
            for family in self.all_families:
                family_key = family.casefold()
                if family_key == query_key:
                    exact.append(family)
                elif family_key.startswith(query_key):
                    starts.append(family)
                elif query_key in family_key:
                    contains.append(family)
            matches = [*exact, *starts, *contains]
        else:
            matches = list(self.all_families)
        if self.max_rows is not None:
            matches = matches[:self.max_rows]
        self.beginResetModel()
        # Loading thousands of differently styled rows at once makes the
        # native Windows combo pause when its popup opens. Rows are exposed in
        # batches as the user scrolls; filtering still searches the full list.
        self._matches = tuple(matches)
        self.families = self._matches[:self.BATCH_SIZE]
        self._indices = {family.casefold(): index for index, family in enumerate(self.families)}
        self._query_key = query_key
        self.endResetModel()

    @property
    def total_count(self) -> int:
        return len(self._matches)

    def canFetchMore(self, parent=QModelIndex()) -> bool:  # noqa: N802 - Qt API
        # QListView eagerly calls fetchMore repeatedly while opening, which
        # defeats batching. More rows are requested explicitly by the
        # scrollbar handler installed on the font picker.
        return False

    def fetchMore(self, parent=QModelIndex()) -> None:  # noqa: N802 - Qt API
        self.load_next_batch(parent)

    def load_next_batch(self, parent=QModelIndex()) -> None:
        if parent.isValid() or len(self.families) >= len(self._matches):
            return
        start = len(self.families)
        end = min(start + self.BATCH_SIZE, len(self._matches))
        self.beginInsertRows(QModelIndex(), start, end - 1)
        self.families = self._matches[:end]
        for index in range(start, end):
            self._indices[self.families[index].casefold()] = index
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.families):
            return None
        family = self.families[index.row()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            return family
        if role == Qt.FontRole:
            # Render each entry in its own typeface, like a professional font
            # picker. QFont is requested only for rows the view actually paints.
            return QFont(family, 11)
        return None

    def index_for(self, family: str) -> int:
        return self._indices.get(family.casefold(), -1)


def populate_font_combo(combo: QComboBox, selected: str = "") -> None:
    combo.blockSignals(True)
    selected = selected or "Segoe UI"
    model = FontFamilyModel(font_families(), combo, selected=selected)
    combo.setModel(model)
    index = model.index_for(selected)
    if index >= 0:
        combo.setCurrentIndex(index)
    elif combo.isEditable():
        combo.setEditText(selected)
    else:
        combo.setCurrentIndex(max(0, model.index_for("Segoe UI")))
    combo.blockSignals(False)


def select_font_family(combo: QComboBox, family: str, *, emit: bool = False) -> str:
    """Select a family and restore the complete menu after any search filter."""
    family = str(family or "").strip() or "Segoe UI"
    model = combo.model()
    if not isinstance(model, FontFamilyModel):
        combo.setCurrentText(family)
        return combo.currentText()
    exact = next(
        (candidate for candidate in model.all_families if candidate.casefold() == family.casefold()),
        family,
    )
    previous = combo.blockSignals(not emit)
    model.selected = exact
    model.set_filter("")
    index = model.index_for(exact)
    if index >= 0:
        combo.setCurrentIndex(index)
    else:
        combo.setEditText(exact)
    combo.blockSignals(previous)
    return exact


def commit_font_combo_text(combo: QComboBox) -> str:
    """Resolve typed search text to an installed family instead of a fake name."""
    query = combo.currentText().strip()
    model = combo.model()
    if not isinstance(model, FontFamilyModel):
        return query
    query_key = query.casefold()
    exact = next(
        (family for family in model.all_families if family.casefold() == query_key), "",
    )
    if exact:
        chosen = exact
    else:
        starts = [family for family in model.all_families if family.casefold().startswith(query_key)]
        contains = [family for family in model.all_families if query_key in family.casefold()]
        chosen = (starts or contains or [model.selected or "Segoe UI"])[0]
    return select_font_family(combo, chosen)


class SearchableComboFilter(QObject):
    """Allow seamless multi-character typing, search filtering and keyboard selection."""

    def __init__(self, combo: QComboBox, model: FontFamilyModel, filter_func, parent=None):
        super().__init__(parent or combo)
        self.combo = combo
        self.model = model
        self.filter_func = filter_func

    def eventFilter(self, watched, event):
        if event.type() == QEvent.FocusIn:
            line_edit = self.combo.lineEdit()
            if line_edit is not None and watched is line_edit:
                QTimer.singleShot(0, line_edit.selectAll)
        elif event.type() == QEvent.MouseButtonPress:
            line_edit = self.combo.lineEdit()
            if line_edit is not None and watched is line_edit:
                QTimer.singleShot(0, line_edit.selectAll)
        elif event.type() == QEvent.KeyPress:
            key = event.key()
            text = event.text()
            line_edit = self.combo.lineEdit()
            if watched in (self.combo.view(), self.combo.view().viewport()) and line_edit is not None:
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    idx = self.combo.view().currentIndex()
                    if idx.isValid() and 0 <= idx.row() < self.model.rowCount():
                        chosen = str(self.model.data(idx, Qt.DisplayRole) or "")
                        self.combo.hidePopup()
                        select_font_family(self.combo, chosen, emit=True)
                        return True
                elif key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End, Qt.Key_Tab, Qt.Key_Escape):
                    return False
                elif key == Qt.Key_Backspace:
                    line_edit.setFocus()
                    cur = line_edit.text()
                    if cur:
                        new_text = cur[:-1]
                        line_edit.setText(new_text)
                        self.filter_func(new_text)
                    return True
                elif text and text.isprintable():
                    line_edit.setFocus()
                    if line_edit.hasSelectedText():
                        new_text = text
                    else:
                        new_text = line_edit.text() + text
                    line_edit.setText(new_text)
                    line_edit.setCursorPosition(len(new_text))
                    self.filter_func(new_text)
                    if not self.combo.view().isVisible():
                        self.combo.showPopup()
                    return True
        return super().eventFilter(watched, event)


def configure_searchable_font_combo(combo: QComboBox, selected: str = "") -> None:
    """Drop-down with contains-search backed by a lightweight virtual model."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.setMaxVisibleItems(18)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(8)
    combo.setToolTip("Abre el menú o escribe parte del nombre para buscar entre todas las fuentes")
    if combo.lineEdit() is not None:
        combo.lineEdit().setPlaceholderText("Buscar fuente por nombre…")
        combo.lineEdit().setClearButtonEnabled(True)
    populate_font_combo(combo, selected)
    combo.view().setUniformItemSizes(True)
    combo.view().setMinimumWidth(260)
    combo.view().setMaximumWidth(340)
    combo.setCompleter(None)
    model = combo.model()
    line_edit = combo.lineEdit()
    scroll_bar = combo.view().verticalScrollBar()
    previous_scroll_handler = getattr(combo, "_font_scroll_handler", None)
    if previous_scroll_handler is not None:
        try:
            scroll_bar.valueChanged.disconnect(previous_scroll_handler)
        except (RuntimeError, TypeError):
            pass

    def load_more_fonts(value: int) -> None:
        if value >= scroll_bar.maximum() - 2:
            model.load_next_batch()

    combo._font_scroll_handler = load_more_fonts
    scroll_bar.valueChanged.connect(load_more_fonts)
    previous_handler = getattr(combo, "_font_filter_handler", None)
    if previous_handler is not None:
        try:
            line_edit.textEdited.disconnect(previous_handler)
        except (RuntimeError, TypeError):
            pass

    def filter_fonts(text: str) -> None:
        cursor = line_edit.cursorPosition()
        model.set_filter(text)
        combo.blockSignals(True)
        combo.setCurrentIndex(-1)
        combo.setEditText(text)
        line_edit.setCursorPosition(min(cursor, len(text)))
        combo.blockSignals(False)
        if model.rowCount() > 0:
            combo.view().setCurrentIndex(model.index(0, 0))

    def font_activated(_index: int) -> None:
        # Selecting a filtered result must leave the next drop-down opening
        # with the full catalogue, while retaining the chosen family.
        select_font_family(combo, combo.currentText())

    combo._font_filter_handler = filter_fonts
    line_edit.textEdited.connect(filter_fonts)
    previous_activated = getattr(combo, "_font_activated_handler", None)
    if previous_activated is not None:
        try:
            combo.activated.disconnect(previous_activated)
        except (RuntimeError, TypeError):
            pass
    combo._font_activated_handler = font_activated
    combo.activated.connect(font_activated)

    filter_obj = SearchableComboFilter(combo, model, filter_fonts, combo)
    combo._font_event_filter = filter_obj
    line_edit.installEventFilter(filter_obj)
    combo.view().installEventFilter(filter_obj)
    combo.view().viewport().installEventFilter(filter_obj)


def register_profile_fonts(manager) -> None:
    changed = False
    for project_type in manager.project_types():
        for project in manager.projects(project_type):
            for entry in manager.font_entries(project_type, project).values():
                path = manager.font_file(project_type, project, entry.get("file", ""))
                if not path:
                    continue
                key = str(path.resolve()).casefold()
                if key in _REGISTERED_FONT_FILES:
                    continue
                if QFontDatabase.addApplicationFont(str(path)) >= 0:
                    _REGISTERED_FONT_FILES.add(key)
                    changed = True
    if changed:
        invalidate_font_cache()
