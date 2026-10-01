from __future__ import annotations

import copy
import os
import subprocess
import sys
import uuid
from time import monotonic, perf_counter
from pathlib import Path

import numpy as np

from PySide6.QtCore import QFileSystemWatcher, QPoint, QThreadPool, QTimer, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog, QHBoxLayout, QMainWindow, QMessageBox, QProgressDialog, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from core.cleaning_process_manager import CleaningProcessManager
from core.app_updater import ReleaseUpdater, UpdateInfo, check_available_update
from core.app_version import APP_UPDATE_CHANNEL, APP_VERSION
from core.balloon_classifier import BALLOON_KINDS, classify_balloon
from core.credential_store import CredentialStore
from core.detection_manager import DetectionManager
from core.font_profile_manager import FontProfileManager
from core.history_manager import HistoryManager
from core.image_manager import ImageManager
from core.ocr_manager import OCRManager
from core.project_io import load_project_bundle, page_key, save_project_bundle
from core.project_manager import ProjectManager
from core.psd_sync import file_signature, inspect_stable_source, reconcile_layer_states
from core.performance_manager import (
    PerformanceTracker, configure_native_threads,
    resource_policy as build_resource_policy, warm_native_image_ops,
)
from core.recovery_manager import RecoveryManager
from core.retouch_layers import (
    consolidate_layer, layer_counts, normalized_layer_states, patch_history, patch_layer,
)
from core.settings_manager import SettingsManager
from core.stroke_engine import StrokeEngine
from core.text_transfer import (
    format_chapter_sections, format_numbered_entries,
    parse_chapter_sections, parse_numbered_entries,
)
from core.translation_manager import TranslationManager
from core.typography_manager import DEFAULT_STYLE, EFFECT_STYLE_KEYS, TypographyManager
from core.workers import ModelTask
from core.workflow_status import page_workflow_status
from core.watermark_manager import normalized_watermark, watermark_bytes
from core.chapter_watermarks import chapter_watermark_settings
from ui.ai_panel import AIOptionsPanel
from ui.canvas_view import CanvasShell
from ui.cleaning_quality_dialog import CleaningQualityDialog
from ui.effects_panel import BUILTIN_EFFECT_PRESETS, TextEffectsPanel
from ui.images_panel import ImagesPanel
from ui.i18n import UiTranslator
from ui.font_utils import configure_searchable_font_combo, register_profile_fonts
from ui.inspector_panel import InspectorPanel
from ui.layers_panel import LayersPanel
from ui.project_selection_dialog import ProjectSelectionDialog
from ui.sidebar import Sidebar
from ui.status_bar import StatusBar
from ui.script_panel import ScriptPanel
from ui.settings_dialog import SettingsDialog
from ui.sfx_panel import SFXPanel
from ui.text_panel import TextOptionsPanel
from ui.topbar import TopBar
from ui.workspace import EditorWorkspace
from ui.task_progress import TaskProgress
from ui.typography_dialog import TypographyDialog
from ui.watermark_dialog import WatermarkDialog
from ui.widgets.controls import ToastNotification


class MainWindow(QMainWindow):
    """Production UI orchestration for the local detection and cleaning models."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KuroPanel Studio")
        self.setMinimumSize(720, 500)
        self._size_for_current_screen()
        self.setAcceptDrops(True)
        self.project = ProjectManager()
        self.models_root = Path(__file__).parent.parent / "Models"
        app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "ManhuaSuiteEditor"
        settings_path = app_data / "settings.json"
        legacy_settings_path = Path(__file__).parent.parent / "api_configs.json"
        # A packaged build places Python sources below ``_internal``. Store
        # preferences in the user's application data so rebuilding, moving or
        # updating the program cannot erase them. Import the legacy file once.
        if not settings_path.exists() and legacy_settings_path.is_file():
            self.settings = SettingsManager(legacy_settings_path)
            self.settings.path = settings_path
            self.settings.save()
        else:
            self.settings = SettingsManager(settings_path)
        performance_settings = self.settings.data.get("performance", {})
        self.resource_policy = build_resource_policy(
            performance_settings.get("resource_profile", "auto"),
            int(performance_settings.get("image_cache_mb", 384)),
        )
        configure_native_threads(self.resource_policy)
        self.images = ImageManager(memory_limit_mb=self.resource_policy.image_cache_mb)
        if not settings_path.exists():
            self.settings.save()
        self.detector = DetectionManager(self.models_root)
        self.detector.set_resource_policy(self.resource_policy)
        self.detector.set_device_mode(str(performance_settings.get("device_mode", "auto")))
        self.ocr = OCRManager(cache_path=app_data / "Cache" / "ocr.sqlite3")
        self.ocr.set_resource_policy(self.resource_policy)
        self.translator = TranslationManager()
        self.watermark_asset_path = app_data / "Watermarks" / "active.png"
        self.credentials = CredentialStore(app_data / "credentials.dat")
        configured_profiles = self.settings.data.get("profiles", {})
        self.font_profiles = FontProfileManager(app_data / "Profiles")
        legacy_root_value = str(configured_profiles.get("root", "")).strip()
        self._legacy_profile_root = Path(legacy_root_value) if legacy_root_value else Path.home() / "Documents" / "Manhwas"
        self._legacy_import_task: ModelTask | None = None
        self._typography_resources_loaded = False
        self.active_font_type = str(configured_profiles.get("active_type", ""))
        self.active_font_profile = str(configured_profiles.get("active_project", ""))
        if not self.active_font_type and self.font_profiles.has_project("Manhwas", self.active_font_profile):
            self.active_font_type = "Manhwas"
        if "root" in configured_profiles:
            self.settings.update_profile(self.active_font_type, self.active_font_profile)
        self.recovery = RecoveryManager(
            app_data / "Recovery", int(self.settings.data.get("general", {}).get("backup_limit", 10))
        )
        self._migrate_legacy_credentials()
        self.cleaner = CleaningProcessManager(self.models_root)
        self.cleaner.set_resource_policy(self.resource_policy)
        self.cleaner.set_aggressiveness(self.settings.data["clean"].get("local_aggressiveness", 90))
        self.cleaner.set_device_mode(str(performance_settings.get("device_mode", "auto")))
        self.performance = PerformanceTracker()
        self.thread_pool = QThreadPool.globalInstance()
        # ONNX and PyTorch already parallelize internally. One foreground job
        # plus one preload avoids CPU/RAM contention from extra workers.
        self.thread_pool.setMaxThreadCount(self.resource_policy.foreground_workers)
        self.io_pool = QThreadPool(self)
        self.io_pool.setMaxThreadCount(self.resource_policy.io_workers)
        self.image_pool = QThreadPool(self)
        # A stale decode cannot be interrupted inside Qt's image plugin. A
        # second foreground slot lets the newly requested page start at once
        # instead of waiting behind the page the user already left.
        self.image_pool.setMaxThreadCount(2)
        self.prefetch_pool = QThreadPool(self)
        self.prefetch_pool.setMaxThreadCount(1)
        self.current_task: ModelTask | None = None
        self._warmup_task: ModelTask | None = None
        self._release_task: ModelTask | None = None
        self._update_check_task: ModelTask | None = None
        self._update_download_task: ModelTask | None = None
        self._update_progress_dialog: QProgressDialog | None = None
        self._staged_update: tuple[UpdateInfo, Path] | None = None
        self._active_operation_key = ""
        self._active_operation_name = ""
        self._models_ready = False
        self._folder_task: ModelTask | None = None
        self._image_task: ModelTask | None = None
        self._prefetch_task: ModelTask | None = None
        self._pending_clean_plan: dict | None = None
        # Identifies the latest asynchronous mask request.  Preparing a mask
        # may finish after the user has cancelled it or changed pages; without
        # a generation guard that stale result can paint the red overlay again
        # and make it look permanently attached to the canvas.
        self._clean_mask_request_id = 0
        self._quality_dialog: CleaningQualityDialog | None = None
        self._quality_review_entries: dict[str, list[dict]] = {}
        self._active_toasts: list[ToastNotification] = []
        self._page_load_token = 0
        self._page_metric_key = ""
        self._page_metric_name = ""
        self._folder_load_token = 0
        self._displayed_page_path: Path | None = None
        self._page_view_states: dict[str, dict] = {}
        self.regions: list[dict] = []
        self.page_regions: dict[str, list[dict]] = {}
        self.clean_results: dict[str, dict] = {}
        self.page_texts: dict[str, str] = {}
        self.page_styles: dict[str, dict] = {}
        self._page_failures: dict[str, dict[str, str]] = {}
        self._active_task_page_key: str | None = None
        self.style_presets: dict[str, dict] = {}
        self.effect_presets: dict[str, dict] = {
            name: TypographyManager.normalized(style)
            for name, style in BUILTIN_EFFECT_PRESETS.items()
        }
        self._effects_clipboard: dict | None = None
        stored_watermark = dict(self.settings.data.get("watermark", {}))
        try:
            stored_payload = self.watermark_asset_path.read_bytes() if self.watermark_asset_path.is_file() else b""
        except OSError:
            stored_payload = b""
        if stored_payload:
            stored_watermark["png_bytes"] = stored_payload
        self.watermark_settings = normalized_watermark(stored_watermark)
        self._persisted_watermark_payload = stored_payload
        self.watermark_dialog: WatermarkDialog | None = None
        self._watermark_previewing = False
        self._watermark_removed_explicitly = False
        self.project_file: Path | None = None
        self._dirty = False
        self._autosave_task: ModelTask | None = None
        history_limit = 40 if self.resource_policy.name == "low" else (60 if self.resource_policy.name == "balanced" else 80)
        self.history = HistoryManager(limit=history_limit)
        self._restoring_state = False
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(450)
        self._history_timer.timeout.connect(self._commit_debounced_state)
        self._watermark_persist_timer = QTimer(self)
        self._watermark_persist_timer.setSingleShot(True)
        self._watermark_persist_timer.setInterval(350)
        self._watermark_persist_timer.timeout.connect(self._persist_watermark_preferences)
        self._text_render_timer = QTimer(self)
        self._text_render_timer.setSingleShot(True)
        self._text_render_timer.setInterval(self.resource_policy.text_debounce_ms)
        self._text_render_timer.timeout.connect(self._render_active_text)
        self._pending_text_render_indices: set[int] = set()
        self._pending_text_render_all = False
        self._page_request_timer = QTimer(self)
        self._page_request_timer.setSingleShot(True)
        self._page_request_timer.setInterval(40)
        self._page_request_timer.timeout.connect(self._start_pending_page_load)
        self._psd_render_timer = QTimer(self)
        self._psd_render_timer.setSingleShot(True)
        self._psd_render_timer.setInterval(140)
        self._psd_render_timer.timeout.connect(self._render_active_psd)
        self._psd_auto_sync = bool(
            self.settings.data.get("general", {}).get("psd_auto_sync", True)
        )
        self._psd_status_text = (
            "Auto · guardar en Photoshop" if self._psd_auto_sync else "Sincronización pausada"
        )
        self._psd_watcher = QFileSystemWatcher(self)
        self._psd_watcher.fileChanged.connect(self._psd_watch_event)
        self._psd_watcher.directoryChanged.connect(self._psd_watch_event)
        self._psd_watch_debounce = QTimer(self)
        self._psd_watch_debounce.setSingleShot(True)
        self._psd_watch_debounce.setInterval(650)
        self._psd_watch_debounce.timeout.connect(self._scan_psd_changes)
        self._psd_poll_timer = QTimer(self)
        self._psd_poll_timer.setInterval(1800)
        self._psd_poll_timer.timeout.connect(self._scan_psd_changes)
        self._psd_poll_timer.start()
        self._psd_known_signatures: dict[Path, tuple[int, int] | None] = {}
        self._psd_pending_paths: set[Path] = set()
        self._psd_sync_task: ModelTask | None = None
        self._psd_sync_started = 0.0
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._configure_autosave_timer()
        self._model_idle_timer = QTimer(self)
        self._model_idle_timer.setInterval(30_000 if self.resource_policy.name == "low" else 60_000)
        self._model_idle_timer.timeout.connect(self._release_idle_gpu_models)
        self._model_idle_timer.start()
        self._memory_pressure_timer = QTimer(self)
        self._memory_pressure_timer.setInterval(15_000)
        self._memory_pressure_timer.timeout.connect(self._relieve_memory_pressure)
        self._memory_pressure_timer.start()
        self._performance_monitor_timer = QTimer(self)
        self._performance_monitor_timer.setInterval(self.resource_policy.status_interval_ms)
        self._performance_monitor_timer.timeout.connect(self._refresh_performance_monitor)
        self._performance_monitor_timer.start()
        self._pending_page_load = None
        self._screen_hooked = False
        self._load_styles()
        self._build_ui()
        self._build_actions()
        self._set_empty_canvas()
        app = QApplication.instance()
        if not hasattr(app, "_kuro_ui_translator"):
            app._kuro_ui_translator = UiTranslator(app)
        self.ui_translator = app._kuro_ui_translator
        self.ui_translator.set_language(self.settings.data["general"].get("ui_language", "es"))
        self.history.reset(self._snapshot())
        self._update_history_actions()
        self._update_provider_statuses()
        self.ui_translator.refresh(self)
        QTimer.singleShot(150, self._offer_recovery)
        QTimer.singleShot(700, self._import_legacy_profiles_async)
        if getattr(sys, "frozen", False) and self.settings.data["general"].get("auto_check_updates", True):
            QTimer.singleShot(3500, self._check_for_updates)
        # Only the high-performance profile preloads at startup. Model creation
        # happens in workers after the first frame, and LaMa is ready before the
        # user reaches cleaning. Low/balanced profiles remain lazy.
        if self.resource_policy.auto_warmup:
            QTimer.singleShot(1200, self._warm_up_models)

    def _migrate_legacy_credentials(self) -> None:
        changed = bool(getattr(self.settings, "needs_save", False))
        aliases = {"ChatGPT": "OpenAI"}
        for provider, value in self.settings.data.get("global_keys", {}).items():
            destination = aliases.get(provider, provider)
            if value and destination in {
                "Alibaba Cloud", "Gemini", "OpenAI", "DeepSeek", "DeepL",
            } and not self.credentials.configured(destination):
                self.credentials.set(destination, value)
            if value:
                self.settings.data["global_keys"][provider] = ""
                changed = True
        if self.settings.data["translate"].get("platform") not in {
            "Alibaba Cloud", "Gemini", "OpenAI", "DeepSeek", "DeepL",
        }:
            self.settings.data["translate"].update({"platform": "Gemini", "model": "gemini-2.5-flash"})
            changed = True
        if changed:
            self.settings.save()

    def _import_legacy_profiles_async(self) -> None:
        """Run the old folder migration after the first frame is responsive."""
        marker = self.font_profiles.root / ".legacy-import-complete"
        if self._legacy_import_task is not None or marker.exists():
            return

        def migrate(progress, cancelled):
            if cancelled():
                return []
            imported = self.font_profiles.import_legacy_root(self._legacy_profile_root, "Manhwas")
            marker.write_text("ok", encoding="utf-8")
            progress(100)
            return imported

        task = ModelTask(migrate)
        self._legacy_import_task = task
        task.signals.completed.connect(self._legacy_profiles_imported)
        task.signals.failed.connect(lambda _message: setattr(self, "_legacy_import_task", None))
        self.io_pool.start(task)

    def _legacy_profiles_imported(self, imported: list[str]) -> None:
        self._legacy_import_task = None
        if imported and not self.active_font_type and self.active_font_profile in imported:
            self.active_font_type = "Manhwas"
            self.settings.update_profile(self.active_font_type, self.active_font_profile)
        if imported:
            self._refresh_font_profiles()

    def _configure_autosave_timer(self) -> None:
        minutes = max(1, int(self.settings.data.get("general", {}).get("autosave_minutes", 2)))
        self._autosave_timer.start(minutes * 60_000)

    def _warm_up_models(self) -> None:
        if self._warmup_task is not None or self._models_ready:
            return
        self.ai_panel.set_status(
            "clean", "Calentando YOLO y LaMa sin bloquear la interfaz…", self.ai_panel.STATUS_WARNING
        )

        def warm_up(progress, cancelled):
            warm_native_image_ops()
            self.ocr.warm_up()
            progress(5)
            # LaMa has the larger cold-start cost. Pay it first in the isolated
            # process so the first cleaning never inherits session creation;
            # YOLO remains warm before this background task completes.
            clean_runtime = self.cleaner.warm_up(
                lambda value: progress(5 + int(value * 0.65)), cancelled,
            )
            if cancelled():
                return {}
            detection_runtime = self.detector.warm_up(
                lambda value: progress(70 + int(value * 0.30)), cancelled,
            )
            progress(100)
            return {"clean": clean_runtime, "detection": detection_runtime}

        task = ModelTask(warm_up)
        self._warmup_task = task
        task.signals.progress.connect(self._models_warmup_progress)
        task.signals.completed.connect(self._models_warmed)
        task.signals.failed.connect(self._models_warmup_failed)
        self.thread_pool.start(task)

    def _ensure_typography_resources(self) -> None:
        if self._typography_resources_loaded:
            return
        register_profile_fonts(self.font_profiles)
        self.text_panel.ensure_fonts_loaded()
        self._typography_resources_loaded = True

    def _models_warmup_progress(self, value: int) -> None:
        if value < 22:
            stage = "Cargando YOLO…"
        elif value < 35:
            stage = "Calentando YOLO en GPU…"
        elif value < 57:
            stage = "Cargando detector OCR de máscaras…"
        elif value < 79:
            stage = "Cargando red LaMa en proceso aislado…"
        else:
            stage = "Calentando LaMa en GPU…"
        self.ai_panel.set_status("clean", stage, self.ai_panel.STATUS_WARNING)

    def _models_warmed(self, runtimes) -> None:
        self._warmup_task = None
        self._models_ready = True
        clean_runtime = runtimes.get("clean", self.cleaner.runtime_label) if isinstance(runtimes, dict) else str(runtimes)
        detection_runtime = runtimes.get("detection", self.detector.runtime_label) if isinstance(runtimes, dict) else self.detector.runtime_label
        self.ai_panel.set_status(
            "clean", f"Modelos listos · {clean_runtime} · YOLO {detection_runtime}",
            self.ai_panel.STATUS_OK,
        )

    def _models_warmup_failed(self, _message: str) -> None:
        self._warmup_task = None
        self._models_ready = False
        self.ai_panel.set_status("clean", "LaMa se cargará al utilizarlo", self.ai_panel.STATUS_WARNING)

    def _load_styles(self) -> None:
        stylesheet = Path(__file__).parent.parent / "assets" / "styles" / "dark_theme.qss"
        self.setStyleSheet(stylesheet.read_text(encoding="utf-8"))

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.topbar = TopBar()
        root_layout.addWidget(self.topbar)
        self.task_progress = TaskProgress()
        self.task_progress.cancel_requested.connect(self._cancel_current_task)
        root_layout.addWidget(self.task_progress)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar()
        body.addWidget(self.sidebar)
        self.ai_panel = AIOptionsPanel()
        self.ai_panel.setMinimumWidth(280)
        self.ai_panel.set_configuration(self.settings.data)
        self.script_panel = ScriptPanel()
        self.text_panel = TextOptionsPanel()
        self.text_panel.set_presets(self.style_presets)
        self.effects_panel = TextEffectsPanel()
        self.effects_panel.set_presets(self.effect_presets)
        self.sfx_panel = SFXPanel()
        self._refresh_font_profiles()
        self.tool_panel = QStackedWidget()
        self.tool_panel.setMinimumWidth(280)
        self.tool_panel.setMaximumWidth(370)
        self.tool_panel.addWidget(self.ai_panel)
        self.tool_panel.addWidget(self.script_panel)
        self.tool_panel.addWidget(self.text_panel)
        self.tool_panel.addWidget(self.effects_panel)
        self.tool_panel.addWidget(self.sfx_panel)
        self.layers = LayersPanel(0)
        self.images_panel = ImagesPanel(self.project.pages, self.images)
        self.canvas_shell = CanvasShell()
        self.canvas_shell.canvas.set_resource_policy(self.resource_policy)
        self.inspector = InspectorPanel()
        self.inspector.setMinimumWidth(0)
        self.inspector.setMaximumWidth(16777215)
        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inspector_scroll.setObjectName("InspectorScroll")
        self.inspector_scroll.setMinimumWidth(270)
        self.inspector_scroll.setMaximumWidth(370)
        self.inspector_scroll.setWidget(self.inspector)
        workspace = EditorWorkspace(
            self.images_panel, self.layers, self.canvas_shell,
            self.tool_panel, self.inspector_scroll,
        )
        workspace.focus_changed.connect(self.topbar.set_focus_mode)
        workspace.dock_collapsed_changed.connect(self.canvas_shell.set_panel_collapsed)
        self.canvas_shell.panel_toggle_requested.connect(
            lambda: workspace.set_dock_collapsed(not workspace.dock_collapsed)
        )
        body.addWidget(workspace, 1)
        root_layout.addLayout(body, 1)
        self.status = StatusBar()
        self.task_progress.active_changed.connect(self.status.set_busy)
        self.status.set_refresh_interval(self.resource_policy.status_interval_ms)
        root_layout.addWidget(self.status)
        self.setCentralWidget(root)
        self.workspace = workspace
        self.sidebar.tool_changed.connect(self._sidebar_changed)
        self.workspace.dock.currentChanged.connect(self._sync_sidebar_from_dock)
        self.workspace.assets.currentChanged.connect(self._sync_sidebar_from_assets)
        self.tool_panel.currentChanged.connect(self._sync_sidebar_from_dock)
        self.sidebar.set_current("Procesar")
        self.ai_panel.action_requested.connect(self._handle_action)
        self.ai_panel.mode_changed.connect(self._ai_mode_changed)
        self.ai_panel.provider_changed.connect(self._save_provider_selection)
        self.ai_panel.style_options_changed.connect(self._style_changed)
        self.script_panel.region_selected.connect(self._script_region_selected)
        self.script_panel.text_edited.connect(self._script_text_edited)
        self.script_panel.translate_page_requested.connect(lambda: self.run_translation("page"))
        self.script_panel.replace_all_requested.connect(self._script_replace_all)
        self.script_panel.search_next_requested.connect(self._script_search_next)
        self.script_panel.translations_queued.connect(self._queue_script_translations)
        self.script_panel.queue_cleared.connect(self._clear_script_translations)
        self.text_panel.style_changed.connect(self._typography_live_changed)
        self.text_panel.fit_requested.connect(self._fit_selected_text_once)
        self.effects_panel.effects_changed.connect(self._effects_live_changed)
        self.sfx_panel.sfx_changed.connect(self._sfx_live_changed)
        self.effects_panel.preset_save_requested.connect(self._save_effect_preset)
        self.effects_panel.preset_apply_requested.connect(self._apply_effect_preset)
        self.effects_panel.copy_requested.connect(self._copy_effects)
        self.effects_panel.paste_requested.connect(self._paste_effects)
        self.text_panel.preset_save_requested.connect(self._save_typography_preset)
        self.text_panel.project_type_selected.connect(self._font_type_selected)
        self.text_panel.profile_selected.connect(self._font_profile_selected)
        self.text_panel.font_role_selected.connect(self._font_role_selected)
        self.text_panel.balloon_kind_selected.connect(self._balloon_kind_selected)
        self.inspector.action_requested.connect(self._handle_action)
        self.inspector.text_editor.textChanged.connect(self._editor_text_changed)
        self.inspector.text_editor.editing_finished.connect(self._finish_text_editing)
        self.topbar.action_requested.connect(self._handle_action)
        self.canvas_shell.welcome.action_requested.connect(self._handle_action)
        self.images_panel.page_selected.connect(self._set_page)
        self.canvas_shell.previous_button.clicked.connect(self.previous_page)
        self.canvas_shell.next_button.clicked.connect(self.next_page)
        self.inspector.previous_requested.connect(self.previous_page)
        self.inspector.next_requested.connect(self.next_page)
        self.inspector.zoom_requested.connect(self._adjust_zoom)
        self.canvas_shell.canvas.zoom_changed.connect(self.inspector.update_zoom)
        self.canvas_shell.canvas.performance_measured.connect(self._record_performance_metric)
        self.canvas_shell.canvas.regions_changed.connect(self._sync_regions)
        self.canvas_shell.canvas.region_edit_requested.connect(self._edit_region_text)
        self.canvas_shell.canvas.region_kind_requested.connect(self._balloon_kind_from_canvas)
        self.canvas_shell.canvas.region_hyphenation_requested.connect(self._hyphenation_from_canvas)
        self.canvas_shell.canvas.inline_text_committed.connect(self._canvas_inline_text_committed)
        self.canvas_shell.canvas.watermark_positions_changed.connect(self._watermark_positions_changed)
        self.canvas_shell.canvas.sfx_nodes_changed.connect(self._sfx_nodes_changed)
        self.canvas_shell.canvas.scene.selectionChanged.connect(self._canvas_selection_changed)
        self.layers.region_selected.connect(self._layer_selected)
        self.layers.detect_requested.connect(self.run_detection)
        self.layers.visibility_changed.connect(self._layer_visibility_changed)
        self.layers.lock_changed.connect(self._layer_lock_changed)
        self.layers.opacity_changed.connect(self._layer_opacity_changed)
        self.layers.rename_requested.connect(self._layer_rename_requested)
        self.layers.duplicate_requested.connect(self._layer_duplicate_requested)
        self.layers.delete_requested.connect(self._layer_delete_requested)
        self.layers.delete_many_requested.connect(self._layer_delete_many_requested)
        self.layers.move_requested.connect(self._layer_move_requested)
        self.layers.selection_changed.connect(self._layer_selection_changed)
        self.layers.batch_visibility_requested.connect(self._layer_batch_visibility_requested)
        self.layers.batch_lock_requested.connect(self._layer_batch_lock_requested)
        self.layers.image_visibility_changed.connect(self._image_layer_visibility_changed)
        self.layers.image_opacity_changed.connect(self._image_layer_opacity_changed)
        self.layers.retouch_visibility_changed.connect(self._retouch_layer_visibility_changed)
        self.layers.retouch_opacity_changed.connect(self._retouch_layer_opacity_changed)
        self.layers.retouch_lock_changed.connect(self._retouch_layer_lock_changed)
        self.layers.retouch_delete_requested.connect(self._retouch_layer_delete_requested)
        self.layers.retouch_merge_requested.connect(self._retouch_layer_merge_requested)
        self.layers.source_layer_visibility_changed.connect(self._source_layer_visibility_changed)
        self.layers.source_layer_opacity_changed.connect(self._source_layer_opacity_changed)
        self.layers.psd_sync_requested.connect(self.sync_active_psd)
        self.layers.psd_auto_sync_changed.connect(self._set_psd_auto_sync)
        self.canvas_shell.canvas.folder_dropped.connect(self.load_dropped_path)
        self.canvas_shell.canvas.retouch_committed.connect(self._commit_retouch_patch)
        self.canvas_shell.canvas.mask_stroke_committed.connect(self._add_mask_brush_stroke)
        self.canvas_shell.canvas.mask_preview_erase_committed.connect(self._erase_from_mask_preview)
        self.canvas_shell.canvas.color_picked.connect(self._retouch_color_picked)
        self.canvas_shell.canvas.brush_size_changed.connect(self._brush_size_changed)
        self.canvas_shell.canvas.brush_mode_toggle_requested.connect(self._toggle_brush_mode)
        self.canvas_shell.canvas.text_layout_status_changed.connect(self._text_layout_status_changed)
        self.images_panel.folder_dropped.connect(self.load_dropped_path)
        self._refresh_performance_monitor()

    def record_startup_time(self, seconds: float) -> None:
        """Receive the cold-start duration measured by the entry point."""
        self._record_performance_metric("Apertura", seconds)

    def _record_performance_metric(self, name: str, seconds: float) -> None:
        metric = self.performance.record(name, seconds)
        self.status.set_operation_time(metric.name, metric.seconds)
        self._refresh_performance_monitor()

    def _refresh_performance_monitor(self) -> None:
        if not hasattr(self, "status"):
            return
        self.status.set_performance_snapshot(
            self.performance.summary(), self.images.cache_stats(), self.resource_policy.name,
        )

    def _retain_profile_pages(self) -> int:
        """Keep only the decoded pages allowed by the active resource profile."""
        if not self.project.pages:
            return 0
        radius = max(0, int(self.resource_policy.page_cache_radius))
        active = self.project.active_index
        keep = {
            page.path
            for index, page in enumerate(self.project.pages)
            if page.path is not None and abs(index - active) <= radius
        }
        removed = self.images.retain_pages(keep)
        if removed:
            self._refresh_performance_monitor()
        return removed

    def _size_for_current_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1400, 820)
            return
        available = screen.availableGeometry()
        width = min(1600, max(self.minimumWidth(), available.width() - 32))
        height = min(900, max(self.minimumHeight(), available.height() - 56))
        self.resize(width, height)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.windowHandle() and not self._screen_hooked:
            self.windowHandle().screenChanged.connect(self._screen_changed)
            self._screen_hooked = True
        self._adapt_to_window()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "topbar"):
            self._adapt_to_window()
        if hasattr(self, "_active_toasts"):
            self._layout_toasts()

    def _screen_changed(self, screen) -> None:
        """Keep the window visible when it moves to a smaller monitor."""
        available = screen.availableGeometry()
        width = min(self.width(), max(self.minimumWidth(), available.width() - 24))
        height = min(self.height(), max(self.minimumHeight(), available.height() - 48))
        self.resize(width, height)
        self.move(max(available.left(), min(self.x(), available.right() - width)), max(available.top(), min(self.y(), available.bottom() - height)))

    def _adapt_to_window(self) -> None:
        self.topbar.adapt_to_width(self.width())
        if hasattr(self, "workspace"):
            self.workspace.adapt_to_width(self.width())
            self.canvas_shell.set_compact_controls(self.workspace.compact)

    def _build_actions(self) -> None:
        next_pending = QAction("Siguiente página pendiente", self)
        next_pending.setShortcut("Ctrl+Alt+N")
        next_pending.triggered.connect(self.images_panel.select_next_pending)
        self.addAction(next_pending)
        focus = QAction("Modo de enfoque", self)
        focus.setShortcut("Ctrl+Shift+F")
        focus.triggered.connect(lambda: self._handle_action("toggle_focus"))
        self.addAction(focus)
        open_folder = QAction("Abrir capítulo", self)
        open_folder.triggered.connect(self.open_chapter)
        open_folder.setShortcut("Ctrl+O")
        self.addAction(open_folder)
        save_project = QAction("Guardar proyecto", self)
        save_project.triggered.connect(self.save_project)
        save_project.setShortcut("Ctrl+S")
        self.addAction(save_project)
        self.undo_action = QAction("Deshacer", self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self.undo)
        self.addAction(self.undo_action)
        self.redo_action = QAction("Rehacer", self)
        self.redo_action.setShortcuts(["Ctrl+Y", "Ctrl+Shift+Z"])
        self.redo_action.triggered.connect(self.redo)
        self.addAction(self.redo_action)
        remove_retouch = QAction("Quitar último retoque", self)
        remove_retouch.setShortcut("Ctrl+Alt+Z")
        remove_retouch.triggered.connect(self.remove_last_retouch)
        self.addAction(remove_retouch)
        brush_p = QAction("Pincel de color", self)
        brush_p.setShortcut("Ctrl+P")
        brush_p.triggered.connect(lambda: self._toggle_brush_mode("paint"))
        self.addAction(brush_p)
        brush_s = QAction("Tampón de clonar", self)
        brush_s.triggered.connect(lambda: self._toggle_brush_mode("clone"))
        self.addAction(brush_s)
        brush_h = QAction("Pincel corrector puntual", self)
        brush_h.triggered.connect(lambda: self._toggle_brush_mode("heal"))
        self.addAction(brush_h)
        brush_r = QAction("Restaurar original", self)
        brush_r.triggered.connect(lambda: self._toggle_brush_mode("restore"))
        self.addAction(brush_r)
        brush_m = QAction("Pincel de máscara", self)
        brush_m.triggered.connect(lambda: self._toggle_brush_mode("mask"))
        self.addAction(brush_m)
        delete_region = QAction("Eliminar cuadro", self)
        delete_region.setShortcut("Delete")
        delete_region.triggered.connect(self._delete_active_region_action)
        self.addAction(delete_region)

    def _delete_active_region_action(self) -> None:
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return
        if hasattr(self, "canvas_shell"):
            self.canvas_shell.canvas.delete_selected_regions()

    def _sidebar_changed(self, tool: str) -> None:
        if tool in {"Páginas", "Capas"}:
            self.workspace.set_focus_mode(False)
            self.workspace.set_dock_collapsed(False)
            self.workspace.assets.setCurrentIndex(0 if tool == "Páginas" else 1)
            if self.workspace.compact:
                self.workspace.dock.setCurrentWidget(self.workspace.assets)
            self.sidebar.set_current(tool)
            return
        if tool == "Editar":
            self.workspace.set_focus_mode(False)
            self.workspace.set_dock_collapsed(False)
            self.workspace.dock.setCurrentWidget(self.inspector_scroll)
            self.canvas_shell.canvas.clear_sfx_nodes()
            self.sidebar.set_current(tool)
            return
        self.workspace.show_tools()
        if hasattr(self, "canvas_shell") and tool != "SFX":
            self.canvas_shell.canvas.clear_sfx_nodes()
        if tool == "Guión":
            self.tool_panel.setCurrentWidget(self.script_panel)
            self._sync_script_panel()
        elif tool == "Texto":
            self.tool_panel.setCurrentWidget(self.text_panel)
            self._ensure_typography_resources()
            self._refresh_font_profiles()
            self._layer_selected(self.layers.current_index())
        elif tool == "Procesar":
            self.tool_panel.setCurrentWidget(self.ai_panel)
        elif tool == "Efectos":
            self.tool_panel.setCurrentWidget(self.effects_panel)
            self._layer_selected(self.layers.current_index())
        elif tool == "SFX":
            self.tool_panel.setCurrentWidget(self.sfx_panel)
            self._layer_selected(self.layers.current_index())
        self.sidebar.set_current(tool)

    def _sync_sidebar_from_assets(self, _index: int) -> None:
        if not self.workspace.compact or self.workspace.dock.currentWidget() is self.workspace.assets:
            self.sidebar.set_current("Capas" if self.workspace.assets.currentIndex() else "Páginas")

    def _sync_sidebar_from_dock(self, _index: int) -> None:
        current = self.workspace.dock.currentWidget()
        if current is self.workspace.assets:
            self._sync_sidebar_from_assets(self.workspace.assets.currentIndex())
        elif current is self.inspector_scroll:
            self.canvas_shell.canvas.clear_sfx_nodes()
            self.sidebar.set_current("Editar")
        else:
            destination = {
                self.script_panel: "Guión", self.text_panel: "Texto",
                self.effects_panel: "Efectos", self.sfx_panel: "SFX",
            }.get(self.tool_panel.currentWidget(), "Procesar")
            self.sidebar.set_current(destination)

    def _handle_action(self, action: str) -> None:
        if action == "toggle_focus":
            self.workspace.set_focus_mode(not self.workspace.focus_mode)
        elif action in {"ocr", "run_ocr_api"}:
            self.run_ocr_api()
        elif action == "run_ocr_api_one":
            self.run_ocr_api_one()
        elif action == "reread_ocr_one":
            self.run_ocr_api_one(force=True)
        elif action == "run_ocr_api_all":
            self.run_ocr_api_all()
        elif action in {"detect", "run_yolo"}:
            self.run_detection()
        elif action in {"clean", "run_clean"}:
            self.clean_text()
        elif action == "run_clean_one":
            self.clean_selected_region()
        elif action == "run_clean_all":
            self.clean_chapter()
        elif action == "apply_clean_mask":
            self.apply_clean_mask_preview()
        elif action == "cancel_clean_mask":
            self.cancel_clean_mask_preview()
        elif action == "open_clean_quality":
            self.open_clean_quality()
        elif action == "mask_preview_erase_on":
            self._set_brush_mode("mask_erase")
        elif action == "mask_preview_erase_off":
            if self.canvas_shell.canvas.brush_mode == "mask_erase":
                self._set_brush_mode(None)
        elif action == "retouch_on":
            self._set_brush_mode("paint")
        elif action == "retouch_off":
            if self.canvas_shell.canvas.brush_mode == "paint":
                self._set_brush_mode(None)
        elif action == "clone_on":
            self._set_brush_mode("clone")
        elif action == "clone_off":
            if self.canvas_shell.canvas.brush_mode == "clone":
                self._set_brush_mode(None)
        elif action == "heal_on":
            self._set_brush_mode("heal")
        elif action == "heal_off":
            if self.canvas_shell.canvas.brush_mode == "heal":
                self._set_brush_mode(None)
        elif action == "restore_on":
            self._set_brush_mode("restore")
        elif action == "restore_off":
            if self.canvas_shell.canvas.brush_mode == "restore":
                self._set_brush_mode(None)
        elif action in {"mask", "mask_brush_on"}:
            self._set_brush_mode("mask")
        elif action == "mask_brush_off":
            if self.canvas_shell.canvas.brush_mode == "mask":
                self._set_brush_mode(None)
        elif action == "retouch_pick":
            self._set_brush_mode("paint")
            self.canvas_shell.canvas.request_color_pick()
            self._toast("Cuentagotas", "Haz clic sobre el color que deseas tomar.")
        elif action == "remove_last_retouch":
            self.remove_last_retouch()
        elif action == "remove_broken_retouches":
            self.remove_broken_retouches()
        elif action in {"translation", "run_translation"}:
            self.run_translation()
        elif action == "run_translation_one":
            self.run_translation("one")
        elif action == "run_translation_all":
            self.run_translation_chapter()
        elif action == "edit_typography":
            self.edit_typography()
        elif action == "settings":
            self.open_settings()
        elif action == "check_updates":
            self._check_for_updates(manual=True)
        elif action in {"switch_project", "change_project"}:
            self.switch_active_project()
        elif action == "watermark":
            self.open_watermark_editor()
        elif action == "open_folder":
            self.open_chapter()
        elif action == "open_psd":
            self.open_psd_files()
        elif action == "apply_text":
            self.apply_text()
        elif action == "clear_text":
            self.inspector.text_editor.clear()
        elif action == "copy_current":
            self.copy_page_text()
        elif action == "copy_all":
            self.copy_chapter_text()
        elif action == "copy_ai":
            self.copy_selected_text()
        elif action == "paste_ai":
            self.paste_distributed_text()
        elif action == "delete_last":
            self.canvas_shell.canvas.delete_last_region()
        elif action == "delete_all":
            self.canvas_shell.canvas.delete_all_regions()
        elif action == "delete_selected":
            self.canvas_shell.canvas.delete_selected_regions()
            self._toast("Selecciones eliminadas", "Se quitaron las regiones seleccionadas.")
        elif action == "save_current":
            self.save_current()
        elif action == "save_all":
            self.save_all()
        elif action == "save_project":
            self.save_project()
        elif action == "open_project":
            self.open_project()
        elif action == "undo":
            self.undo()
        elif action == "redo":
            self.redo()
        else:
            self._toast("Función sin backend", f"{action}: requiere un modelo o flujo adicional.")

    def _set_retouch_mode(self, enabled: bool) -> None:
        self._set_brush_mode("paint" if enabled else None)

    def _set_brush_mode(self, mode: str | None) -> None:
        layer_for_mode = {"paint": "paint", "clone": "paint", "heal": "paint", "restore": "restore"}.get(mode or "")
        if layer_for_mode and self.project.pages:
            if self._retouch_layer_state(self._active_key())[layer_for_mode].get("locked", False):
                self._toast("Capa bloqueada", "Desbloquea esa capa de retoque antes de pintar.")
                self.ai_panel.sync_brush_mode(None)
                return
        size = int(self.ai_panel.style_options("clean").get("thickness", 15))
        self.canvas_shell.canvas.set_brush_mode(mode, size)
        self.ai_panel.sync_brush_mode(mode)
        if mode == "paint":
            self._toast("Pincel de color · P", "Arrastra para pintar. Shift + mover ↔ cambia el grosor. Alt + clic toma color.")
        elif mode == "clone":
            self._toast("Tampón de clonar · S", "Alt + clic fija el origen de textura. Arrastra para clonar.")
        elif mode == "heal":
            self._toast("Corrector puntual · H", "Pinta sobre manchas o imperfecciones para fusionar la textura.")
        elif mode == "restore":
            self._toast("Restaurar original · R", "Pinta para recuperar exactamente los píxeles de la imagen original.")
        elif mode == "mask":
            self._toast("Pincel de máscara · M", "Pinta la máscara, revísala en rojo y pulsa Aplicar limpieza.")
        elif mode == "mask_erase":
            self._toast("Editor de máscara", "Pinta sobre las zonas rojas que LaMa no debe modificar.")

    def _toggle_brush_mode(self, mode: str) -> None:
        self._set_brush_mode(None if self.canvas_shell.canvas.brush_mode == mode else mode)

    def _brush_size_changed(self, size: int) -> None:
        self.ai_panel.set_brush_size(size)
        key = self._active_key()
        if key:
            clean_style = self.page_styles.setdefault(key, {}).setdefault("clean", {})
            clean_style["thickness"] = int(size)

    def _ai_mode_changed(self, mode: str) -> None:
        if mode != "clean" and self.canvas_shell.canvas.brush_mode is not None:
            self._set_brush_mode(None)
        if mode == "clean" and not self._models_ready and self._warmup_task is None:
            QTimer.singleShot(0, self._warm_up_models)

    def _retouch_color_picked(self, color) -> None:
        self.canvas_shell.canvas.set_brush_color(color)
        self.ai_panel.set_retouch_color(color)
        self._toast("Color tomado", color.name().upper())

    def _commit_retouch_patch(self, patch: dict) -> None:
        runtime = "Restauración original" if str(patch.get("kind", "")) == "restore" else "Pincel de color"
        self._append_manual_patch(patch, runtime)

    def _append_manual_patch(self, patch: dict, runtime: str) -> None:
        if not self.project.pages:
            return
        mask = patch.get("mask")
        if mask is not None and StrokeEngine.is_suspicious_connector(mask):
            self._toast(
                "Trazo descartado",
                "Se detectó un salto fuera del lienzo; no se modificó la imagen.",
                "warning",
            )
            return
        patch["id"] = uuid.uuid4().hex
        patch.setdefault("kind", "manual")
        key = self._active_key()
        if key is None:
            return
        logical_layer = patch_layer(patch)
        if self._retouch_layer_state(key)[logical_layer].get("locked", False):
            self._toast("Capa bloqueada", "Desbloquea la capa de retoque antes de modificarla.")
            return
        current = self.clean_results.get(key, {
            "patches": [], "targets": [], "runtime": runtime,
        })
        patches = [*current.get("patches", []), patch]
        self.clean_results[key] = {**current, "patches": patches, "runtime": runtime}
        self.canvas_shell.canvas.apply_cleaning([patch], replace=False)
        self._refresh_image_layers(key)
        self._record_history()

    def remove_last_retouch(self) -> None:
        """Remove the latest persisted manual patch without relying on history."""
        key = self._active_key()
        result = self.clean_results.get(key or "")
        if not key or not result:
            self._toast("Sin retoques", "Esta página no tiene parches manuales.")
            return
        patches = list(result.get("patches", []))
        index = next(
            (position for position in range(len(patches) - 1, -1, -1)
             if str(patches[position].get("kind", "legacy")) in {"manual", "legacy", "restore"}),
            None,
        )
        if index is None:
            self._toast("Sin retoques", "No hay un parche manual que eliminar.")
            return
        patches.pop(index)
        self.clean_results[key] = {**result, "patches": patches}
        self.canvas_shell.canvas.apply_cleaning(patches, replace=True)
        self._refresh_image_layers(key)
        self._record_history()
        self._toast("Retoque eliminado", "Se quitó el último parche manual de esta página.")

    def remove_broken_retouches(self) -> None:
        """Remove persisted connectors created by the old viewport-jump bug."""
        key = self._active_key()
        result = self.clean_results.get(key or "")
        if not key or not result:
            self._toast("Sin tiras", "Esta página no tiene retoques guardados.")
            return
        patches = list(result.get("patches", []))

        def broken(patch: dict) -> bool:
            if str(patch.get("kind", "legacy")) not in {"manual", "legacy", "restore"}:
                return False
            mask = patch.get("mask")
            return mask is not None and StrokeEngine.is_suspicious_connector(mask)

        retained = [patch for patch in patches if not broken(patch)]
        removed = len(patches) - len(retained)
        if not removed:
            self._toast("Sin tiras", "No se encontraron parches con el patrón defectuoso.")
            return
        self.clean_results[key] = {**result, "patches": retained}
        self.canvas_shell.canvas.apply_cleaning(retained, replace=True)
        self._refresh_image_layers(key)
        self._record_history()
        self._toast("Tiras eliminadas", f"Se retiraron {removed} parche(s) defectuoso(s).")

    def _add_mask_brush_stroke(self, stroke: dict) -> None:
        """Add one exact manual stroke to the editable LaMa mask preview."""
        if not self.project.pages:
            return
        key = self._active_key()
        if key is None:
            return
        mask = np.ascontiguousarray(stroke.get("mask"), dtype=np.uint8)
        if mask.ndim != 2 or not np.any(mask):
            return
        if StrokeEngine.is_suspicious_connector(mask):
            self._toast("Trazo descartado", "La máscara contenía un salto fuera del lienzo.", "warning")
            return
        x, y = int(stroke.get("x", 0)), int(stroke.get("y", 0))
        height, width = mask.shape
        page = self.project.active_page
        page_width, page_height = int(page.width), int(page.height)
        if x < 0 or y < 0 or x >= page_width or y >= page_height:
            return
        width = min(width, page_width - x)
        height = min(height, page_height - y)
        mask = np.ascontiguousarray(mask[:height, :width])
        if width <= 0 or height <= 0 or not np.any(mask):
            return

        pending = self._pending_clean_plan
        if not pending or pending.get("key") != key:
            plan = {
                "image_path": str(page.path),
                "entries": [],
                "targets": [],
                "cached": True,
                "provider": self.cleaner.provider_name,
                "runtime": self.cleaner.runtime_label,
                "masked_pixels": 0,
                "manual": True,
            }
            pending = {"key": key, "path": page.path, "plan": plan}
            self._pending_clean_plan = pending
        plan = pending["plan"]
        target = {"x": x, "y": y, "width": width, "height": height}
        plan.setdefault("entries", []).append({
            "x": x, "y": y, "mask": mask, "target": target,
        })
        plan.setdefault("targets", []).append(target)
        plan["manual"] = True
        plan["masked_pixels"] = sum(
            int(np.count_nonzero(entry["mask"])) for entry in plan["entries"]
        )
        self.canvas_shell.canvas.show_mask_preview(plan["entries"])
        self.ai_panel.set_mask_preview_active(True)
        self.ai_panel.set_status(
            "clean",
            f"Máscara manual lista · {plan['masked_pixels']:,} píxeles",
            self.ai_panel.STATUS_WARNING,
        )

    def _start_task(self, operation, title: str, message: str, finished=None, stage: str = "") -> None:
        if self.current_task:
            self._toast("Proceso en curso", "Espera a que termine la tarea actual.")
            return
        if stage:
            self.inspector.set_stage(stage)
        else:
            self.inspector.set_progress(0)
        task = ModelTask(operation)
        self.current_task = task
        self._active_operation_key = f"foreground:{id(task)}"
        self._active_operation_name = title
        self._active_task_page_key = None if "capítulo" in title.casefold() else self._active_key()
        self.performance.start(self._active_operation_key)
        self.task_progress.start(title, stage)
        task.signals.progress.connect(self.task_progress.update_progress)
        task.signals.stage.connect(self._task_stage)
        if stage:
            task.signals.progress.connect(
                lambda value, label=stage: self.inspector.set_stage(f"{label} {value}%", value)
            )
        else:
            task.signals.progress.connect(lambda value: self.inspector.set_progress(value))
        task.signals.completed.connect(lambda result: self._task_completed(result, title, message, finished))
        task.signals.failed.connect(self._task_failed)
        self.thread_pool.start(task)

    def _task_stage(self, stage: str, page_id: str) -> None:
        self.task_progress.set_stage(stage)
        if page_id:
            self._active_task_page_key = page_id

    def _cancel_current_task(self) -> None:
        if str(self.task_progress._owner or "").startswith("folder:") and self._folder_task is not None:
            self._folder_task.cancel()
        elif self.current_task is not None:
            self.current_task.cancel()

    def _release_idle_gpu_models(self) -> None:
        minutes = int(self.settings.data.get("performance", {}).get("gpu_idle_minutes", 5))
        if self.resource_policy.name == "low" and minutes > 0:
            minutes = min(minutes, self.resource_policy.model_idle_minutes)
        if minutes <= 0 or self.current_task or self._warmup_task or self._release_task:
            return
        cutoff = monotonic() - minutes * 60
        low_memory = self.resource_policy.name == "low"
        detector_idle = (
            self.detector.is_loaded
            and (low_memory or self.detector._device != "cpu")
            and self.detector.last_used < cutoff
        )
        cleaner_process = getattr(self.cleaner, "_process", None)
        cleaner_loaded = cleaner_process is not None and cleaner_process.is_alive()
        cleaner_idle = (
            cleaner_loaded
            and not self.project.pages
            and (low_memory or "CUDA" in self.cleaner.provider_name)
            and self.cleaner.last_used < cutoff
        )
        if not detector_idle and not cleaner_idle:
            return

        def release(_progress, _cancelled):
            released = False
            if detector_idle:
                released = self.detector.unload() or released
            if cleaner_idle:
                released = self.cleaner.unload() or released
            return released

        task = ModelTask(release)
        self._release_task = task
        task.signals.completed.connect(self._gpu_models_released)
        task.signals.failed.connect(lambda _message: setattr(self, "_release_task", None))
        self.thread_pool.start(task)

    def _relieve_memory_pressure(self) -> None:
        try:
            import psutil
            pressure = float(psutil.virtual_memory().percent)
        except ImportError:
            return
        if pressure < 88.0:
            return
        self.images.trim_memory()
        self.canvas_shell.canvas.trim_memory()
        self.ocr.trim_cache(32 if self.resource_policy.name == "low" else 96)
        if pressure < 93.0 or self.current_task or self._warmup_task or self._release_task:
            return

        def release_under_pressure(_progress, _cancelled):
            detector_released = self.detector.unload()
            cleaner_released = self.cleaner.unload()
            return detector_released or cleaner_released

        task = ModelTask(release_under_pressure)
        self._release_task = task
        task.signals.completed.connect(self._gpu_models_released)
        task.signals.failed.connect(lambda _message: setattr(self, "_release_task", None))
        self.thread_pool.start(task)

    def _gpu_models_released(self, released: bool) -> None:
        self._release_task = None
        if released:
            self._models_ready = False
            self.ai_panel.set_status(
                "clean", "Memoria de modelos liberada · las cachés siguen disponibles",
                self.ai_panel.STATUS_OK,
            )

    def _save_provider_selection(self, section: str, platform: str, model: str) -> None:
        self.settings.update_provider(section, platform, model)
        if section in {"ocr", "translate"}:
            self._update_provider_statuses()

    def _active_key(self) -> str | None:
        return page_key(self.project.active_page) if self.project.pages else None

    def _remember_displayed_page_view(self) -> None:
        """Remember zoom and viewport center independently for each page."""
        if not self.project.pages or self._displayed_page_path is None:
            return
        page = self.project.active_page
        if page.path != self._displayed_page_path:
            return
        self._page_view_states[page_key(page)] = self.canvas_shell.canvas.view_state()

    def _page_workflow_status(self, key: str) -> dict[str, bool | str]:
        status: dict[str, bool | str] = dict(
            page_workflow_status(self.page_regions.get(key, []), self.clean_results.get(key))
        )
        failures = self._page_failures.get(key, {})
        status["error"] = bool(failures)
        if failures:
            status["error_message"] = "\n".join(f"{step}: {message}" for step, message in failures.items())
        return status

    def _refresh_page_statuses(self) -> None:
        self.ai_panel.set_chapter_status(len(self.project.pages))
        self.images_panel.set_statuses([
            self._page_workflow_status(page_key(page)) for page in self.project.pages
        ])

    def _ordered_regions_for_key(self, key: str) -> list[dict]:
        manga = bool(
            self.page_styles.get(key, {}).get("translation", {}).get(
                "manga_mode",
                self.page_styles.get(key, {}).get("ocr", {}).get("manga_mode", False),
            )
        )
        return TypographyManager.ordered(self.page_regions.get(key, []), manga)

    def _pending_text_items(self, key: str, create: bool = True) -> list[dict]:
        page_style = self.page_styles.setdefault(key, {}) if create else self.page_styles.get(key, {})
        transfer = page_style.setdefault("text_transfer", {}) if create else page_style.get("text_transfer", {})
        pending = transfer.setdefault("pending", []) if create else transfer.get("pending", [])
        normalized = [
            item if isinstance(item, dict) else {"text": str(item), "target": "translation"}
            for item in pending
            if str(item.get("text", "") if isinstance(item, dict) else item).strip()
        ]
        if create and normalized != pending:
            transfer["pending"] = normalized
        return normalized

    @staticmethod
    def _assign_transferred_text(region: dict, item: dict) -> None:
        # Queue entries have already had their transfer numbering removed by
        # the parser. Keep numbers that are part of the actual dialogue.
        value = str(item.get("text", "")).strip()
        if not value:
            return
        if item.get("target") == "ocr":
            region["text"] = value
            region["ocr_completed"] = True
            region["translation"] = value
        else:
            region["translation"] = value
            region["translation_completed"] = True
        region["applied_text"] = value
        region["typeset_completed"] = bool(region.get("style"))

    def _consume_pending_text(self, key: str, regions: list[dict]) -> int:
        pending = self._pending_text_items(key)
        if not pending or not regions:
            return 0
        ordered = TypographyManager.ordered(
            regions,
            bool(self.page_styles.get(key, {}).get("translation", {}).get(
                "manga_mode", self.page_styles.get(key, {}).get("ocr", {}).get("manga_mode", False),
            )),
        )
        consumed = 0
        for region in ordered:
            if consumed >= len(pending):
                break
            item = pending[consumed]
            if item.get("target") == "ocr":
                occupied = str(region.get("text") or "").strip()
            else:
                occupied = str(region.get("translation") or "").strip()
                applied = str(region.get("applied_text") or "").strip()
                source = str(region.get("text") or "").strip()
                if applied and applied != source:
                    occupied = applied
            if occupied:
                continue
            self._assign_transferred_text(region, item)
            consumed += 1
        del pending[:consumed]
        self.page_styles[key].setdefault("text_transfer", {})["pending"] = pending
        if key == self._active_key() and hasattr(self, "script_panel"):
            self.script_panel.set_pending_translations(pending)
        return consumed

    def _numbered_page_content(self, key: str, target: str | None = None) -> str:
        target = target or ("translation" if self.ai_panel.current_mode == "translation" else "ocr")
        entries = []
        for fallback, region in enumerate(self._ordered_regions_for_key(key), 1):
            if target == "translation":
                value = region.get("translation") or region.get("applied_text") or region.get("text", "")
            else:
                value = region.get("text", "")
            entries.append((int(region.get("number", fallback)), value))
        text = format_numbered_entries(entries)
        if text:
            return text
        pending = self._pending_text_items(key, create=False)
        return format_numbered_entries(
            (index, item.get("text", "")) for index, item in enumerate(pending, 1)
        )

    def _refresh_page_text(self, key: str | None) -> None:
        if key:
            ordered = self._ordered_regions_for_key(key)
            numbered_texts = []
            for fallback_number, region in enumerate(ordered, start=1):
                text = str(region.get("translation") or region.get("text") or "").strip()
                if text:
                    number = int(region.get("number", fallback_number))
                    numbered_texts.append(f"#{number:02} {text}")
            pending = self._pending_text_items(key, create=False)
            self.page_texts[key] = "\n\n".join(numbered_texts) or format_numbered_entries(
                (index, item.get("text", "")) for index, item in enumerate(pending, 1)
            )
            if key == self._active_key():
                self.inspector.set_page_ocr(
                    self._numbered_page_content(key, "ocr"), len(pending),
                )

    def copy_page_text(self) -> None:
        key = self._active_key()
        if not key:
            self._toast("Sin página", "Abre un capítulo antes de copiar texto.")
            return
        content = self._numbered_page_content(key)
        if not content:
            self._toast("Sin texto", "La página todavía no contiene OCR ni texto pendiente.")
            return
        QGuiApplication.clipboard().setText(content)
        self._toast("Página copiada", f"{len(parse_numbered_entries(content))} caja(s) copiadas con su número.")

    def copy_chapter_text(self) -> None:
        sections = []
        for page in self.project.pages:
            key = page_key(page)
            content = self._numbered_page_content(key)
            if content:
                sections.append((page.name, content))
        content = format_chapter_sections(sections)
        if not content:
            self._toast("Sin texto", "El capítulo todavía no contiene OCR ni texto pendiente.")
            return
        QGuiApplication.clipboard().setText(content)
        self._toast("Capítulo copiado", f"Se copiaron {len(sections)} página(s) con numeración y encabezados.")

    def copy_selected_text(self) -> None:
        index = self.layers.current_index()
        if not 0 <= index < len(self.regions):
            self.copy_page_text()
            return
        region = self.regions[index]
        value = (
            region.get("translation") or region.get("applied_text") or region.get("text", "")
            if self.ai_panel.current_mode == "translation"
            else region.get("text", "")
        )
        value = str(value or "").strip()
        if not value:
            self._toast("Caja sin texto", "La caja seleccionada todavía no contiene texto.")
            return
        number = int(region.get("number", index + 1))
        QGuiApplication.clipboard().setText(format_numbered_entries([(number, value)]))
        self._toast("Caja copiada", f"Se copió el texto de la caja {number}.")

    def _apply_pasted_entries(self, key: str, entries: list[str], target: str) -> tuple[int, int]:
        items = [{"text": entry, "target": target} for entry in entries if str(entry).strip()]
        ordered = self._ordered_regions_for_key(key)
        assigned = min(len(ordered), len(items))
        for region, item in zip(ordered, items[:assigned]):
            self._assign_transferred_text(region, item)
        pending = items[assigned:]
        self.page_styles.setdefault(key, {}).setdefault("text_transfer", {})["pending"] = pending
        return assigned, len(pending)

    def _queue_script_translations(self, raw: str) -> None:
        key = self._active_key()
        if not key:
            self._toast("Sin página", "Abre un capítulo antes de preparar traducciones.")
            return
        entries = parse_numbered_entries(raw)
        if not entries:
            self._toast("Sin texto", "Escribe al menos una traducción.")
            return
        pending = [{"text": entry, "target": "translation"} for entry in entries]
        transfer = self.page_styles.setdefault(key, {}).setdefault("text_transfer", {})
        transfer["pending"] = pending
        transfer["source"] = raw
        self.script_panel.set_pending_translations(pending)
        self._refresh_page_text(key)
        self._record_history()
        self._toast("Cola preparada", f"{len(pending)} traducción(es) listas. Dibuja una caja por cada texto.")

    def _clear_script_translations(self) -> None:
        key = self._active_key()
        if not key:
            return
        transfer = self.page_styles.setdefault(key, {}).setdefault("text_transfer", {})
        transfer["pending"] = []
        transfer["source"] = ""
        self.script_panel.queue_input.clear()
        self.script_panel.set_pending_translations([])
        self._refresh_page_text(key)
        self._record_history()

    def paste_distributed_text(self) -> None:
        if not self.project.pages:
            self._toast("Sin capítulo", "Abre un capítulo antes de pegar texto.")
            return
        raw = QGuiApplication.clipboard().text().strip()
        if not raw:
            self._toast("Portapapeles vacío", "Copia primero el OCR o las traducciones.")
            return
        target = "ocr" if self.ai_panel.current_mode == "ocr" else "translation"
        chapter = parse_chapter_sections(raw)
        assigned = pending = matched = 0
        if chapter:
            by_name = {}
            for page in self.project.pages:
                by_name[page.name.casefold()] = page_key(page)
                by_name[Path(page.name).stem.casefold()] = page_key(page)
            for name, entries in chapter.items():
                key = by_name.get(name.casefold()) or by_name.get(Path(name).stem.casefold())
                if key:
                    used, queued = self._apply_pasted_entries(key, entries, target)
                    assigned += used
                    pending += queued
                    matched += 1
            if not matched:
                self._toast("Páginas no reconocidas", "Los encabezados no coinciden con las imágenes del capítulo.")
                return
        else:
            entries = parse_numbered_entries(raw)
            if not entries:
                self._toast("Sin texto", "No se encontraron líneas para distribuir.")
                return
            assigned, pending = self._apply_pasted_entries(self._active_key(), entries, target)
        key = self._active_key()
        self.regions = self.page_regions.get(key, [])
        self._refresh_page_text(key)
        self._render_page_layers(key)
        self._record_history()
        detail = f"{assigned} texto(s) colocados"
        if pending:
            detail += f" · {pending} pendientes se asignarán al dibujar nuevas cajas"
        self._toast("Texto distribuido", detail + ".")

    def _layer_selected(self, index: int) -> None:
        if self._restoring_state:
            return
        if not 0 <= index < len(self.regions):
            self.canvas_shell.canvas.clear_sfx_nodes()
            self.inspector.clear_layer_text()
            self.text_panel.clear_layer()
            self.effects_panel.clear_layer()
            self.sfx_panel.clear_layer()
            return
        region = self.regions[index]
        selected = self.layers.selected_indices() or [index]
        self.canvas_shell.canvas.select_regions(selected, index)
        final_text = str(region.get("translation") or region.get("applied_text") or region.get("text") or "")
        self.inspector.set_layer_text(
            index, str(region.get("text", "")), final_text,
            int(region.get("number", index + 1)),
        )
        page_default = self.page_styles.get(self._active_key() or "", {}).get("typography", DEFAULT_STYLE)
        self.text_panel.set_presets(self.style_presets)
        selected_style = {**page_default, **region.get("style", {}), "text_overflow": bool(region.get("text_overflow", False))}
        self.text_panel.set_layer(index, selected_style, str(region.get("style_preset", "")))
        self.text_panel.set_font_role(str(region.get("font_role", "")))
        self.text_panel.set_balloon_kind(
            str(region.get("balloon_kind", "dialogue")), bool(region.get("balloon_kind_manual", False)),
        )
        self.effects_panel.set_layer(
            index, {**page_default, **region.get("style", {})}, len(selected),
        )
        self.sfx_panel.set_layer(
            index, {**page_default, **region.get("style", {})}, len(selected),
        )
        if self.tool_panel.currentWidget() is self.sfx_panel:
            self.canvas_shell.canvas.show_sfx_nodes(index)
        preset_name = str(region.get("effect_preset", ""))
        if preset_name in self.effect_presets:
            self.effects_panel.preset.blockSignals(True)
            self.effects_panel.preset.setCurrentText(preset_name)
            self.effects_panel.preset.blockSignals(False)
        if hasattr(self, "script_panel"):
            rid = str(region.get("id", f"region-{index}"))
            self.script_panel.select_region(rid)
        if len(selected) > 1:
            self.text_panel.layer_label.setText(f"Editando {len(selected)} capas · los cambios de estilo se aplican en lote")

    def _sync_script_panel(self) -> None:
        if hasattr(self, "script_panel"):
            self.script_panel.set_regions(self.regions, self.current_page)
            key = self._active_key()
            source = str(self.page_styles.get(key or "", {}).get("text_transfer", {}).get("source", ""))
            if self.script_panel.queue_input.toPlainText() != source:
                self.script_panel.queue_input.setPlainText(source)
            self.script_panel.set_pending_translations(
                self._pending_text_items(key, create=False) if key else []
            )
            active_idx = self.layers.current_index()
            if 0 <= active_idx < len(self.regions):
                rid = str(self.regions[active_idx].get("id", f"region-{active_idx}"))
                self.script_panel.select_region(rid)

    def _script_region_selected(self, region_id: str) -> None:
        for idx, reg in enumerate(self.regions):
            if str(reg.get("id")) == str(region_id):
                self.layers.set_current_index(idx)
                self.canvas_shell.canvas.select_regions([idx], idx)
                self._layer_selected(idx)
                break

    def _script_text_edited(self, region_id: str, new_text: str) -> None:
        for idx, reg in enumerate(self.regions):
            if str(reg.get("id")) == str(region_id):
                reg["translation"] = str(new_text)
                reg["applied_text"] = str(new_text)
                reg["typeset_completed"] = bool(str(new_text).strip() and reg.get("style"))
                self._store_active_regions(self.regions)
                self._refresh_page_text(self._active_key())
                if self.layers.current_index() == idx:
                    self.inspector.set_layer_text(
                        idx, str(reg.get("text", "")), str(new_text),
                        int(reg.get("number", idx + 1)),
                    )
                self._schedule_text_render(immediate=True, indices=[idx])
                self._history_timer.start()
                break

    def _script_search_next(self, query: str, case_sensitive: bool, whole_word: bool, scope: str) -> None:
        if not query or not self.regions:
            return
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = rf"\b{re.escape(query)}\b" if whole_word else re.escape(query)
        curr = max(0, self.layers.current_index())
        total = len(self.regions)
        for offset in range(1, total + 1):
            idx = (curr + offset) % total
            reg = self.regions[idx]
            text = str(reg.get("translation") or reg.get("applied_text") or reg.get("text") or "")
            orig = str(reg.get("text") or "")
            if re.search(pattern, text, flags=flags) or re.search(pattern, orig, flags=flags):
                self.layers.set_current_index(idx)
                self.canvas_shell.canvas.select_regions([idx], idx)
                self._layer_selected(idx)
                self.script_panel.replace_feedback.setText(f"Encontrado en #{idx + 1:02d}")
                return
        self.script_panel.replace_feedback.setText("No se encontraron más coincidencias.")

    def _script_replace_all(self, find_str: str, replace_str: str, case_sensitive: bool, whole_word: bool, scope: str) -> None:
        if not find_str:
            return
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = rf"\b{re.escape(find_str)}\b" if whole_word else re.escape(find_str)
        total_replacements = 0
        if scope == "page" or not hasattr(self, "project") or not self.project.pages:
            for idx, reg in enumerate(self.regions):
                text = str(reg.get("translation") or reg.get("applied_text") or reg.get("text") or "")
                new_text, count = re.subn(pattern, replace_str, text, flags=flags)
                if count > 0:
                    total_replacements += count
                    reg["translation"] = new_text
                    reg["applied_text"] = new_text
            if total_replacements > 0:
                self._store_active_regions(self.regions)
                self._apply_layer_structure(self.layers.current_index())
                self._record_history()
        else:
            for page in self.project.pages:
                page_regions = page.get("regions", [])
                for reg in page_regions:
                    text = str(reg.get("translation") or reg.get("applied_text") or reg.get("text") or "")
                    new_text, count = re.subn(pattern, replace_str, text, flags=flags)
                    if count > 0:
                        total_replacements += count
                        reg["translation"] = new_text
                        reg["applied_text"] = new_text
            self.regions = copy.deepcopy(self.project.pages[self.current_page].get("regions", []))
            self._apply_layer_structure(self.layers.current_index())
            self._record_history()
        self.script_panel.replace_feedback.setText(f"{total_replacements} reemplazo(s) realizado(s).")
        self._sync_script_panel()

    def _edit_region_text(self, index: int) -> None:
        """Synchronize the selected layer without stealing canvas focus."""
        if not 0 <= index < len(self.regions):
            return
        self.layers.set_selected_indices([index], index)
        self._layer_selected(index)

    def _canvas_inline_text_committed(self, index: int, value: str) -> None:
        """Persist a direct canvas edit and rebuild only that text layer."""
        if self._restoring_state or not self.project.pages or not 0 <= index < len(self.regions):
            return
        region = self.regions[index]
        region["translation"] = str(value)
        region["applied_text"] = str(value)
        region["typeset_completed"] = bool(str(value).strip() and region.get("style"))
        self._store_active_regions(self.regions)
        self._refresh_page_text(self._active_key())
        if self.layers.current_index() == index:
            self.inspector.set_layer_text(
                index, str(region.get("text", "")), str(value),
                int(region.get("number", index + 1)),
            )
        self._schedule_text_render(immediate=True, indices=[index])
        self._history_timer.start()
        self._refresh_page_statuses()

    def _text_layout_status_changed(self, statuses: list[bool]) -> None:
        for index, overflow in enumerate(statuses):
            if index < len(self.regions):
                self.regions[index]["text_overflow"] = bool(overflow)
        self.layers.set_overflow_states(statuses)
        current = self.layers.current_index()
        if 0 <= current < len(statuses):
            self.text_panel.overflow_warning.setVisible(bool(statuses[current]))

    def _selected_layer_indices(self) -> list[int]:
        selected = self.layers.selected_indices()
        if selected:
            return [index for index in selected if 0 <= index < len(self.regions)]
        current = self.layers.current_index()
        return [current] if 0 <= current < len(self.regions) else []

    def _layer_selection_changed(self, indices: list[int]) -> None:
        if self._restoring_state:
            return
        self.canvas_shell.canvas.select_regions(indices, self.layers.current_index())

    def _layer_batch_visibility_requested(self, indices: list[int], visible: bool) -> None:
        valid = [index for index in indices if 0 <= index < len(self.regions)]
        for index in valid:
            self.regions[index]["visible"] = bool(visible)
            self.canvas_shell.canvas.set_region_state(index, visible=visible)
        if valid:
            self._store_active_regions(self.regions)
            self.layers.set_regions(self.regions)
            self.layers.set_selected_indices(valid, valid[-1])
            self._record_history()

    def _layer_batch_lock_requested(self, indices: list[int], locked: bool) -> None:
        valid = [index for index in indices if 0 <= index < len(self.regions)]
        for index in valid:
            self.regions[index]["locked"] = bool(locked)
            self.canvas_shell.canvas.set_region_state(index, locked=locked)
        if valid:
            self._store_active_regions(self.regions)
            self.layers.set_regions(self.regions)
            self.layers.set_selected_indices(valid, valid[-1])
            self._record_history()

    def _layer_visibility_changed(self, index: int, visible: bool) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        self.regions[index]["visible"] = bool(visible)
        self.canvas_shell.canvas.set_region_state(index, visible=visible)
        self._store_active_regions(self.regions)
        self._record_history()

    def _layer_lock_changed(self, index: int, locked: bool) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        self.regions[index]["locked"] = bool(locked)
        self.canvas_shell.canvas.set_region_state(index, locked=locked)
        self._store_active_regions(self.regions)
        self.layers._update_actions()
        self._record_history()

    def _layer_opacity_changed(self, index: int, opacity: int) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        targets = self._selected_layer_indices() or [index]
        for target in targets:
            self.regions[target]["opacity"] = max(0, min(100, int(opacity)))
            self.canvas_shell.canvas.set_region_state(target, opacity=opacity)
        self._store_active_regions(self.regions)
        self._history_timer.start()

    def _layer_rename_requested(self, index: int, name: str) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        self.regions[index]["name"] = name.strip()
        self._store_active_regions(self.regions)
        self.layers.set_regions(self.regions)
        self.layers.set_current_index(index)
        self._record_history()

    def _apply_layer_structure(self, selected_index: int | None, *, record: bool = True) -> None:
        """Synchronize structural layer changes without reloading the page image."""
        if selected_index is not None and self.regions:
            selected_index = min(max(0, selected_index), len(self.regions) - 1)
        else:
            selected_index = None
        selected_id = str(self.regions[selected_index].get("id", "")) if selected_index is not None else ""
        self._store_active_regions(self.regions)
        self.canvas_shell.canvas.set_regions(self.regions)
        self.canvas_shell.canvas.add_text(
            [region.get("applied_text") or region.get("translation") or region.get("text", "") for region in self.regions],
            self.regions,
        )
        self.layers.set_regions(
            self.regions, selected_id or None, keep_empty_selection=selected_index is None
        )
        if selected_index is not None:
            self.layers.set_current_index(selected_index)
            self.canvas_shell.canvas.select_regions([selected_index], selected_index)
        else:
            self._clear_text_region_selection()
            # Qt may deliver a delayed current-row update after the deleted
            # QListWidgetItem is destroyed. Settle it once more on the next
            # event-loop turn without changing zoom or viewport position.
            QTimer.singleShot(0, self._clear_text_region_selection)
        self._refresh_page_text(self._active_key())
        self._sync_script_panel()
        if record:
            self._record_history()

    def _clear_text_region_selection(self) -> None:
        self.canvas_shell.canvas.select_regions([], None)
        self.layers.set_selected_indices([], None)
        self.inspector.clear_layer_text()
        self.text_panel.clear_layer()
        self.effects_panel.clear_layer()
        self.sfx_panel.clear_layer()
        if hasattr(self, "script_panel"):
            self.script_panel.select_region("")

    def _layer_duplicate_requested(self, index: int) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        duplicate = copy.deepcopy(self.regions[index])
        duplicate["id"] = f"copy-{uuid.uuid4().hex}"
        duplicate["name"] = f"{duplicate.get('name') or f'Capa {index + 1}'} copia"
        duplicate["x"] = int(duplicate.get("x", 0)) + 12
        duplicate["y"] = int(duplicate.get("y", 0)) + 12
        duplicate["locked"] = False
        duplicate["visible"] = True
        self.regions.insert(index + 1, duplicate)
        self._apply_layer_structure(index + 1)

    def _layer_delete_requested(self, index: int) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        if self.regions[index].get("locked", False):
            self._toast("Capa bloqueada", "Desbloquea la capa antes de eliminarla.")
            return
        self.regions.pop(index)
        self._apply_layer_structure(None)

    def _layer_delete_many_requested(self, indices: list[int]) -> None:
        valid = sorted({
            index for index in indices
            if 0 <= index < len(self.regions) and not self.regions[index].get("locked", False)
        }, reverse=True)
        if not valid:
            return
        for index in valid:
            self.regions.pop(index)
        self._apply_layer_structure(None)

    def _layer_move_requested(self, source: int, target: int) -> None:
        if self._restoring_state or not (0 <= source < len(self.regions) and 0 <= target < len(self.regions)):
            return
        region = self.regions.pop(source)
        self.regions.insert(target, region)
        self._apply_layer_structure(target)

    def _image_layer_state(self, key: str | None) -> dict:
        if not key:
            return {}
        layers = self.page_styles.setdefault(key, {}).setdefault("layers", {})
        layers.setdefault("original", {"visible": True, "opacity": 100})
        layers.setdefault("clean", {"visible": True, "opacity": 100})
        return layers

    def _retouch_layer_state(self, key: str | None) -> dict[str, dict]:
        if not key:
            return normalized_layer_states(None)
        stored = self.page_styles.setdefault(key, {}).setdefault("retouch_layers", {})
        normalized = normalized_layer_states(stored)
        stored.clear()
        stored.update(normalized)
        return stored

    def _source_layer_states(self, key: str | None) -> dict[str, dict]:
        if not key:
            return {}
        return self.page_styles.setdefault(key, {}).setdefault("source_layers", {})

    def _refresh_image_layers(self, key: str | None) -> None:
        state = self._image_layer_state(key)
        patches = list(self.clean_results.get(key or "", {}).get("patches", []))
        clean_available = bool(patches)
        retouch_states = self._retouch_layer_state(key)
        counts = layer_counts(patches)
        history = patch_history(patches)
        attention = sum(
            patch_layer(patch) == "automatic" and str(patch.get("qc_status", "")) == "attention"
            for patch in patches
        )
        self.ai_panel.set_quality_available(bool(counts.get("automatic", 0)), int(attention))
        self.canvas_shell.compare_button.setEnabled(clean_available)
        if not clean_available and self.canvas_shell.compare_button.isChecked():
            self.canvas_shell.compare_button.setChecked(False)
        self.layers.set_image_layers(
            bool(self.project.pages), clean_available, state, counts, retouch_states, history,
        )
        source_layers = self.project.active_page.source_layers if self.project.pages else []
        self.layers.set_source_layers(source_layers, self._source_layer_states(key))
        self.layers.set_psd_sync_state(
            available=bool(source_layers), auto=self._psd_auto_sync,
            text=self._psd_status_text,
            syncing=self._psd_sync_task is not None,
        )
        for layer_name in ("original", "clean"):
            layer_state = state.get(layer_name, {})
            self.canvas_shell.canvas.set_image_layer_state(
                layer_name,
                visible=bool(layer_state.get("visible", True)),
                opacity=int(layer_state.get("opacity", 100)),
            )
        for layer_name, layer_state in retouch_states.items():
            self.canvas_shell.canvas.set_retouch_layer_state(
                layer_name,
                visible=bool(layer_state.get("visible", True)),
                opacity=int(layer_state.get("opacity", 100)),
            )

    def _image_layer_visibility_changed(self, layer: str, visible: bool) -> None:
        if self._restoring_state or not self.project.pages:
            return
        key = self._active_key()
        state = self._image_layer_state(key)
        state.setdefault(layer, {})["visible"] = bool(visible)
        self.canvas_shell.canvas.set_image_layer_state(layer, visible=visible)
        self._record_history()

    def _image_layer_opacity_changed(self, layer: str, opacity: int) -> None:
        if self._restoring_state or not self.project.pages:
            return
        key = self._active_key()
        state = self._image_layer_state(key)
        state.setdefault(layer, {})["opacity"] = max(0, min(100, int(opacity)))
        self.canvas_shell.canvas.set_image_layer_state(layer, opacity=opacity)
        self._history_timer.start()

    def _retouch_layer_visibility_changed(self, layer: str, visible: bool) -> None:
        if self._restoring_state or not self.project.pages:
            return
        state = self._retouch_layer_state(self._active_key())
        if layer not in state:
            return
        state[layer]["visible"] = bool(visible)
        self.canvas_shell.canvas.set_retouch_layer_state(layer, visible=visible)
        self._record_history()

    def _retouch_layer_opacity_changed(self, layer: str, opacity: int) -> None:
        if self._restoring_state or not self.project.pages:
            return
        state = self._retouch_layer_state(self._active_key())
        if layer not in state:
            return
        state[layer]["opacity"] = max(0, min(100, int(opacity)))
        self.canvas_shell.canvas.set_retouch_layer_state(layer, opacity=opacity)
        self._history_timer.start()

    def _retouch_layer_lock_changed(self, layer: str, locked: bool) -> None:
        if self._restoring_state or not self.project.pages:
            return
        state = self._retouch_layer_state(self._active_key())
        if layer not in state:
            return
        state[layer]["locked"] = bool(locked)
        self._record_history()

    def _retouch_layer_delete_requested(self, layer: str) -> None:
        key = self._active_key()
        result = self.clean_results.get(key or "")
        state = self._retouch_layer_state(key)
        if not key or not result or layer not in state:
            return
        if state[layer].get("locked", False):
            self._toast("Capa bloqueada", "Desbloquea la capa antes de eliminarla.")
            return
        patches = [patch for patch in result.get("patches", []) if patch_layer(patch) != layer]
        self.clean_results[key] = {**result, "patches": patches}
        self.canvas_shell.canvas.apply_cleaning(patches, replace=True)
        self._refresh_image_layers(key)
        self._record_history()
        self._toast("Capa eliminada", "Se retiraron sus parches sin modificar la imagen original.")

    def _retouch_layer_merge_requested(self, layer: str) -> None:
        key = self._active_key()
        result = self.clean_results.get(key or "")
        state = self._retouch_layer_state(key)
        if not key or not result or layer not in state:
            return
        if state[layer].get("locked", False):
            self._toast("Capa bloqueada", "Desbloquea la capa antes de consolidarla.")
            return
        patches, merged = consolidate_layer(list(result.get("patches", [])), layer)
        if merged < 2:
            self._toast("Nada que consolidar", "Esta capa necesita al menos dos parches.")
            return
        patches[next(index for index, patch in enumerate(patches) if patch_layer(patch) == layer)].setdefault("id", uuid.uuid4().hex)
        self.clean_results[key] = {**result, "patches": patches}
        self.canvas_shell.canvas.apply_cleaning(patches, replace=True)
        self._refresh_image_layers(key)
        self._record_history()
        self._toast("Capa consolidada", f"{merged} parches se combinaron en uno solo.")

    def _source_layer_visibility_changed(self, layer_id: str, visible: bool) -> None:
        self._update_source_layer(layer_id, "visible", bool(visible))

    def _source_layer_opacity_changed(self, layer_id: str, opacity: int) -> None:
        self._update_source_layer(layer_id, "opacity", max(0, min(100, int(opacity))))

    def _update_source_layer(self, layer_id: str, field: str, value) -> None:
        if self._restoring_state or not self.project.pages or not self.project.active_page.source_layers:
            return
        key = self._active_key()
        self._source_layer_states(key).setdefault(str(layer_id), {})[field] = value
        # PSD composition can be expensive, particularly with effects. Reuse
        # the existing background image worker and never block the Qt thread.
        self._displayed_page_path = None
        self._psd_render_timer.start()
        self._history_timer.start()

    def _render_active_psd(self) -> None:
        if self.project.pages and self.project.active_page.source_layers:
            self.images.invalidate(self.project.active_page.path)
            self._load_page_image(
                self.project.active_page, self._active_key(), preserve_view=True,
            )

    def _psd_pages(self) -> list:
        return [
            page for page in self.project.pages
            if page.path is not None and page.path.suffix.lower() in {".psd", ".psb"}
        ]

    def _watched_source_pages(self) -> list:
        """Pages whose source can be overwritten by Photoshop in place."""
        supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".psd", ".psb"}
        return [
            page for page in self.project.pages
            if page.path is not None and page.path.suffix.casefold() in supported
        ]

    def _configure_psd_watcher(self, reset_signatures: bool = True) -> None:
        watched = [*self._psd_watcher.files(), *self._psd_watcher.directories()]
        if watched:
            self._psd_watcher.removePaths(watched)
        pages = self._watched_source_pages()
        paths = [Path(page.path).resolve() for page in pages if page.path]
        directories = sorted({str(path.parent) for path in paths if path.parent.is_dir()})
        files = [str(path) for path in paths if path.is_file()]
        if directories:
            self._psd_watcher.addPaths(directories)
        if files:
            self._psd_watcher.addPaths(files)
        if reset_signatures:
            self._psd_known_signatures = {
                path: file_signature(path) for path in paths
            }
        else:
            current_paths = set(paths)
            self._psd_known_signatures = {
                path: signature for path, signature in self._psd_known_signatures.items()
                if path in current_paths
            }
            for path in paths:
                self._psd_known_signatures.setdefault(path, file_signature(path))
        available = bool(self._psd_pages())
        if not self._psd_sync_task:
            self._psd_status_text = (
                "Auto · guardar en Photoshop" if self._psd_auto_sync
                else "Sincronización pausada"
            )
        if hasattr(self, "layers"):
            self.layers.set_psd_sync_state(
                available=available, auto=self._psd_auto_sync,
                text=self._psd_status_text,
                syncing=self._psd_sync_task is not None,
            )

    def _ensure_psd_watch_paths(self) -> None:
        """Re-add files after Photoshop replaces them atomically on save."""
        watched_files = {Path(value).resolve() for value in self._psd_watcher.files()}
        missing = [
            str(Path(page.path).resolve()) for page in self._watched_source_pages()
            if page.path and Path(page.path).is_file()
            and Path(page.path).resolve() not in watched_files
        ]
        if missing:
            self._psd_watcher.addPaths(missing)

    def _psd_watch_event(self, _changed_path: str) -> None:
        self._ensure_psd_watch_paths()
        if self._psd_auto_sync:
            self._psd_watch_debounce.start()

    def _set_psd_auto_sync(self, enabled: bool) -> None:
        self._psd_auto_sync = bool(enabled)
        self._psd_status_text = (
            "Auto · guardar en Photoshop" if self._psd_auto_sync else "Sincronización pausada"
        )
        self.settings.update_psd_auto_sync(self._psd_auto_sync)
        self.layers.set_psd_sync_state(
            available=bool(self._psd_pages()), auto=self._psd_auto_sync,
            text=self._psd_status_text,
            syncing=self._psd_sync_task is not None,
        )
        if self._psd_auto_sync:
            self._scan_psd_changes()

    def _scan_psd_changes(self, force: bool = False) -> None:
        if not force and not self._psd_auto_sync:
            return
        self._ensure_psd_watch_paths()
        pages = self._watched_source_pages()
        active_path = (
            Path(self.project.active_page.path).resolve()
            if self.project.pages and self.project.active_page.path
            else None
        )
        changed: list[Path] = []
        for page in pages:
            path = Path(page.path).resolve()
            signature = file_signature(path)
            if signature is None:
                continue
            if force:
                if path == active_path:
                    changed.append(path)
            elif signature != self._psd_known_signatures.get(path):
                changed.append(path)
        if not changed:
            if force:
                self._toast("PSD actualizado", "El archivo activo ya coincide con Photoshop.")
            return
        if self._psd_sync_task is not None:
            self._psd_pending_paths.update(changed)
            return
        self._start_psd_sync(changed, manual=force)

    def sync_active_psd(self) -> None:
        if not self.project.pages or not self.project.active_page.source_layers:
            self._toast("Sin PSD activo", "Selecciona una página PSD o PSB para sincronizarla.")
            return
        self._scan_psd_changes(force=True)

    def _start_psd_sync(self, paths: list[Path], manual: bool = False) -> None:
        unique_paths = list(dict.fromkeys(Path(path).resolve() for path in paths))
        if not unique_paths:
            return
        self._psd_sync_started = monotonic()
        self._psd_status_text = "Esperando guardado estable…"
        self.layers.set_psd_sync_state(
            available=True, auto=self._psd_auto_sync,
            text=self._psd_status_text, syncing=True,
        )

        def inspect_changed(progress, cancelled):
            results, errors = [], []
            for index, path in enumerate(unique_paths, start=1):
                if cancelled():
                    break
                try:
                    results.append(inspect_stable_source(path))
                except Exception as error:
                    errors.append(f"{path.name}: {error}")
                progress(int(index * 100 / max(1, len(unique_paths))))
            return {"results": results, "errors": errors, "manual": manual}

        task = ModelTask(inspect_changed)
        self._psd_sync_task = task
        task.signals.completed.connect(self._psd_sync_ready)
        task.signals.failed.connect(self._psd_sync_failed)
        self.io_pool.start(task)

    def _psd_sync_ready(self, payload: dict) -> None:
        self._psd_sync_task = None
        results = list(payload.get("results", []))
        errors = list(payload.get("errors", []))
        active_changed = False
        for result in results:
            path = Path(result["path"]).resolve()
            page = next(
                (candidate for candidate in self.project.pages
                 if candidate.path and Path(candidate.path).resolve() == path),
                None,
            )
            if page is None:
                continue
            key = page_key(page)
            updated_layers = result.get("layers")
            if updated_layers is not None:
                states = self._source_layer_states(key)
                reconciled = reconcile_layer_states(
                    page.source_layers, updated_layers, states,
                )
                self.page_styles.setdefault(key, {})["source_layers"] = reconciled
                page.source_layers = list(updated_layers)
            page.width = int(result["width"])
            page.height = int(result["height"])
            page.size_label = str(result["size_label"])
            self._psd_known_signatures[path] = result.get("signature")
            self.images.invalidate(path)
            active_changed = bool(
                self.project.pages and self.project.active_page.path
                and Path(self.project.active_page.path).resolve() == path
            ) or active_changed

        self._configure_psd_watcher(reset_signatures=False)
        if active_changed:
            self._displayed_page_path = None
            self._load_page_image(
                self.project.active_page, self._active_key(), preserve_view=True,
            )
        if results:
            self._dirty = True
            elapsed = monotonic() - self._psd_sync_started
            self._psd_status_text = f"Sincronizado · {elapsed:.1f} s"
            self.layers.set_psd_sync_state(
                available=True, auto=self._psd_auto_sync,
                text=self._psd_status_text, syncing=False,
            )
            self._toast(
                "Imagen sincronizada",
                f"{len(results)} archivo(s) actualizado(s) sin mover cajas, textos ni zoom.",
            )
        if errors:
            if payload.get("manual"):
                self._toast("PSD pendiente", " · ".join(errors))
            else:
                self._psd_status_text = "Photoshop sigue guardando · reintentando"
                self.layers.set_psd_sync_state(
                    available=True, auto=self._psd_auto_sync,
                    text=self._psd_status_text, syncing=False,
                )
        if self._psd_pending_paths:
            pending = list(self._psd_pending_paths)
            self._psd_pending_paths.clear()
            self._start_psd_sync(pending)

    def _psd_sync_failed(self, message: str) -> None:
        self._psd_sync_task = None
        self._psd_status_text = "No se pudo sincronizar"
        self.layers.set_psd_sync_state(
            available=bool(self._psd_pages()), auto=self._psd_auto_sync,
            text=self._psd_status_text, syncing=False,
        )
        self._toast("No se pudo sincronizar el PSD", message)

    def _refresh_font_profiles(self) -> None:
        project_types = self.font_profiles.project_types()
        if self.active_font_type not in project_types:
            self.active_font_type = ""
            self.active_font_profile = ""
        projects = self.font_profiles.projects(self.active_font_type) if self.active_font_type else []
        if self.active_font_profile not in projects:
            self.active_font_profile = ""
        entries = self.font_profiles.font_entries(
            self.active_font_type, self.active_font_profile,
        ) if self.active_font_profile else {}
        self.text_panel.set_profiles(
            project_types, self.active_font_type, projects, self.active_font_profile,
            bool(self.project.pages and self.active_font_type and self.active_font_profile), entries,
        )
        translation_profile = (
            self.font_profiles.translation_profile(self.active_font_type, self.active_font_profile)
            if self.font_profiles.has_project(self.active_font_type, self.active_font_profile)
            else {"glossary": []}
        )
        self.ai_panel.set_translation_context(
            self.active_font_profile, len(translation_profile.get("glossary", [])),
        )

    def _dialogue_font_defaults(self) -> tuple[str, str, str, dict] | None:
        matched = self.font_profiles.dialogue_font(
            self.active_font_type, self.active_font_profile,
        )
        if matched is None:
            return None
        alias, entry = matched
        family = str(entry.get("family", "")).strip()
        if not family:
            return None
        path = self.font_profiles.font_file(
            self.active_font_type, self.active_font_profile, str(entry.get("file", "")),
        )
        return alias, family, str(path) if path else "", dict(entry.get("style", {}))

    def _kind_font_defaults(self, kind: str) -> tuple[str, str, str, dict]:
        matched = self.font_profiles.balloon_kind_font(
            self.active_font_type, self.active_font_profile, kind,
        )
        if matched is not None:
            alias, entry = matched
            family = str(entry.get("family", "")).strip()
            if family:
                path = self.font_profiles.font_file(
                    self.active_font_type, self.active_font_profile, str(entry.get("file", "")),
                )
                return alias, family, str(path) if path else "", dict(entry.get("style", {}))
        fallback = {
            "dialogue": ("Segoe UI", 400),
            "shout": ("Arial", 900),
            "caption": ("Segoe UI", 700),
        }
        family, weight = fallback.get(kind, fallback["dialogue"])
        return "", family, "", {"font_weight": weight}

    def _set_kind_font(self, region: dict, kind: str, *, force: bool = False) -> None:
        style = region.get("style")
        style = dict(style) if isinstance(style, dict) else {}
        if not force and (str(region.get("font_role", "")).strip() or
                          str(style.get("font_family", "")).strip()) and not region.get("balloon_font_from_kind"):
            return
        alias, family, font_file, role_style = self._kind_font_defaults(kind)
        style.update({"font_family": family, "font_file": font_file})
        for name in ("font_weight", "font_size", "italic"):
            if name in role_style:
                style[name] = role_style[name]
        if force or region.get("balloon_font_from_kind") or style.get("balloon_shape", "auto") in {"", "auto"}:
            style["balloon_shape"] = {
                "dialogue": "ellipse", "shout": "diamond", "caption": "rectangle",
            }[kind]
        region["style"] = TypographyManager.normalized(style)
        if alias:
            region["font_role"] = alias
        else:
            region.pop("font_role", None)
        region["balloon_font_from_kind"] = True

    def _apply_dialogue_defaults(self, regions: list[dict], key: str | None = None) -> list[dict]:
        """Classify visible new boxes; keep manual font and category choices."""
        if not regions:
            return regions
        key = key or self._active_key()
        canvas = self.canvas_shell.canvas
        image = canvas._pixmap_item.pixmap().toImage()
        may_detect = bool(
            key == self._active_key() and self.project.pages
            and self._displayed_page_path == self.project.active_page.path
            and not image.isNull()
        )
        pending = [region for region in regions if not region.get("balloon_kind_manual")
                   and region.get("balloon_kind_source") != "detected"]
        rgb = canvas._qimage_rgb_array(image) if may_detect and pending else None
        for region in regions:
            if region.get("balloon_kind_manual"):
                continue
            previous = str(region.get("balloon_kind", ""))
            if rgb is not None and region.get("balloon_kind_source") != "detected":
                box = tuple(int(region.get(name, 0)) for name in ("x", "y", "width", "height"))
                detected = classify_balloon(rgb, box)
                region["balloon_kind"] = detected or "dialogue"
                region["balloon_kind_source"] = "detected"
            elif previous not in BALLOON_KINDS:
                region["balloon_kind"] = "dialogue"
                region["balloon_kind_source"] = "default"
            if previous != region["balloon_kind"] or not region.get("balloon_font_from_kind"):
                self._set_kind_font(region, region["balloon_kind"])
        return regions

    def _balloon_kind_from_canvas(self, region_id: str, kind: str) -> None:
        index = next(
            (index for index, region in enumerate(self.regions)
             if str(region.get("id", "")) == region_id), -1,
        )
        if index < 0:
            return
        self.layers.set_selected_indices([index], index)
        self.canvas_shell.canvas.select_regions([index], index)
        self._balloon_kind_selected(kind, indices=[index])

    def _hyphenation_from_canvas(self, region_id: str, enabled: bool) -> None:
        index = next(
            (index for index, region in enumerate(self.regions)
             if str(region.get("id", "")) == region_id), -1,
        )
        if index < 0:
            return
        self.regions[index].setdefault("style", {})["hyphenation"] = bool(enabled)
        self._store_active_regions(self.regions)
        self.layers.set_selected_indices([index], index)
        self.canvas_shell.canvas.select_regions([index], index)
        self._schedule_text_render(indices=[index])
        self._layer_selected(index)
        self._record_history()

    def _balloon_kind_selected(self, kind: str, indices: list[int] | None = None) -> None:
        if self._restoring_state:
            return
        indices = self._selected_layer_indices() if indices is None else indices
        if not indices:
            return
        for index in indices:
            region = self.regions[index]
            if kind == "auto":
                region["balloon_kind_manual"] = False
                region["balloon_kind_source"] = ""
                self._apply_dialogue_defaults([region])
                resolved = str(region.get("balloon_kind", "dialogue"))
            else:
                resolved = kind if kind in BALLOON_KINDS else "dialogue"
                region["balloon_kind"] = resolved
                region["balloon_kind_manual"] = True
                region["balloon_kind_source"] = "manual"
                self._set_kind_font(region, resolved, force=True)
            region["typeset_completed"] = bool(region.get("applied_text"))
        self._store_active_regions(self.regions)
        self._schedule_text_render(indices=indices)
        self._layer_selected(indices[-1])
        self._record_history()

    @staticmethod
    def _enable_adaptive_typesetting(regions: list[dict]) -> None:
        """Fit OCR and translated dialogue to the detected balloon interior.

        SFX layers keep their independent path/perspective layout. A selected
        font size remains the maximum; long dialogue can shrink to fit while
        subsequent box edits continue to respect the balloon's safe area.
        """
        for region in regions:
            original = region.get("style", {})
            explicit = original if isinstance(original, dict) else {}
            style = TypographyManager.normalized(explicit)
            if not style.get("sfx_enabled", False):
                # Normalized defaults already enable adaptive dialogue. Keep
                # an existing manual choice when OCR or translation reruns.
                if "auto_fit" not in explicit:
                    style["auto_fit"] = True
                if "balloon_fit" not in explicit:
                    style["balloon_fit"] = True
            region["style"] = style

    def _install_project_typography_defaults(self) -> None:
        """Migrate unstyled pages/boxes to the active project's Dialogue role."""
        defaults = self._dialogue_font_defaults()
        for page in self.project.pages:
            key = page_key(page)
            if defaults is not None:
                _alias, family, font_file, role_style = defaults
                page_style = self.page_styles.setdefault(key, {})
                typography = page_style.setdefault("typography", {})
                if not str(typography.get("font_family", "")).strip():
                    typography.update({
                        **role_style,
                        "font_family": family,
                        "font_file": font_file,
                    })
            self._apply_dialogue_defaults(self.page_regions.get(key, []), key=key)

    def _font_type_selected(self, project_type: str) -> None:
        self.active_font_type = project_type
        available = self.font_profiles.projects(project_type)
        self.active_font_profile = available[0] if available else ""
        self.settings.update_profile(self.active_font_type, self.active_font_profile)
        register_profile_fonts(self.font_profiles)
        self._refresh_font_profiles()
        if self.active_font_profile:
            self._toast("Biblioteca cambiada", f"Biblioteca activa: {project_type} · Proyecto: {self.active_font_profile}")
        else:
            self._toast("Biblioteca cambiada", f"Biblioteca activa: {project_type}")

    def _font_profile_selected(self, project_type: str, project: str) -> None:
        self.active_font_type = project_type
        self.active_font_profile = project
        self.settings.update_profile(project_type, project)
        register_profile_fonts(self.font_profiles)
        self._refresh_font_profiles()
        self._toast("Proyecto asignado", f"Proyecto activo: {project} ({project_type}) asignado al capítulo.")

    def _font_role_selected(
        self, alias: str, family: str, file_name: str, role_style: dict | None = None,
    ) -> None:
        if self._restoring_state:
            return
        indices = self._selected_layer_indices()
        if indices:
            font_path = self.font_profiles.font_file(self.active_font_type, self.active_font_profile, file_name)
            for index in indices:
                self.regions[index]["font_role"] = alias
                self.regions[index]["balloon_font_from_kind"] = False
                style = self.regions[index].setdefault("style", {})
                style.update(dict(role_style or {}))
                style["font_family"] = family
                style["font_file"] = str(font_path) if font_path else ""
                self.regions[index]["style"] = TypographyManager.normalized(style)
                self.regions[index]["typeset_completed"] = bool(self.regions[index].get("applied_text"))
            self._store_active_regions(self.regions)
            self._schedule_text_render(indices=indices)
            self._history_timer.start()

    def _typography_live_changed(self, style: dict) -> None:
        if self._restoring_state:
            return
        indices = self._selected_layer_indices()
        if not indices:
            return
        selected_preset = self.text_panel.preset.currentText()
        for index in indices:
            current = TypographyManager.normalized(self.regions[index].get("style", {}))
            updated = TypographyManager.normalized(style)
            for key in EFFECT_STYLE_KEYS:
                updated[key] = current[key]
            if any(current.get(name) != updated.get(name) for name in (
                "font_family", "font_file", "font_weight", "font_size", "italic",
            )):
                self.regions[index]["balloon_font_from_kind"] = False
            self.regions[index]["style"] = updated
            self.regions[index]["typeset_completed"] = bool(self.regions[index].get("applied_text"))
            if selected_preset in self.style_presets:
                self.regions[index]["style_preset"] = selected_preset
            else:
                self.regions[index].pop("style_preset", None)
        self._store_active_regions(self.regions)
        self._schedule_text_render(indices=indices)
        self._history_timer.start()

    def _fit_selected_text_once(self) -> None:
        """Fit selected layers once, then keep their resulting size manual."""
        if self._restoring_state:
            return
        indices = self._selected_layer_indices()
        if not indices:
            return
        for index in indices:
            style = TypographyManager.normalized(self.regions[index].get("style", {}))
            style["fit_once"] = True
            style["auto_fit"] = False
            self.regions[index]["style"] = style
            self.regions[index].pop("layout_snapshot", None)
        # Rendering is synchronous here; CanvasView writes back the selected
        # point size and removes the transient fit_once marker.
        self._schedule_text_render(immediate=True, indices=indices)
        self._store_active_regions(self.regions)
        current = self.layers.current_index()
        if 0 <= current < len(self.regions):
            page_default = self.page_styles.get(self._active_key() or "", {}).get("typography", DEFAULT_STYLE)
            self.text_panel.set_layer(current, {**page_default, **self.regions[current].get("style", {})})
        self._history_timer.start()

    def _effects_live_changed(self, effects: dict) -> None:
        if self._restoring_state:
            return
        indices = self._selected_layer_indices()
        if not indices:
            return
        for index in indices:
            current = TypographyManager.normalized(self.regions[index].get("style", {}))
            current.update(effects)
            self.regions[index]["style"] = TypographyManager.normalized(current)
            self.regions[index].pop("effect_preset", None)
            self.regions[index]["typeset_completed"] = bool(self.regions[index].get("applied_text"))
        self._store_active_regions(self.regions)
        self._schedule_text_render(indices=indices)
        self._history_timer.start()

    def _sfx_live_changed(self, values: dict) -> None:
        if self._restoring_state:
            return
        indices = self._selected_layer_indices()
        if not indices:
            return
        for index in indices:
            current = TypographyManager.normalized(self.regions[index].get("style", {}))
            current.update(values)
            self.regions[index]["style"] = TypographyManager.normalized(current)
            self.regions[index]["typeset_completed"] = bool(self.regions[index].get("applied_text"))
        self._store_active_regions(self.regions)
        if not bool(values.get("sfx_enabled", False)):
            self.canvas_shell.canvas.clear_sfx_nodes()
        elif self.tool_panel.currentWidget() is self.sfx_panel:
            self.canvas_shell.canvas.show_sfx_nodes(self.layers.current_index())
        self._schedule_text_render(indices=indices)
        self._history_timer.start()

    def _sfx_nodes_changed(self, index: int, values: dict) -> None:
        if self._restoring_state or not 0 <= index < len(self.regions):
            return
        current = TypographyManager.normalized(self.regions[index].get("style", {}))
        current.update(values)
        self.regions[index]["style"] = TypographyManager.normalized(current)
        self._store_active_regions(self.regions)
        if self.layers.current_index() == index:
            page_default = self.page_styles.get(self._active_key() or "", {}).get("typography", DEFAULT_STYLE)
            self.sfx_panel.set_layer(index, {**page_default, **self.regions[index]["style"]})
        # CanvasView already committed the high-quality SFX render on mouse
        # release. Scheduling it again caused a visible double redraw.
        self._history_timer.start()

    def _schedule_text_render(self, immediate: bool = False, indices: list[int] | None = None) -> None:
        if indices is None:
            self._pending_text_render_all = True
            self._pending_text_render_indices.clear()
        elif not self._pending_text_render_all:
            self._pending_text_render_indices.update(int(index) for index in indices)
        if immediate:
            self._text_render_timer.stop()
            self._render_active_text()
        else:
            self._text_render_timer.start(8)

    def _render_active_text(self) -> None:
        if not self.project.pages or self._restoring_state:
            return
        lines = [region.get("applied_text", region.get("text", "")) for region in self.regions]
        if self._pending_text_render_all or not self._pending_text_render_indices:
            self.canvas_shell.canvas.add_text(lines, self.regions)
        else:
            self.canvas_shell.canvas.update_text_layers(
                list(self._pending_text_render_indices), lines, self.regions,
            )
        self._pending_text_render_all = False
        self._pending_text_render_indices.clear()

    @staticmethod
    def _effect_preset_keys() -> tuple[str, ...]:
        return (*EFFECT_STYLE_KEYS, "font_family", "font_file", "font_size", "font_weight", "italic")

    def _save_effect_preset(self, name: str) -> None:
        indices = self._selected_layer_indices()
        if not indices:
            self._toast("Selecciona una capa", "El preset debe partir de una capa de texto.")
            return
        current = TypographyManager.normalized(self.regions[indices[-1]].get("style", {}))
        preset = {key: current[key] for key in self._effect_preset_keys() if key in current}
        self.effect_presets[name] = TypographyManager.normalized(preset)
        self.effects_panel.set_presets(self.effect_presets)
        self.effects_panel.preset.blockSignals(True)
        self.effects_panel.preset.setCurrentText(name)
        self.effects_panel.preset.blockSignals(False)
        self._apply_effect_preset(name, self.effect_presets[name])
        self._toast("Preset guardado", f"{name} conserva fuente, trazos, degradado, resplandor y sombra.")

    def _apply_effect_preset(self, name: str, preset: dict) -> None:
        if self._restoring_state:
            return
        indices = self._selected_layer_indices()
        if not indices:
            return
        normalized = TypographyManager.normalized(preset)
        for index in indices:
            current = TypographyManager.normalized(self.regions[index].get("style", {}))
            for key in self._effect_preset_keys():
                if key in normalized:
                    current[key] = normalized[key]
            self.regions[index]["style"] = TypographyManager.normalized(current)
            self.regions[index]["effect_preset"] = name
            self.regions[index]["typeset_completed"] = bool(self.regions[index].get("applied_text"))
        self._store_active_regions(self.regions)
        self.canvas_shell.canvas.add_text([r.get("applied_text", "") for r in self.regions], self.regions)
        self._record_history()

    def _copy_effects(self) -> None:
        indices = self._selected_layer_indices()
        if not indices:
            return
        style = TypographyManager.normalized(self.regions[indices[-1]].get("style", {}))
        self._effects_clipboard = {key: style[key] for key in EFFECT_STYLE_KEYS}
        QGuiApplication.clipboard().setText("MSE_EFFECTS:" + repr(self._effects_clipboard))
        self._toast("Efectos copiados", "Puedes pegarlos en una o varias capas seleccionadas.")

    def _paste_effects(self) -> None:
        indices = self._selected_layer_indices()
        if not indices or not self._effects_clipboard:
            self._toast("Sin efectos copiados", "Copia primero los efectos de una capa.")
            return
        for index in indices:
            current = TypographyManager.normalized(self.regions[index].get("style", {}))
            current.update(self._effects_clipboard)
            self.regions[index]["style"] = TypographyManager.normalized(current)
            self.regions[index].pop("effect_preset", None)
        self._store_active_regions(self.regions)
        self.canvas_shell.canvas.add_text([r.get("applied_text", "") for r in self.regions], self.regions)
        self._record_history()
        self._toast("Efectos pegados", f"Se actualizaron {len(indices)} capa(s).")

    def _save_typography_preset(self, name: str, style: dict) -> None:
        indices = self._selected_layer_indices()
        preset = TypographyManager.normalized(style)
        if indices:
            current = TypographyManager.normalized(self.regions[indices[-1]].get("style", {}))
            for key in EFFECT_STYLE_KEYS:
                preset[key] = current[key]
        self.style_presets[name] = preset
        for index in indices:
            self.regions[index]["style"] = dict(self.style_presets[name])
            self.regions[index]["style_preset"] = name
            self.regions[index]["typeset_completed"] = bool(self.regions[index].get("applied_text"))
        if indices:
            self._store_active_regions(self.regions)
        self.text_panel.set_presets(self.style_presets)
        self.text_panel.preset.setCurrentText(name)
        self._record_history()
        self._toast("Estilo guardado", f"{name} ya puede reutilizarse en cualquier capa del capítulo.")

    def _canvas_selection_changed(self) -> None:
        if self._restoring_state:
            return
        selected = []
        for index, item in enumerate(self.canvas_shell.canvas._regions):
            try:
                if item.isSelected():
                    selected.append(index)
            except RuntimeError:
                # Qt can deliver a final selectionChanged while the scene is
                # tearing down an item. Ignore that stale wrapper.
                continue
        if selected:
            current = selected[-1]
            self.layers.set_selected_indices(selected, current)

    def _update_provider_statuses(self) -> None:
        provider, model = self.ai_panel.ocr_configuration()
        endpoint = self.settings.data["ocr"].get("base_url", "")
        signature = (provider, model.strip(), endpoint)
        key = self.credentials.get("Alibaba Cloud") or os.environ.get("DASHSCOPE_API_KEY", "")
        try:
            OCRManager.normalize_endpoint(endpoint)
            endpoint_error = ""
        except ValueError as error:
            endpoint_error = str(error)
        if provider != "Alibaba Cloud":
            ocr_reason = f"Proveedor OCR no disponible: {provider}"
        elif not str(key).strip():
            ocr_reason = "Agrega la API key de Alibaba Cloud en Configuración"
        elif not model.strip():
            ocr_reason = "Escribe un modelo OCR en Configuración"
        elif endpoint_error:
            ocr_reason = endpoint_error
        elif signature == getattr(self, "_ocr_rejected_config", None):
            ocr_reason = "Modelo o región no disponible (404). Revisa OCR en Configuración"
        else:
            ocr_reason = ""
        ocr_ready = not ocr_reason
        self.ai_panel.set_status(
            "ocr", "Clave y URL listas · modelo sin verificar" if ocr_ready else ocr_reason,
            self.ai_panel.STATUS_NEUTRAL if ocr_ready else self.ai_panel.STATUS_WARNING,
        )
        self.ai_panel.set_ready("ocr", ocr_ready, ocr_reason)
        provider = self.settings.data["translate"].get("platform", "Gemini")
        translation_ready = self.credentials.configured(provider) and bool(self.ai_panel.translate_model.currentText().strip())
        self.ai_panel.set_status(
            "translation", f"{provider} configurado" if translation_ready else f"Configura {provider} y su modelo",
            self.ai_panel.STATUS_OK if translation_ready else self.ai_panel.STATUS_WARNING,
        )
        self.ai_panel.set_ready("translation", translation_ready, f"Configura {provider} y su modelo")

    def switch_active_project(self) -> None:
        """Open the project & profile chooser anytime to switch library/project."""
        project_types = self.font_profiles.project_types()
        if not any(self.font_profiles.projects(pt) for pt in project_types):
            self._toast("Sin proyectos", "Abre Ajustes para crear tu primer proyecto de traducción.")
            return
        selector = ProjectSelectionDialog(
            self.font_profiles, self.active_font_type, self.active_font_profile, self,
        )
        if selector.exec() != QDialog.Accepted:
            return
        selected = selector.selected_project()
        if selected is None:
            return
        self.active_font_type, self.active_font_profile = selected
        self.settings.update_profile(self.active_font_type, self.active_font_profile)
        register_profile_fonts(self.font_profiles)
        self._refresh_font_profiles()
        self._toast(
            "Proyecto cambiado",
            f"Se asignó '{self.active_font_profile}' ({self.active_font_type}) al espacio de trabajo actual.",
        )

    def _check_for_updates(self, manual: bool = False) -> None:
        if not getattr(sys, "frozen", False):
            if manual:
                self._toast("Actualizaciones", "Instala la aplicación de Windows para recibir actualizaciones automáticas.")
            return
        if self._staged_update is not None:
            self._offer_update(self._staged_update[0])
            return
        if self._update_check_task is not None or self._update_download_task is not None:
            return
        task = ModelTask(
            lambda _progress, _cancelled: check_available_update(APP_VERSION, APP_UPDATE_CHANNEL)
        )
        self._update_check_task = task
        task.signals.completed.connect(lambda info: self._update_check_finished(info, manual))
        task.signals.failed.connect(lambda message: self._update_check_failed(message, manual))
        self.io_pool.start(task)

    def _update_check_finished(self, info: UpdateInfo | None, manual: bool) -> None:
        self._update_check_task = None
        if info is not None:
            self._offer_update(info)
        elif manual:
            self._toast("Sin actualizaciones", f"Ya tienes la versión {APP_VERSION}.")

    def _update_check_failed(self, message: str, manual: bool) -> None:
        self._update_check_task = None
        if manual:
            self._toast("No se pudo buscar actualizaciones", message, "error")

    def _offer_update(self, info: UpdateInfo) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Actualización disponible")
        dialog.setTextFormat(Qt.PlainText)
        dialog.setText(f"KuroPanel Studio {info.version} está disponible.\nVersión actual: {APP_VERSION}.")
        dialog.setInformativeText(
            "El instalador local ya está verificado. Se abrirá al cerrar la aplicación."
            if info.local_path else
            "Se descargará y verificará el instalador antes de cerrar la aplicación."
        )
        install_button = dialog.addButton("Actualizar ahora", QMessageBox.AcceptRole)
        dialog.addButton("Más tarde", QMessageBox.RejectRole)
        self.ui_translator.refresh(dialog)
        dialog.exec()
        if dialog.clickedButton() is not install_button:
            return
        if self.current_task is not None:
            self._toast("Proceso en curso", "Espera a que termine antes de actualizar.")
            return
        if info.local_path is not None:
            self._install_update(info.local_path)
        elif self._staged_update is not None and self._staged_update[0] == info:
            self._install_update(self._staged_update[1])
        else:
            self._download_update(info)

    def _download_update(self, info: UpdateInfo) -> None:
        destination = self.settings.path.parent / "Updates" / info.filename
        dialog = QProgressDialog("Descargando actualización…", "Cancelar", 0, 100, self)
        dialog.setWindowTitle("Actualizando KuroPanel Studio")
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        self._update_progress_dialog = dialog
        task = ModelTask(lambda report, cancelled: ReleaseUpdater().download(
            info, destination, report, cancelled,
        ))
        self._update_download_task = task
        dialog.canceled.connect(task.cancel)
        task.signals.progress.connect(dialog.setValue)
        task.signals.completed.connect(lambda path: self._update_download_finished(info, path))
        task.signals.failed.connect(self._update_download_failed)
        self.ui_translator.refresh(dialog)
        dialog.show()
        self.io_pool.start(task)

    def _update_download_finished(self, info: UpdateInfo, path: Path) -> None:
        self._update_download_task = None
        self._staged_update = (info, path)
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        self._install_update(path)

    def _update_download_failed(self, message: str) -> None:
        self._update_download_task = None
        if self._update_progress_dialog is not None:
            self._update_progress_dialog.close()
            self._update_progress_dialog = None
        if message != "Operación cancelada" and message != "Descarga cancelada.":
            self._toast("No se pudo actualizar", message, "error")

    def _install_update(self, installer: Path) -> None:
        if not installer.is_file() or not self.close():
            return
        language = "english" if self.settings.data["general"].get("ui_language") == "en" else "spanish"
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(
                [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                 "/CLOSEAPPLICATIONS", "/SP-", f"/LANG={language}"],
                cwd=str(installer.parent), creationflags=flags, close_fds=True,
            )
        except OSError as error:
            self._toast("No se pudo abrir el instalador", str(error), "error")
            self.show()

    def open_settings(self) -> None:
        # Existing imported project fonts must be registered before the profile
        # editor builds its picker and preview.
        register_profile_fonts(self.font_profiles)
        dialog = SettingsDialog(
            self.settings.data, self.credentials, self.font_profiles,
            False, self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.profile_settings is not None:
            chosen_type = dialog.profile_settings._current_type()
            chosen_project = dialog.profile_settings._current_project()
            if chosen_type and chosen_project:
                self.active_font_type = chosen_type
                self.active_font_profile = chosen_project
                self.settings.update_profile(chosen_type, chosen_project)
                register_profile_fonts(self.font_profiles)
                self._refresh_font_profiles()
        values = dialog.values()
        for provider, credential in values["keys"].items():
            # SettingsDialog loads existing encrypted values, so saving every
            # field preserves unchanged keys and lets an intentionally empty
            # field remove a credential.
            self.credentials.set(provider, credential)
        previous_device_mode = str(self.settings.data.get("performance", {}).get("device_mode", "auto"))
        previous_resource_profile = self.resource_policy.name
        self.settings.update_workflow(values)
        self.ui_translator.set_language(self.settings.data["general"]["ui_language"])
        self.ocr.set_endpoint(values.get("ocr_base_url", ""))
        self.ai_panel.ocr_model.setCurrentText(self.settings.data["ocr"]["model"])
        self._psd_auto_sync = bool(values.get("psd_auto_sync", True))
        self._configure_psd_watcher(reset_signatures=False)
        self.resource_policy = build_resource_policy(
            values.get("resource_profile", "auto"), int(values.get("image_cache_mb", 384)),
        )
        configure_native_threads(self.resource_policy)
        device_mode = str(values.get("device_mode", "auto"))
        self.detector.set_resource_policy(self.resource_policy)
        self.detector.set_device_mode(device_mode)
        self.ocr.set_resource_policy(self.resource_policy)
        self.cleaner.set_resource_policy(self.resource_policy)
        self.cleaner.set_device_mode(device_mode)
        self.images.set_memory_limit_mb(self.resource_policy.image_cache_mb)
        self.thread_pool.setMaxThreadCount(self.resource_policy.foreground_workers)
        self.io_pool.setMaxThreadCount(self.resource_policy.io_workers)
        self.image_pool.setMaxThreadCount(1 if self.resource_policy.name == "low" else 2)
        self.canvas_shell.canvas.set_resource_policy(self.resource_policy)
        self.status.set_refresh_interval(self.resource_policy.status_interval_ms)
        self._performance_monitor_timer.setInterval(self.resource_policy.status_interval_ms)
        self._text_render_timer.setInterval(self.resource_policy.text_debounce_ms)
        self._model_idle_timer.setInterval(30_000 if self.resource_policy.name == "low" else 60_000)
        self.history.limit = 40 if self.resource_policy.name == "low" else (60 if self.resource_policy.name == "balanced" else 80)
        self._retain_profile_pages()
        self._refresh_performance_monitor()
        self._models_ready = False
        if (
            device_mode != previous_device_mode
            or self.resource_policy.name != previous_resource_profile
        ) and self._release_task is None:
            def release_changed_models(_progress, _cancelled):
                detector_released = self.detector.unload()
                cleaner_released = self.cleaner.unload()
                return detector_released or cleaner_released
            task = ModelTask(release_changed_models)
            self._release_task = task
            task.signals.completed.connect(self._gpu_models_released)
            task.signals.failed.connect(lambda _message: setattr(self, "_release_task", None))
            self.thread_pool.start(task)
        register_profile_fonts(self.font_profiles)
        selected_family = self.text_panel.family.currentText()
        configure_searchable_font_combo(self.text_panel.family, selected_family)
        self._typography_resources_loaded = True
        self.active_font_type = values.get("active_type", "")
        self.active_font_profile = values.get("active_project", "")
        self.settings.update_profile(self.active_font_type, self.active_font_profile)
        self._refresh_font_profiles()
        self.ai_panel.set_configuration(self.settings.data)
        if self.ai_panel.translate_model.findText(values["model"]) < 0:
            self.ai_panel.translate_model.addItem(values["model"])
        self.ai_panel.translate_model.setCurrentText(values["model"])
        self._configure_autosave_timer()
        self._update_provider_statuses()
        self.ui_translator.refresh(self)
        self._toast("Configuración guardada", "Credenciales cifradas y flujo de IA actualizado.")

    def open_watermark_editor(self) -> None:
        if self.watermark_dialog is None:
            dialog = WatermarkDialog(self.watermark_settings, self)
            dialog.settings_changed.connect(self._watermark_settings_changed)
            dialog.apply_current_requested.connect(self._apply_watermark_current)
            dialog.apply_chapter_requested.connect(self._apply_watermark_chapter)
            dialog.remove_current_requested.connect(self._remove_watermark_current)
            dialog.remove_chapter_requested.connect(self._remove_watermark_chapter)
            dialog.dialog_closed.connect(self._watermark_dialog_closed)
            dialog.redistribute_requested.connect(self._redistribute_watermark_current)
            dialog.redistribute_chapter_requested.connect(self._redistribute_watermark_chapter)
            self.watermark_dialog = dialog
        self.watermark_dialog.set_settings(self.watermark_settings)
        self._watermark_removed_explicitly = False
        self._watermark_previewing = True
        self._refresh_watermark_preview(force=True)
        self.watermark_dialog.show()
        self.watermark_dialog.raise_()
        self.watermark_dialog.activateWindow()

    def _watermark_settings_changed(self, settings: dict) -> None:
        self._watermark_removed_explicitly = False
        previous = self.watermark_settings
        updated = normalized_watermark(settings)
        # Canvas dragging may have changed positions since the dialog opened.
        # Appearance controls must not restore that stale snapshot.
        updated["page_positions"] = copy.deepcopy(previous.get("page_positions", {}))
        updated["positions"] = copy.deepcopy(previous.get("positions"))
        geometry_keys = {
            "png_bytes", "size_mode", "scale_percent", "width_px", "anchor",
            "margin_x", "margin_y", "offset_x", "offset_y", "rotation",
            "repeat", "auto_count", "repeat_count", "avoid_text", "seam_safe", "keep_inside",
            "distribution", "minimum_gap",
        }
        if self.project.pages and any(previous.get(key) != updated.get(key) for key in geometry_keys):
            updated["page_positions"].pop(self.project.active_page.name, None)
        updated["active"] = bool(watermark_bytes(updated))
        self.watermark_settings = updated
        self._refresh_watermark_preview(force=True)
        self._watermark_persist_timer.start()
        if self.project.pages:
            self._history_timer.start()

    def _redistribute_watermark_current(self) -> None:
        if not self.project.pages or not self._watermark_ready():
            return
        self.watermark_settings["page_positions"].pop(self.project.active_page.name, None)
        self.watermark_settings["positions"] = None
        if self.watermark_dialog is not None:
            self.watermark_dialog.set_settings(self.watermark_settings)
        self._refresh_watermark_preview(force=True)
        self._history_timer.start()

    def _redistribute_watermark_chapter(self) -> None:
        if not self.project.pages or not self._watermark_ready():
            return
        self.watermark_settings["page_positions"] = {}
        self.watermark_settings["positions"] = None
        if self.watermark_dialog is not None:
            self.watermark_dialog.set_settings(self.watermark_settings)
        self._refresh_watermark_preview(force=True)
        self._history_timer.start()

    def _chapter_watermarks(self, preview=False) -> dict:
        settings = copy.deepcopy(self.watermark_settings)
        if preview and not settings.get("enabled_pages"):
            settings["enabled_pages"] = [page.name for page in self.project.pages]
        elif preview and self.project.active_page.name not in settings["enabled_pages"]:
            settings["enabled_pages"].append(self.project.active_page.name)
        return chapter_watermark_settings([
            dict(name=page.name, width=page.width, height=page.height,
                 regions=self.page_regions.get(page_key(page), []))
            for page in self.project.pages
        ], settings)

    def _watermark_positions_changed(self, positions: list[list[int]]) -> None:
        if not self.project.pages or self._restoring_state:
            return
        page_name = self.project.active_page.name
        self.watermark_settings.setdefault("page_positions", {})[page_name] = [list(position) for position in positions]
        self._history_timer.start()

    def _watermark_ready(self) -> bool:
        if watermark_bytes(self.watermark_settings):
            return True
        self._toast("Falta la marca", "Selecciona o arrastra primero un archivo PNG.")
        return False

    def _persist_watermark_preferences(self) -> None:
        config = normalized_watermark(self.watermark_settings)
        payload = watermark_bytes(config)
        if payload and payload != self._persisted_watermark_payload:
            try:
                self.watermark_asset_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.watermark_asset_path.with_suffix(".png.tmp")
                temporary.write_bytes(payload)
                temporary.replace(self.watermark_asset_path)
                self._persisted_watermark_payload = payload
            except OSError:
                pass
        saved = {
            key: value for key, value in config.items()
            if key not in {"png_bytes", "enabled_pages", "page_positions", "positions", "source_path"}
        }
        self.settings.update_watermark(saved)

    def _apply_watermark_current(self) -> None:
        if not self.project.pages or not self._watermark_ready():
            return
        enabled = list(self.watermark_settings["enabled_pages"])
        page_name = self.project.active_page.name
        if page_name not in enabled:
            enabled.append(page_name)
        self.watermark_settings["enabled_pages"] = enabled
        self.watermark_settings["active"] = True
        self._watermark_removed_explicitly = False
        self._persist_watermark_preferences()
        self._refresh_watermark_preview(force=True)
        self._history_timer.stop()
        self._record_history()
        self._toast("Marca aplicada", f"La página {page_name} se exportará con la marca de agua.")

    def _apply_watermark_chapter(self) -> None:
        if not self.project.pages or not self._watermark_ready():
            return
        self.watermark_settings["enabled_pages"] = [page.name for page in self.project.pages]
        self.watermark_settings["active"] = True
        self._watermark_removed_explicitly = False
        self._persist_watermark_preferences()
        self._refresh_watermark_preview(force=True)
        self._history_timer.stop()
        self._record_history()
        self._toast("Marca aplicada", f"Se aplicó a las {len(self.project.pages)} páginas del capítulo.")

    def _remove_watermark_current(self) -> None:
        if not self.project.pages:
            return
        page_name = self.project.active_page.name
        self._watermark_removed_explicitly = True
        self.watermark_settings["enabled_pages"] = [
            name for name in self.watermark_settings["enabled_pages"] if name != page_name
        ]
        self.watermark_settings.get("page_positions", {}).pop(page_name, None)
        if not self.watermark_settings["enabled_pages"]:
            self.watermark_settings["active"] = False
        self._persist_watermark_preferences()
        self._refresh_watermark_preview(force=False)
        self._history_timer.stop()
        self._record_history()

    def _remove_watermark_chapter(self) -> None:
        self.watermark_settings["enabled_pages"] = []
        self._watermark_removed_explicitly = True
        self.watermark_settings["page_positions"] = {}
        self.watermark_settings["active"] = False
        self._persist_watermark_preferences()
        self._refresh_watermark_preview(force=False)
        if self.project.pages:
            self._history_timer.stop()
            self._record_history()

    def _watermark_dialog_closed(self) -> None:
        self._watermark_previewing = False
        if self.project.pages and watermark_bytes(self.watermark_settings) and not self._watermark_removed_explicitly:
            if not self.watermark_settings.get("enabled_pages"):
                self.watermark_settings["enabled_pages"] = [page.name for page in self.project.pages]
                self._history_timer.stop()
                self._record_history()
            self.watermark_settings["active"] = True
        self._persist_watermark_preferences()
        self._refresh_watermark_preview(force=False)

    def _refresh_watermark_preview(self, force: bool = False) -> None:
        if not self.project.pages:
            self.canvas_shell.canvas.clear_watermark()
            return
        current_name = self.project.active_page.name
        enabled = current_name in self.watermark_settings.get("enabled_pages", [])
        visible = bool(force or enabled)
        preview_settings = self._chapter_watermarks(preview=force).get(current_name)
        self.canvas_shell.canvas.set_watermark(preview_settings, visible, current_name)
        if self.watermark_dialog is not None:
            self.watermark_dialog.set_scope_status(
                enabled, len(self.watermark_settings.get("enabled_pages", [])), len(self.project.pages),
                len(self.canvas_shell.canvas._watermark_items),
            )

    def _watermark_for_page(self, page) -> dict | None:
        if page.name not in self.watermark_settings.get("enabled_pages", []):
            return None
        if not watermark_bytes(self.watermark_settings):
            return None
        return self._chapter_watermarks().get(page.name)

    def edit_typography(self) -> None:
        index = self.layers.current_index()
        if not 0 <= index < len(self.regions):
            self._toast("Selecciona una capa", "Elige la caja cuyo texto deseas componer.")
            return
        region = self.regions[index]
        page_default = self.page_styles.get(self._active_key() or "", {}).get("typography", DEFAULT_STYLE)
        dialog = TypographyDialog({**page_default, **region.get("style", {})}, self.style_presets, self)
        if dialog.exec() != QDialog.Accepted:
            return
        style, preset_name = dialog.values()
        existing_style = TypographyManager.normalized(region.get("style", {}))
        for effect_key in EFFECT_STYLE_KEYS:
            style[effect_key] = existing_style[effect_key]
        style = TypographyManager.normalized(style)
        region["style"] = style
        if preset_name:
            self.style_presets[preset_name] = dict(style)
            region["style_preset"] = preset_name
        elif dialog.preset.currentText() in self.style_presets:
            region["style_preset"] = dialog.preset.currentText()
        self._store_active_regions(self.regions)
        self.text_panel.set_presets(self.style_presets)
        self.text_panel.set_layer(index, style, str(region.get("style_preset", "")))
        self.canvas_shell.canvas.add_text([r.get("applied_text", "") for r in self.regions], self.regions)
        self._record_history()
        self._toast("Tipografía aplicada", f"Capa {index + 1}: fuente, ajuste y composición actualizados.")

    def _snapshot(self) -> dict:
        if self.project.pages and not self._restoring_state:
            self._refresh_page_text(self._active_key())
        return {
            "active_index": self.project.active_index,
            "page_regions": self.page_regions,
            "clean_results": self.clean_results,
            "page_texts": self.page_texts,
            "page_styles": self.page_styles,
            "style_presets": self.style_presets,
            "effect_presets": self.effect_presets,
            "watermark_settings": self.watermark_settings,
        }

    def _record_history(self) -> None:
        if not self._restoring_state:
            if self.history.record(self._snapshot()):
                self._dirty = True
            self._update_history_actions()
            self._refresh_page_statuses()

    def _update_history_actions(self) -> None:
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self.history.can_undo)
            self.redo_action.setEnabled(self.history.can_redo)
            self.topbar.history_buttons["undo"].setEnabled(self.history.can_undo)
            self.topbar.history_buttons["redo"].setEnabled(self.history.can_redo)

    def _editor_text_changed(self) -> None:
        if not self._restoring_state and self.project.pages:
            index = self.layers.current_index()
            if 0 <= index < len(self.regions):
                value = self.inspector.text_editor.toPlainText()
                self.regions[index]["translation"] = value
                self.regions[index]["applied_text"] = value
                self.regions[index]["typeset_completed"] = bool(value.strip() and self.regions[index].get("style"))
                self._store_active_regions(self.regions)
                self._refresh_page_text(self._active_key())
                self.canvas_shell.canvas.preview_text_value(index, value, self.regions[index])
            self._history_timer.start()
            self._refresh_page_statuses()

    def _finish_text_editing(self) -> None:
        """Restore exact effects once, rather than rebuilding on every key."""
        if self._restoring_state or not self.project.pages or not self.inspector.auto_apply.isChecked():
            return
        index = self.layers.current_index()
        if 0 <= index < len(self.regions):
            self._schedule_text_render(immediate=True, indices=[index])

    def _style_changed(self, mode: str, options: dict) -> None:
        if self._restoring_state or not self.project.pages:
            return
        if mode == "clean":
            self.canvas_shell.canvas.set_brush_size(int(options.get("thickness", 15)))
        key = self._active_key()
        if key:
            self.page_styles.setdefault(key, {})[mode] = dict(options)
            if mode in {"ocr", "translation"} and "manga_mode" in options:
                self.canvas_shell.canvas.set_manga_mode(bool(options["manga_mode"]))
                selected = self.layers.current_index()
                selected_id = (
                    str(self.regions[selected].get("id", ""))
                    if 0 <= selected < len(self.regions) else ""
                )
                self.layers.set_regions(
                    self.regions, selected_id or None, keep_empty_selection=selected < 0,
                )
            self._history_timer.start()

    def _commit_debounced_state(self) -> None:
        if self.project.pages and not self._restoring_state:
            self._refresh_page_text(self._active_key())
            self._record_history()

    def _restore_snapshot(self, snapshot: dict) -> None:
        self._history_timer.stop()
        view_state = self.canvas_shell.canvas.view_state()
        self._restoring_state = True
        try:
            self.page_regions = snapshot["page_regions"]
            self.clean_results = snapshot["clean_results"]
            self.page_texts = snapshot["page_texts"]
            self.page_styles = snapshot["page_styles"]
            self.style_presets = snapshot.get("style_presets", {})
            self.effect_presets = snapshot.get("effect_presets", {
                name: TypographyManager.normalized(style)
                for name, style in BUILTIN_EFFECT_PRESETS.items()
            })
            self.watermark_settings = normalized_watermark(snapshot.get("watermark_settings"))
            if self.watermark_dialog is not None:
                self.watermark_dialog.set_settings(self.watermark_settings)
            self.effects_panel.set_presets(self.effect_presets)
            if self.project.pages:
                index = min(max(0, int(snapshot.get("active_index", 0))), len(self.project.pages) - 1)
                self.images_panel.list.blockSignals(True)
                self.images_panel.list.setCurrentRow(index)
                self.images_panel.list.blockSignals(False)
                self._set_page(index)
                self.canvas_shell.canvas.restore_view_state(view_state)
                self._sync_script_panel()
        finally:
            self._restoring_state = False
        self._update_history_actions()

    def undo(self) -> None:
        if self.current_task is not None:
            self._toast("Proceso en curso", "Espera a que termine la limpieza, OCR o traducción antes de deshacer.")
            return
        if self._history_timer.isActive():
            self._history_timer.stop()
            self._commit_debounced_state()
        snapshot = self.history.undo()
        if snapshot is None:
            self._toast("Deshacer", "No hay cambios anteriores.")
            return
        self._restore_snapshot(snapshot)
        self._toast("Deshacer", "Se restauró el cambio anterior.")

    def redo(self) -> None:
        if self.current_task is not None:
            self._toast("Proceso en curso", "Espera a que termine la limpieza, OCR o traducción antes de rehacer.")
            return
        if self._history_timer.isActive():
            self._history_timer.stop()
            self._commit_debounced_state()
        snapshot = self.history.redo()
        if snapshot is None:
            self._toast("Rehacer", "No hay cambios posteriores.")
            return
        self._restore_snapshot(snapshot)
        self._toast("Rehacer", "Se volvió a aplicar el cambio.")

    def _task_completed(self, result, title: str, message: str, finished) -> None:
        if self.task_progress._owner == "foreground" and self.task_progress._cancel_pending:
            self._task_failed("Operación cancelada")
            return
        page_id = self._active_task_page_key
        self.ai_panel.set_ocr_usage(self.ocr.usage_stats())
        metric_name = "LaMa" if title == "Limpieza" else ("LaMa del capítulo" if title == "Limpieza de capítulo" else title)
        metric = self.performance.finish(self._active_operation_key, metric_name)
        self._active_operation_key = ""
        self._active_operation_name = ""
        self._active_task_page_key = None
        self.current_task = None
        self.inspector.set_progress(100, active=False)
        self.task_progress.finish()
        if metric:
            self.status.set_operation_time(metric.name, metric.seconds)
            self._refresh_performance_monitor()
        if finished:
            finished(result)
        category = self._operation_category(title)
        if category:
            completed = {page_id} if page_id and "capítulo" not in title.casefold() else set()
            if "capítulo" in title.casefold() and isinstance(result, dict):
                completed.update((result.get("pages", {}) if category == "Traducción" else result).keys())
            for key in completed:
                if key in self._page_failures:
                    self._page_failures[key].pop(category, None)
                    if not self._page_failures[key]:
                        del self._page_failures[key]
            if completed:
                self._refresh_page_statuses()
        self._toast(f"{title} completado", message)

    @staticmethod
    def _operation_category(title: str) -> str:
        name = title.casefold()
        if "ocr" in name:
            return "OCR"
        if "traducci" in name:
            return "Traducción"
        if "limpieza" in name or "máscara" in name:
            return "Limpieza"
        if "detecci" in name:
            return "Detección"
        if "exportaci" in name:
            return "Exportación"
        return ""

    def _task_failed(self, message: str) -> None:
        self.ai_panel.set_ocr_usage(self.ocr.usage_stats())
        operation_name = self._active_operation_name
        page_id = self._active_task_page_key
        metric_name = "LaMa" if operation_name == "Limpieza" else (
            "LaMa del capítulo" if operation_name == "Limpieza de capítulo" else operation_name or "Error"
        )
        metric = self.performance.finish(self._active_operation_key, metric_name)
        self._active_operation_key = ""
        self._active_operation_name = ""
        self._active_task_page_key = None
        self.current_task = None
        self.inspector.set_progress(0, active=False)
        cancelled = message == "Operación cancelada"
        self.task_progress.finish(error="" if cancelled else message, cancelled=cancelled)
        if cancelled:
            if operation_name in {"Máscara de texto", "Limpieza"}:
                self.cancel_clean_mask_preview(silent=True)
            if metric:
                self.status.set_operation_time(metric.name, metric.seconds)
                self._refresh_performance_monitor()
            self._toast("Proceso cancelado", "No se aplicaron resultados de la tarea.")
            return
        category = self._operation_category(operation_name)
        if category and page_id:
            self._page_failures.setdefault(page_id, {})[category] = message.splitlines()[0]
            self._refresh_page_statuses()
        if "OCR" in operation_name.upper():
            self.ai_panel.set_status("ocr", message.splitlines()[0], self.ai_panel.STATUS_WARNING)
            if "404" in message:
                provider, model = self.ai_panel.ocr_configuration()
                self._ocr_rejected_config = (
                    provider, model.strip(), self.settings.data["ocr"].get("base_url", ""),
                )
                self._update_provider_statuses()
        if operation_name in {"Máscara de texto", "Limpieza"}:
            # Every error path must release the editable overlay too.  Model
            # errors previously skipped the normal completion callback.
            self.cancel_clean_mask_preview(silent=True)
        if metric:
            self.status.set_operation_time(metric.name, metric.seconds)
            self._refresh_performance_monitor()
        self._toast("Error de proceso", message)

    def _set_empty_canvas(self) -> None:
        # Also invalidates a preparation task that is still running and has not
        # produced a pending plan yet.
        self.cancel_clean_mask_preview(silent=True)
        self._displayed_page_path = None
        self.canvas_shell.canvas.clear_page()
        self.canvas_shell.page_label.setText("—  /  —")
        self.inspector.update_page(0, 0)
        self.status.set_page("Sin imagen", 0, 0, "—")
        self.layers.set_image_layers(False, False, {})
        self.layers.set_source_layers([], {})
        self.ai_panel.set_quality_available(False)
        self.inspector.clear_page_ocr()

    def _set_page(self, index: int) -> None:
        if not self.project.pages:
            self._set_empty_canvas()
            return
        if self._page_metric_key:
            self.performance.cancel(self._page_metric_key)
        self._page_metric_key = f"page:{index}:{perf_counter()}"
        self._page_metric_name = "Apertura de página" if self._displayed_page_path is None else "Cambio de página"
        self.performance.start(self._page_metric_key)
        if index != self.project.active_index:
            self._remember_displayed_page_view()
        if index != self.project.active_index:
            # Do this even when the red overlay has not appeared yet: an old
            # prepare_masks callback must never repaint itself on another page
            # (or after quickly returning to the original one).
            self.cancel_clean_mask_preview(silent=True)
        if not self._restoring_state:
            if self._history_timer.isActive():
                self._history_timer.stop()
                self._commit_debounced_state()
            previous_key = self._active_key()
            self._refresh_page_text(previous_key)
        page = self.project.set_active(index)
        key = page_key(page)
        self.regions = self.page_regions.get(key, [])
        if self._sanitize_transport_markers(self.regions):
            self._store_active_regions(self.regions)
            self._dirty = True
        self._refresh_page_text(key)
        same_page = self._displayed_page_path == page.path and not self.canvas_shell.canvas._pixmap_item.pixmap().isNull()
        if same_page:
            self._render_page_layers(key)
            self._finish_page_metric()
        else:
            self._pending_page_load = (page, key)
            source_states = self._source_layer_states(key)
            if self.images.cached_active_image(page.path, source_states) is not None:
                self._page_request_timer.stop()
                self._start_pending_page_load()
            else:
                self._page_request_timer.start()
        if self.regions:
            self._layer_selected(0)
        else:
            self.inspector.clear_layer_text()
        for mode, options in self.page_styles.get(key, {}).items():
            self.ai_panel.set_style_options(mode, options)
        self.canvas_shell.page_label.setText(f"{index + 1}  /  {len(self.project.pages)}")
        self.inspector.update_page(index, len(self.project.pages))
        if same_page:
            self.status.set_page(page.name, page.width, page.height, page.size_label)
        if self.tool_panel.currentWidget() is self.script_panel:
            self._sync_script_panel()
        if not self._restoring_state:
            self.history.replace_current(self._snapshot())
            self._update_history_actions()

    def _render_page_layers(self, key: str, page_changed: bool = False) -> None:
        render_started = perf_counter()
        manga_mode = bool(
            self.page_styles.get(key, {}).get("translation", {}).get(
                "manga_mode",
                self.page_styles.get(key, {}).get("ocr", {}).get("manga_mode", False),
            )
        )
        self.canvas_shell.canvas.set_manga_mode(manga_mode)
        self.canvas_shell.canvas.set_regions(self.regions)
        visible_text = [
            region.get("applied_text") or region.get("translation") or region.get("text", "")
            for region in self.regions
        ]
        self.canvas_shell.canvas.add_text_batched(visible_text, self.regions)
        self.layers.set_regions(self.regions)
        clean_result = self.clean_results.get(key, {})
        patches = list(clean_result.get("patches", []))
        retained = [
            patch for patch in patches
            if not (
                str(patch.get("kind", "legacy")) in {"manual", "legacy", "restore"}
                and patch.get("mask") is not None
                and StrokeEngine.is_suspicious_connector(patch["mask"])
            )
        ]
        if len(retained) != len(patches):
            # Repair projects created by older brush implementations as soon
            # as their page is shown; the defective strip no longer remains
            # stuck waiting for a manual cleanup command.
            self.clean_results[key] = {**clean_result, "patches": retained}
            self._dirty = True
        if page_changed:
            # Never leave patches from the previous page above the new base
            # image while the incremental patch renderer catches up.
            self.canvas_shell.canvas.clear_cleaning()
        if retained:
            self.canvas_shell.canvas.apply_cleaning(retained, replace=not page_changed)
        else:
            self.canvas_shell.canvas.clear_cleaning()
        self._refresh_image_layers(key)
        self._refresh_watermark_preview(force=self._watermark_previewing)
        self._record_performance_metric("Carga de capas", perf_counter() - render_started)

    def _finish_page_metric(self) -> None:
        if not self._page_metric_key:
            return
        metric = self.performance.finish(self._page_metric_key, self._page_metric_name or "Cambio de página")
        self._page_metric_key = ""
        self._page_metric_name = ""
        if metric:
            self.status.set_operation_time(metric.name, metric.seconds)
            self._refresh_performance_monitor()

    def _load_page_image(self, page, key: str, preserve_view: bool = False) -> None:
        self._page_load_token += 1
        token = self._page_load_token
        if self._image_task is not None:
            self._image_task.cancel()
        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
            self._prefetch_task = None
        self.prefetch_pool.clear()
        self.image_pool.clear()
        self.status.set_page(f"Cargando {page.name}…", page.width, page.height, page.size_label)
        view_state = (
            self.canvas_shell.canvas.view_state()
            if preserve_view
            else copy.deepcopy(self._page_view_states.get(key))
        )
        selected_indices = self.layers.selected_indices() if preserve_view else []
        current_index = self.layers.current_index() if preserve_view else -1

        source_states = copy.deepcopy(self._source_layer_states(key))
        cached_image = self.images.cached_active_image(page.path, source_states)
        def load_page(_progress, cancelled):
            if cancelled():
                return None
            return self.images.active_image(page.path, source_states)

        task = ModelTask(load_page)
        self._image_task = task

        def image_ready(image) -> None:
            if token != self._page_load_token or image is None or not self.project.pages:
                return
            active = self.project.active_page
            if active.path != page.path:
                return
            self._image_task = None
            final_view_state = (
                self.canvas_shell.canvas.view_state()
                if self._displayed_page_path == page.path
                else view_state
            )
            self._displayed_page_path = page.path
            canvas = self.canvas_shell.canvas
            canvas.setUpdatesEnabled(False)
            canvas.set_image(image, (page.width, page.height))
            self.status.set_page(page.name, page.width, page.height, page.size_label)
            # Let Qt paint the original pixels before constructing boxes,
            # text effects, patches and watermarks. Previously the image was
            # decoded but remained invisible until every overlay was ready.
            def finish_layers() -> None:
                if (
                    token != self._page_load_token or not self.project.pages
                    or self.project.active_page.path != page.path
                ):
                    return
                self.regions = self.page_regions.get(key, [])
                self._apply_dialogue_defaults(self.regions, key=key)
                self._render_page_layers(key, page_changed=not preserve_view)
                if final_view_state is not None:
                    self.canvas_shell.canvas.restore_view_state(final_view_state)
                    self._page_view_states[key] = copy.deepcopy(final_view_state)
                if preserve_view:
                    valid = [index for index in selected_indices if 0 <= index < len(self.regions)]
                    if valid:
                        current = current_index if current_index in valid else valid[-1]
                        self.layers.set_selected_indices(valid, current)
                        self.canvas_shell.canvas.select_regions(valid, current)
                self._refresh_page_statuses()
                self._retain_profile_pages()
                self._finish_page_metric()
                QTimer.singleShot(
                    1400 if self.resource_policy.name == "low" else 350,
                    self._warm_up_models,
                )

            try:
                finish_layers()
            finally:
                canvas.setUpdatesEnabled(True)
                canvas.viewport().update()
            if self.resource_policy.page_cache_radius > 0:
                QTimer.singleShot(40, self._prefetch_adjacent_pages)

        task.signals.completed.connect(image_ready)
        task.signals.failed.connect(lambda message: self._page_image_failed(token, message))
        if cached_image is not None:
            # Returning to a recently viewed page no longer waits for a worker
            # round trip or a second conversion from disk.
            self._image_task = None
            QTimer.singleShot(0, lambda image=cached_image: image_ready(image))
        else:
            self.image_pool.start(task)

    def _start_pending_page_load(self) -> None:
        pending = self._pending_page_load
        self._pending_page_load = None
        if pending is not None:
            self._load_page_image(*pending)

    def _prefetch_adjacent_pages(self) -> None:
        """Warm the next likely page after the active page is fully visible."""
        if not self.project.pages or self._image_task is not None:
            return
        active = self.project.active_index
        radius = max(0, int(self.resource_policy.page_cache_radius))
        if radius <= 0:
            self._retain_profile_pages()
            return
        offsets = tuple(
            offset
            for distance in range(1, radius + 1)
            for offset in (distance, -distance)
        )
        candidates: list[tuple[Path, dict]] = []
        for offset in offsets:
            index = active + offset
            if not 0 <= index < len(self.project.pages):
                continue
            page = self.project.pages[index]
            if page.path is None:
                continue
            states = copy.deepcopy(self._source_layer_states(page_key(page)))
            if self.images.cached_active_image(page.path, states) is None:
                candidates.append((page.path, states))
        if not candidates:
            return

        def prefetch(_progress, cancelled):
            for path, states in candidates:
                if cancelled():
                    return None
                self.images.prefetch_image(path, states)
            return True

        task = ModelTask(prefetch)
        self._prefetch_task = task

        def finished(_result=None) -> None:
            if self._prefetch_task is task:
                self._prefetch_task = None
            self._retain_profile_pages()

        task.signals.completed.connect(finished)
        task.signals.failed.connect(finished)
        self.prefetch_pool.start(task, -1)

    def _page_image_failed(self, token: int, message: str) -> None:
        if token == self._page_load_token:
            self._image_task = None
            self._finish_page_metric()
            self._toast("No se pudo cargar la imagen", message)

    def previous_page(self) -> None:
        if self.project.pages:
            self.images_panel.list.setCurrentRow(max(0, self.project.active_index - 1))

    def next_page(self) -> None:
        if self.project.pages:
            self.images_panel.list.setCurrentRow(min(len(self.project.pages) - 1, self.project.active_index + 1))

    def _adjust_zoom(self, delta: int) -> None:
        self.canvas_shell.canvas.set_zoom(self.canvas_shell.canvas._zoom + delta)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path:
                self.load_dropped_path(local_path)
                event.acceptProposedAction()
                return
        event.ignore()

    def open_chapter(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Abrir carpeta del capítulo")
        if not folder:
            return
        self.load_chapter_folder(Path(folder))

    def load_dropped_path(self, dropped_path: str) -> None:
        path = Path(dropped_path)
        if path.is_file() and path.suffix.lower() in {".psd", ".psb"}:
            self.load_chapter_folder(path.parent, [path])
        else:
            self.load_chapter_folder(path if path.is_dir() else path.parent)

    def open_psd_files(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "Abrir PSD como capítulo", "", "Photoshop (*.psd *.psb)"
        )
        paths = [Path(name) for name in filenames if Path(name).is_file()]
        if paths:
            self.load_chapter_folder(paths[0].parent, paths)

    def load_chapter_folder(self, folder: Path, selected_paths: list[Path] | None = None) -> None:
        if not folder.is_dir():
            self._toast("Carpeta no válida", "Arrastra una carpeta local que contenga imágenes.")
            return
        project_types = self.font_profiles.project_types()
        has_profiles = any(self.font_profiles.projects(project_type) for project_type in project_types)
        if has_profiles:
            selector = ProjectSelectionDialog(
                self.font_profiles, self.active_font_type, self.active_font_profile, self,
            )
            if selector.exec() != QDialog.Accepted:
                return
            selected = selector.selected_project()
            if selected is None:
                return
            self.active_font_type, self.active_font_profile = selected
        self.settings.update_profile(self.active_font_type, self.active_font_profile)
        self._history_timer.stop()
        if self._folder_task is not None:
            self._folder_task.cancel()
        self._folder_load_token += 1
        token = self._folder_load_token
        self.images_panel.folder.setText(f"Leyendo: {folder.name}…")
        self.status.set_page("Cargando capítulo…", 0, 0, "—")
        def scan(progress, cancelled):
            if selected_paths is None:
                return ProjectManager.scan_folder(folder, progress, cancelled)
            pages = []
            total = max(1, len(selected_paths))
            for index, path in enumerate(selected_paths, start=1):
                if cancelled():
                    break
                pages.append(ProjectManager._page_from_path(path))
                progress(int(index * 100 / total), f"Página {index} de {total} · leyendo capítulo")
            return pages
        task = ModelTask(scan)
        self._folder_task = task
        if self.current_task is None:
            self.task_progress.start("Cargar capítulo", "Leyendo imágenes…", owner=f"folder:{token}")
        task.signals.progress.connect(
            lambda value, owner=f"folder:{token}": self.task_progress.update_progress(value, owner=owner)
        )
        task.signals.stage.connect(
            lambda value, _page_id, owner=f"folder:{token}": self.task_progress.set_stage(value, owner=owner)
        )
        task.signals.completed.connect(lambda pages: self._chapter_scanned(folder, pages, token))
        task.signals.failed.connect(lambda message: self._chapter_scan_failed(message, token))
        self.io_pool.start(task)

    def _chapter_scan_failed(self, message: str, token: int) -> None:
        if token != self._folder_load_token:
            return
        self._folder_task = None
        cancelled = message == "Operación cancelada"
        self.task_progress.finish(
            error="" if cancelled else message, owner=f"folder:{token}", cancelled=cancelled,
        )
        if not cancelled:
            self._toast("No se pudo cargar el capítulo", message)

    def _chapter_scanned(self, folder: Path, pages: list, token: int) -> None:
        if token != self._folder_load_token:
            return
        if self.task_progress._owner == f"folder:{token}" and self.task_progress._cancel_pending:
            self._chapter_scan_failed("Operación cancelada", token)
            return
        self._folder_task = None
        self.task_progress.finish(
            error="La carpeta no contiene imágenes compatibles." if not pages else "",
            owner=f"folder:{token}",
        )
        if not pages:
            self._toast("Sin imágenes", "La carpeta no contiene imágenes compatibles.")
            return
        self._page_failures.clear()
        self.project.pages = pages
        self.project.active_index = 0
        self._configure_psd_watcher(reset_signatures=True)
        self.project_file = None
        self._displayed_page_path = None
        self._page_view_states.clear()
        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
            self._prefetch_task = None
        self.prefetch_pool.clear()
        self.images.clear()
        self.page_regions = {}
        self.clean_results = {}
        self._quality_review_entries.clear()
        self.page_texts = {}
        base_styles = {mode: self.ai_panel.style_options(mode) for mode, _ in self.ai_panel.MODES}
        self.page_styles = {page_key(page): {mode: dict(options) for mode, options in base_styles.items()} for page in self.project.pages}
        self._install_project_typography_defaults()
        self.watermark_settings = normalized_watermark(self.watermark_settings)
        self.watermark_settings["page_positions"] = {}
        self.watermark_settings["positions"] = None
        self.watermark_settings["enabled_pages"] = (
            [page.name for page in self.project.pages]
            if self.watermark_settings.get("active") and watermark_bytes(self.watermark_settings)
            else []
        )
        if self.watermark_dialog is not None:
            self.watermark_dialog.set_settings(self.watermark_settings)
        self.style_presets = {}
        self.effect_presets = {
            name: TypographyManager.normalized(style)
            for name, style in BUILTIN_EFFECT_PRESETS.items()
        }
        self.effects_panel.set_presets(self.effect_presets)
        self.images_panel.refresh_pages(self.project.pages)
        self._set_page(0)
        # This first request is deliberate, not rapid navigation: start its
        # worker immediately instead of paying the navigation debounce.
        if self._page_request_timer.isActive():
            self._page_request_timer.stop()
        self._start_pending_page_load()
        self.history.reset(self._snapshot())
        self._dirty = False
        self._update_history_actions()
        self._refresh_font_profiles()
        psd_pages = [page for page in self.project.pages if page.source_layers]
        psd_detail = f" · {sum(len(page.source_layers) for page in psd_pages)} capas PSD" if psd_pages else ""
        self._toast("Capítulo cargado", f"{len(self.project.pages)} imágenes disponibles{psd_detail}.")

    def run_detection(self) -> None:
        if not self.project.pages or self.project.active_page.path is None:
            self._toast("Abre un capítulo", "Selecciona una carpeta con imágenes antes de ejecutar YOLO.")
            return
        page = self.project.active_page
        source_states = copy.deepcopy(self._source_layer_states(self._active_key()))
        self._start_task(
            lambda progress, cancelled: self.detector.detect(page.path, progress, cancelled, source_states),
            "Detección YOLO",
            "Las regiones reales se dibujaron sobre el canvas.",
            self._show_regions,
            stage="Detectando texto con YOLO…",
        )

    def run_ocr_api(self) -> None:
        self._run_ocr_for_regions(self.regions)

    def run_ocr_api_one(self, force: bool = False) -> None:
        index = self.layers.current_index()
        if not 0 <= index < len(self.regions):
            self._toast("Selecciona una caja", "Haz clic en una caja antes de ejecutar OCR individual.")
            return
        self._run_ocr_for_regions([self.regions[index]], force=force)

    def _run_ocr_for_regions(self, target_regions: list[dict], force: bool = False) -> None:
        if self.current_task is not None:
            self._toast("Proceso en curso", "Espera a que termine la operación actual.")
            return
        if not self.project.pages or self.project.active_page.path is None:
            self._toast("Abre un capítulo", "Selecciona una carpeta con imágenes antes de ejecutar OCR.")
            return
        if not target_regions:
            self._toast("Sin cajas de texto", "Primero ejecuta Autodetectar texto (YOLO).")
            return
        if not force:
            target_regions = [
                region for region in target_regions
                if OCRManager.needs_recognition(region)
            ]
            if not target_regions:
                self.ai_panel.set_status("ocr", "OCR ya completado · sin solicitudes nuevas", self.ai_panel.STATUS_OK)
                self._toast("OCR ya disponible", "Todas las cajas de esta página ya contienen OCR.")
                return
        self._update_provider_statuses()
        if not self.ai_panel.is_ready("ocr"):
            self.open_settings()
            return
        provider, model = self.ai_panel.ocr_configuration()
        key = self.credentials.get("Alibaba Cloud" if provider == "Qwen API" else provider)
        try:
            self.ocr.set_endpoint(self.settings.data["ocr"].get("base_url", ""))
            self.ocr.validate(provider, key)
        except (RuntimeError, ValueError) as error:
            self._toast("OCR mediante API", str(error))
            return
        page = self.project.active_page
        source_states = copy.deepcopy(self._source_layer_states(self._active_key()))
        request_targets: dict[str, dict] = {}
        request_regions: list[dict] = []
        for region in target_regions:
            token = uuid.uuid4().hex
            requested = copy.deepcopy(region)
            requested["_ocr_request_token"] = token
            request_targets[token] = region
            request_regions.append(requested)
        ordered = TypographyManager.ordered(
            request_regions, bool(self.ai_panel.style_options("ocr").get("manga_mode", False))
        )
        self.ai_panel.set_status(
            "ocr", f"OCR en cola segura · {len(ordered)} caja(s) · {self.ocr.parallelism} conexión(es)",
            self.ai_panel.STATUS_WARNING,
        )
        self._start_task(
            lambda progress, cancelled: self.ocr.run_regions(
                page.path, ordered, provider, model, key, progress, cancelled, source_states,
                force_refresh=force,
            ),
            "OCR completado",
            "El texto reconocido se insertó en las cajas y en el panel de aplicación.",
            lambda regions: self._show_ocr_text(regions, request_targets),
            stage="Esperando respuesta de Alibaba OCR…",
        )

    def _show_ocr_text(
        self, regions: list[dict], request_targets: dict[str, dict] | None = None,
    ) -> None:
        if request_targets:
            self._merge_ocr_results(regions, request_targets)
        else:
            # Compatibility for restored tasks created before request tokens.
            recognized = {region.get("id"): region for region in regions}
            self.regions = [recognized.get(region.get("id"), region) for region in self.regions]
        self._apply_dialogue_defaults(self.regions)
        self._enable_adaptive_typesetting(self.regions)
        for region in self.regions:
            self._promote_ocr_overlay(region)
        self._store_active_regions(self.regions)
        texts = [region.get("applied_text", "") for region in self.regions]
        self.canvas_shell.canvas.set_regions(self.regions)
        self.canvas_shell.canvas.add_text_batched(texts, self.regions)
        self.layers.set_regions(self.regions)
        key = self._active_key()
        self._refresh_page_text(key)
        self._layer_selected(max(0, self.layers.current_index()))
        self._record_history()
        self.ai_panel.set_status(
            "ocr", f"OCR listo · {len(regions)} caja(s) · caché activa",
            self.ai_panel.STATUS_OK,
        )

    def _merge_ocr_results(
        self, recognized_regions: list[dict], request_targets: dict[str, dict],
    ) -> None:
        """Apply OCR by an opaque request token, never by a visible layer ID."""
        for recognized in recognized_regions:
            token = str(recognized.get("_ocr_request_token", ""))
            target = request_targets.get(token)
            if target is None:
                continue
            old_source = str(target.get("text", ""))
            old_translation = str(target.get("translation", ""))
            old_applied = str(target.get("applied_text", ""))
            payload = {
                key: value for key, value in recognized.items()
                if key != "_ocr_request_token"
            }
            target.update(payload)
            source = OCRManager.normalize_cjk_text(str(target.get("text", "")))
            target["text"] = source
            target["ocr_stale"] = False
            target["ocr_checked"] = True
            if not old_translation.strip() or old_translation == old_source:
                target["translation"] = source
            if (
                not old_applied.strip()
                or old_applied == old_source
                or old_applied == old_translation
            ):
                target["applied_text"] = str(target.get("translation") or source)

    def run_ocr_api_all(self) -> None:
        if self.current_task is not None:
            self._toast("Proceso en curso", "Espera a que termine la operación actual.")
            return
        if not self.project.pages:
            self._toast("Abre un capítulo", "Carga imágenes antes de ejecutar OCR por lote.")
            return
        work = []
        chapter_targets: dict[str, dict[str, dict]] = {}
        for page in self.project.pages:
            key = page_key(page)
            missing = [
                region for region in self.page_regions.get(key, [])
                if OCRManager.needs_recognition(region)
            ]
            if page.path and missing:
                targets: dict[str, dict] = {}
                requested: list[dict] = []
                for region in missing:
                    token = uuid.uuid4().hex
                    clone = copy.deepcopy(region)
                    clone["_ocr_request_token"] = token
                    targets[token] = region
                    requested.append(clone)
                chapter_targets[key] = targets
                work.append((page, requested))
        if not work:
            if any(self.page_regions.get(page_key(page)) for page in self.project.pages):
                self.ai_panel.set_status("ocr", "OCR del capítulo ya completado", self.ai_panel.STATUS_OK)
                self._toast("OCR ya disponible", "Todas las cajas del capítulo ya contienen OCR.")
                return
            self._toast("Sin cajas de texto", "Detecta o dibuja cajas en las páginas primero.")
            return
        self._update_provider_statuses()
        if not self.ai_panel.is_ready("ocr"):
            self.open_settings()
            return
        provider, model = self.ai_panel.ocr_configuration()
        key = self.credentials.get("Alibaba Cloud" if provider == "Qwen API" else provider)
        try:
            self.ocr.set_endpoint(self.settings.data["ocr"].get("base_url", ""))
            self.ocr.validate(provider, key)
        except (RuntimeError, ValueError) as error:
            self._toast("OCR mediante API", str(error))
            return

        def recognize_chapter(progress, cancelled):
            results = {}
            for page_index, (page, missing) in enumerate(work):
                page_id = page_key(page)
                manga = bool(self.page_styles.get(page_id, {}).get("ocr", {}).get("manga_mode", False))
                ordered = TypographyManager.ordered(missing, manga)
                offset, span = page_index * 100 / len(work), 100 / len(work)
                page_stage = f"Página {page_index + 1} de {len(work)} · OCR"
                progress(int(offset), page_stage, page_id)
                results[page_id] = self.ocr.run_regions(
                    page.path, ordered, provider, model, key,
                    lambda value, base=offset, amount=span, label=page_stage, current=page_id: progress(
                        int(base + value * amount / 100), label, current,
                    ),
                    cancelled, copy.deepcopy(self._source_layer_states(page_id)),
                )
                if cancelled():
                    return {}
            return results

        self.ai_panel.set_status(
            "ocr", f"OCR de capítulo · cola segura de {self.ocr.parallelism} conexión(es)",
            self.ai_panel.STATUS_WARNING,
        )
        self._start_task(
            recognize_chapter, "OCR del capítulo",
            f"Se procesaron {len(work)} páginas pendientes en orden de lectura.",
            lambda results: self._show_chapter_ocr(results, chapter_targets),
            stage="Procesando OCR del capítulo…",
        )

    def _show_chapter_ocr(
        self, results: dict[str, list[dict]],
        chapter_targets: dict[str, dict[str, dict]] | None = None,
    ) -> None:
        for key, recognized_regions in results.items():
            if chapter_targets and key in chapter_targets:
                self._merge_ocr_results(recognized_regions, chapter_targets[key])
                merged = self.page_regions.get(key, [])
            else:
                recognized = {region.get("id"): region for region in recognized_regions}
                merged = [recognized.get(region.get("id"), region) for region in self.page_regions.get(key, [])]
            self._apply_dialogue_defaults(merged, key=key)
            self._enable_adaptive_typesetting(merged)
            for region in merged:
                self._promote_ocr_overlay(region)
            self.page_regions[key] = merged
            self._refresh_page_text(key)
        if self._active_key() in results:
            self.regions = self.page_regions[self._active_key()]
            self._render_page_layers(self._active_key())
        self._record_history()
        self.ai_panel.set_status("ocr", "OCR del capítulo listo · caché activa", self.ai_panel.STATUS_OK)

    @staticmethod
    def _promote_ocr_overlay(region: dict) -> None:
        """Expose a completed OCR result without waiting for layer selection."""
        source_text = OCRManager.normalize_cjk_text(str(region.get("text", "")))
        region["text"] = source_text
        region["ocr_completed"] = bool(source_text.strip())
        if not str(region.get("translation", "")).strip():
            region["translation"] = source_text
        if not str(region.get("applied_text", "")).strip():
            region["applied_text"] = str(region.get("translation") or source_text)

    @staticmethod
    def _sanitize_transport_markers(regions: list[dict]) -> bool:
        """Repair API labels already stored by older project versions."""
        changed = False
        for region in regions:
            source = OCRManager.normalize_cjk_text(str(region.get("text", "")))
            translated = TranslationManager._sanitize_translation(region.get("translation", ""))
            applied = TranslationManager._sanitize_translation(region.get("applied_text", ""))
            for field, value in (("text", source), ("translation", translated), ("applied_text", applied)):
                if str(region.get(field, "")) != value:
                    region[field] = value
                    changed = True
            if not translated:
                region["translation_completed"] = False
            region["ocr_completed"] = bool(source.strip())
        return changed

    def run_translation(self, scope: str = "page") -> None:
        if not self.regions:
            self._toast("Sin texto", "Ejecuta OCR o escribe texto en las cajas antes de traducir.")
            return
        provider, model = self.ai_panel.translate_configuration()
        api_key = self.credentials.get(provider)
        configuration = self.settings.data["translate"]
        profile_type, profile_name = self.active_font_type, self.active_font_profile
        if self.font_profiles.has_project(profile_type, profile_name):
            translation_profile = self.font_profiles.translation_profile(profile_type, profile_name)
        else:
            translation_profile = {
                "glossary": configuration.get("glossary", ""),
                "prompt": configuration.get("prompt", ""),
            }
        manga = bool(self.ai_panel.style_options("translation").get("manga_mode", False))
        candidates = self.regions
        if scope == "one":
            index = self.layers.current_index()
            if not 0 <= index < len(self.regions):
                self._toast("Selecciona una caja", "Haz clic en una caja antes de traducirla.")
                return
            candidates = [self.regions[index]]
        else:
            candidates = [
                region for region in candidates
                if not (
                    bool(region.get("translation_completed", False))
                    and str(region.get("translation", "")).strip()
                )
            ]
            if not candidates:
                self.ai_panel.set_status(
                    "translation", "Página ya traducida · sin solicitudes nuevas",
                    self.ai_panel.STATUS_OK,
                )
                self._toast("Traducción ya disponible", "Todas las cajas de esta página ya están traducidas.")
                return
        ordered = [region for region in TypographyManager.ordered(candidates, manga) if str(region.get("text", "")).strip()]
        if not ordered:
            self._toast("Sin OCR", "Ninguna capa contiene texto original para traducir.")
            return
        try:
            self.translator.validate(provider, api_key)
        except RuntimeError as error:
            self._toast("Traducción", str(error))
            return
        texts = [str(region["text"]) for region in ordered]
        context = self._recent_translation_context(limit=5000)

        def translate(progress, cancelled):
            result = self.translator.translate_with_context(
                texts, provider, model, api_key,
                configuration.get("source_language", "ZH"), configuration.get("target_language", "ES"),
                translation_profile.get("glossary", []), translation_profile.get("prompt", ""),
                context, progress, cancelled,
            )
            return {
                "translated": [
                    (region.get("id"), value)
                    for region, value in zip(ordered, result["translations"])
                ],
                "terms": result.get("terms", []),
                "profile_type": profile_type,
                "profile_name": profile_name,
            }

        self._start_task(translate, "Traducción", "Las traducciones quedaron editables por capa.", self._show_translations)

    def run_translation_all(self) -> None:
        self.run_translation("page")

    def run_translation_chapter(self) -> None:
        pages = [
            page for page in self.project.pages
            if any(
                str(region.get("text", "")).strip()
                and not (
                    bool(region.get("translation_completed", False))
                    and str(region.get("translation", "")).strip()
                )
                for region in self.page_regions.get(page_key(page), [])
            )
        ]
        if not pages:
            if any(
                str(region.get("text", "")).strip()
                for regions in self.page_regions.values() for region in regions
            ):
                self.ai_panel.set_status("translation", "Capítulo ya traducido", self.ai_panel.STATUS_OK)
                self._toast("Traducción ya disponible", "Todas las cajas del capítulo ya están traducidas.")
            else:
                self._toast("Sin OCR", "Ninguna página contiene texto original para traducir.")
            return
        provider, model = self.ai_panel.translate_configuration()
        api_key = self.credentials.get(provider)
        try:
            self.translator.validate(provider, api_key)
        except RuntimeError as error:
            self._toast("Traducción", str(error))
            return
        configuration = self.settings.data["translate"]
        profile_type, profile_name = self.active_font_type, self.active_font_profile
        translation_profile = (
            self.font_profiles.translation_profile(profile_type, profile_name)
            if self.font_profiles.has_project(profile_type, profile_name)
            else {"glossary": configuration.get("glossary", ""), "prompt": configuration.get("prompt", "")}
        )
        initial_context = self._recent_translation_context(limit=5000)

        def translate_chapter(progress, cancelled):
            translated_pages: dict[str, list[tuple[object, str]]] = {}
            learned_terms: list[dict] = []
            context_blocks = [initial_context] if initial_context else []
            for page_index, page in enumerate(pages):
                if cancelled():
                    return {}
                key = page_key(page)
                manga = bool(self.page_styles.get(key, {}).get("translation", {}).get("manga_mode", False))
                ordered = [
                    region for region in TypographyManager.ordered(self.page_regions.get(key, []), manga)
                    if str(region.get("text", "")).strip()
                    and not (
                        bool(region.get("translation_completed", False))
                        and str(region.get("translation", "")).strip()
                    )
                ]
                texts = [str(region["text"]) for region in ordered]
                offset, span = page_index * 100 / len(pages), 100 / len(pages)
                page_stage = f"Página {page_index + 1} de {len(pages)} · traducción"
                progress(int(offset), page_stage, key)
                result = self.translator.translate_with_context(
                    texts, provider, model, api_key,
                    configuration.get("source_language", "ZH"), configuration.get("target_language", "ES"),
                    translation_profile.get("glossary", []), translation_profile.get("prompt", ""),
                    "\n".join(context_blocks)[-5000:],
                    lambda value, base=offset, amount=span, label=page_stage, current=key: progress(
                        int(base + value * amount / 100), label, current,
                    ),
                    cancelled,
                )
                values = list(result.get("translations", []))
                translated_pages[key] = [(region.get("id"), value) for region, value in zip(ordered, values)]
                learned_terms.extend(result.get("terms", []))
                context_blocks.extend(f"{source} → {target}" for source, target in zip(texts, values))
            return {
                "pages": translated_pages, "terms": learned_terms,
                "profile_type": profile_type, "profile_name": profile_name,
            }

        self._start_task(
            translate_chapter, "Traducción del capítulo",
            f"Se traducirán {len(pages)} páginas conservando el contexto y glosario.",
            self._show_chapter_translations,
            stage="Traduciendo capítulo…",
        )

    def _show_chapter_translations(self, payload: dict) -> None:
        for key, translated in payload.get("pages", {}).items():
            values = dict(translated)
            for region in self.page_regions.get(key, []):
                if region.get("id") in values:
                    region["translation"] = values[region["id"]]
                    region["applied_text"] = values[region["id"]]
                    region["translation_completed"] = True
            self._enable_adaptive_typesetting(self.page_regions.get(key, []))
            self._refresh_page_text(key)
        terms = payload.get("terms", [])
        profile_type = str(payload.get("profile_type", ""))
        profile_name = str(payload.get("profile_name", ""))
        if terms and self.font_profiles.has_project(profile_type, profile_name):
            combined = self.font_profiles.merge_glossary(profile_type, profile_name, terms)
            self.ai_panel.set_translation_context(profile_name, len(combined))
        if self._active_key() in payload.get("pages", {}):
            self.regions = self.page_regions[self._active_key()]
            self._render_page_layers(self._active_key())
        self._record_history()

    def _recent_translation_context(self, limit: int = 5000) -> str:
        """Send a bounded sample of prior dialogue for chapter consistency."""
        blocks: list[str] = []
        active_key = self._active_key()
        for page in self.project.pages[: self.project.active_index + 1]:
            key = page_key(page)
            if key == active_key:
                continue
            for region in self.page_regions.get(key, []):
                source = str(region.get("text", "")).strip()
                translated = str(region.get("translation", "")).strip()
                if source and translated:
                    blocks.append(f"{source} → {translated}")
        return "\n".join(blocks[-40:])[-max(0, int(limit)):]

    def _show_translations(self, payload) -> None:
        if isinstance(payload, dict) and "translated" in payload:
            translated = payload.get("translated", [])
            new_terms = payload.get("terms", [])
            profile_type = str(payload.get("profile_type", ""))
            profile_name = str(payload.get("profile_name", ""))
        else:
            translated, new_terms, profile_type, profile_name = payload, [], "", ""
        values = dict(translated)
        for region in self.regions:
            if region.get("id") in values:
                region["translation"] = values[region["id"]]
                region["applied_text"] = values[region["id"]]
                region["translation_completed"] = True
        self._enable_adaptive_typesetting(self.regions)
        self._store_active_regions(self.regions)
        self._refresh_page_text(self._active_key())
        self._render_page_layers(self._active_key())
        self._sync_script_panel()
        self._record_history()
        if new_terms and self.font_profiles.has_project(profile_type, profile_name):
            combined = self.font_profiles.merge_glossary(profile_type, profile_name, new_terms)
            if profile_type == self.active_font_type and profile_name == self.active_font_profile:
                self.ai_panel.set_translation_context(profile_name, len(combined))
            self._toast(
                "Glosario actualizado",
                f"{len(new_terms)} término(s) revisados · {len(combined)} guardados en {profile_name}.",
            )

    def _show_regions(self, regions: list[dict]) -> None:
        self._apply_dialogue_defaults(regions)
        key = self._active_key()
        if key:
            self._consume_pending_text(key, regions)
        self.regions = regions
        self._store_active_regions(regions)
        self.canvas_shell.canvas.set_regions(regions)
        self.canvas_shell.canvas.add_text(
            [region.get("applied_text") or region.get("translation") or region.get("text", "") for region in regions],
            regions,
        )
        self.layers.set_regions(regions)
        self._refresh_page_text(key)
        self._record_history()

    def _sync_regions(self, regions: list[dict]) -> None:
        previous_current = self.layers.current_index()
        previous_current_id = (
            str(self.regions[previous_current].get("id", ""))
            if 0 <= previous_current < len(self.regions) else ""
        )
        previous_selected_ids = {
            str(self.regions[index].get("id", ""))
            for index in self.layers.selected_indices() if 0 <= index < len(self.regions)
        }
        selected_ids = [
            str(item.region.get("id", ""))
            for item in self.canvas_shell.canvas._regions if item.isSelected()
        ]
        self._mark_moved_regions_ocr_stale(self.regions, regions)
        previous_ids = {str(region.get("id", "")) for region in self.regions}
        surviving_ids = {str(region.get("id", "")) for region in regions}
        deleted_ids = previous_ids - surviving_ids
        active_deleted = bool(previous_current_id and previous_current_id in deleted_ids)
        if active_deleted:
            selected_ids = []
        elif not selected_ids:
            # Scene selection can be transiently empty while an unrelated box
            # is removed. Preserve surviving layers by stable ID, never row.
            selected_ids = [value for value in previous_selected_ids if value in surviving_ids]
        selected_id = selected_ids[-1] if selected_ids else ""
        previous_order = [str(region.get("id", "")) for region in self.regions]
        new_order = [str(region.get("id", "")) for region in regions]
        new_regions = [
            region for region in regions
            if str(region.get("id", "")) not in previous_ids
        ]
        self._apply_dialogue_defaults(new_regions)
        key = self._active_key()
        if key:
            self._consume_pending_text(key, new_regions)
        self.regions = regions
        self._store_active_regions(regions)
        self._refresh_page_text(key)
        # When the deleted box was the active selection, keep the canvas at
        # the same viewport position and leave selection empty. Choosing an
        # arbitrary surviving layer makes the view jump to that layer.
        self.layers.set_regions(
            regions, selected_id or None, keep_empty_selection=active_deleted or not bool(selected_id)
        )
        if active_deleted:
            self._clear_text_region_selection()
            QTimer.singleShot(0, self._clear_text_region_selection)
        text_values = [
            region.get("applied_text") or region.get("translation") or region.get("text", "")
            for region in regions
        ]
        if previous_order == new_order:
            # A move carries the already composed lines with the box. Only
            # resizing or changing content requests a new composition.
            if self.canvas_shell.canvas._last_region_change_kind != "move":
                changed = [
                    index for index, region in enumerate(regions)
                    if str(region.get("id", "")) in selected_ids
                ]
                self.canvas_shell.canvas.update_text_layers(
                    changed or list(range(len(regions))), text_values, regions,
                )
        else:
            self.canvas_shell.canvas.add_text(text_values, regions)
        if self.canvas_shell.canvas._last_region_change_kind in {"resize", "scale"} and selected_id:
            resized_index = next(
                (index for index, region in enumerate(regions) if str(region.get("id", "")) == selected_id),
                -1,
            )
            if resized_index >= 0:
                page_default = self.page_styles.get(key or "", {}).get("typography", DEFAULT_STYLE)
                resized = regions[resized_index]
                self.text_panel.set_layer(
                    resized_index,
                    {**page_default, **resized.get("style", {})},
                    str(resized.get("style_preset", "")),
                )
                self.text_panel.set_font_role(str(resized.get("font_role", "")))
        if self.tool_panel.currentWidget() is self.sfx_panel and selected_id:
            selected_index = next((i for i, region in enumerate(regions) if str(region.get("id", "")) == selected_id), -1)
            self.canvas_shell.canvas.show_sfx_nodes(selected_index)
        if new_regions and self.tool_panel.currentWidget() is self.script_panel:
            self._sync_script_panel()
        self._record_history()

    @staticmethod
    def _mark_moved_regions_ocr_stale(previous: list[dict], updated: list[dict]) -> int:
        """Mark OCR as stale when its source crop geometry has changed."""
        previous_by_id = {str(region.get("id", "")): region for region in previous}
        geometry = ("x", "y", "width", "height")
        changed = 0
        for region in updated:
            old = previous_by_id.get(str(region.get("id", "")))
            if old is None or not (region.get("ocr_checked") or str(region.get("text", "")).strip()):
                continue
            if any(int(old.get(key, 0)) != int(region.get(key, 0)) for key in geometry):
                region["ocr_stale"] = True
                changed += 1
        return changed

    def _store_active_regions(self, regions: list[dict]) -> None:
        if self.project.pages:
            page = self.project.active_page
            self.page_regions[page_key(page)] = regions

    def clean_text(self) -> None:
        if not self.project.pages or not self.regions:
            self._toast("No hay regiones", "Ejecuta la detección antes de usar LaMa.")
            return
        if self._retouch_layer_state(self._active_key())["automatic"].get("locked", False):
            self._toast("Limpieza bloqueada", "Desbloquea la capa Limpieza automática.")
            return
        self._prepare_clean_mask(self.regions)

    def clean_selected_region(self) -> None:
        if not self.project.pages or not self.regions:
            self._toast("No hay cajas", "Detecta o dibuja una caja antes de limpiar.")
            return
        if self._retouch_layer_state(self._active_key())["automatic"].get("locked", False):
            self._toast("Limpieza bloqueada", "Desbloquea la capa Limpieza automática.")
            return
        selected = [item.region for item in self.canvas_shell.canvas._regions if item.isSelected()]
        if not selected:
            self._toast("Selecciona una capa", "Haz clic en una caja del lienzo y pulsa Una capa.")
            return
        self._prepare_clean_mask(selected)

    def _prepare_clean_mask(self, regions: list[dict]) -> None:
        if self._pending_clean_plan is not None:
            self.cancel_clean_mask_preview(silent=True)
        self._clean_mask_request_id += 1
        request_id = self._clean_mask_request_id
        page = self.project.active_page
        key = self._active_key()
        source_states = copy.deepcopy(self._source_layer_states(key))
        self._start_task(
            lambda progress, cancelled: self.cleaner.prepare_masks(
                page.path, regions, progress, cancelled, source_states
            ),
            "Máscara de texto",
            "La máscara está lista para revisión.",
            lambda plan: self._show_mask_preview(
                key, page.path, {**plan, "_source_states": source_states}, request_id,
            ),
            stage="Detectando tinta para la máscara…",
        )

    def _show_mask_preview(
        self, key: str | None, image_path: Path, plan: dict,
        request_id: int | None = None,
    ) -> None:
        if request_id is not None and request_id != self._clean_mask_request_id:
            # The user cancelled or navigated while detection was running.
            # Silently discard this stale worker result.
            return
        if key is None or key != self._active_key() or not plan.get("entries"):
            self.cancel_clean_mask_preview(silent=True)
            self._toast("Sin máscara", "No se detectó tinta de texto dentro de las cajas.")
            return
        self._pending_clean_plan = {"key": key, "path": image_path, "plan": plan}
        self.canvas_shell.canvas.show_mask_preview(plan["entries"])
        self.ai_panel.set_mask_preview_active(True)
        solid = int(plan.get("solid_entries", 0))
        total = len(plan.get("entries", []))
        if solid and solid == total:
            source = "globo sólido · rango de color"
        elif solid:
            source = f"híbrida · {solid} globo(s) sólido(s)"
        else:
            source = "caché" if plan.get("cached") else "detector neural"
        self.ai_panel.set_status(
            "clean",
            f"Máscara lista · {source} · {plan.get('masked_pixels', 0):,} píxeles",
            self.ai_panel.STATUS_WARNING,
        )

    def _erase_from_mask_preview(self, stroke: dict) -> None:
        pending = self._pending_clean_plan
        if not pending or pending.get("key") != self._active_key():
            return
        mask = stroke.get("mask")
        if mask is not None and StrokeEngine.is_suspicious_connector(mask):
            self._toast("Trazo descartado", "El borrador salió del lienzo y no se aplicó.", "warning")
            return
        changed = StrokeEngine.erase_mask_entries(
            pending["plan"].get("entries", []), stroke
        )
        if changed:
            entries = pending["plan"].get("entries", [])
            pending["plan"]["masked_pixels"] = sum(
                int(np.count_nonzero(entry["mask"])) for entry in entries
            )
            self.canvas_shell.canvas.show_mask_preview(entries)
            self.ai_panel.set_status(
                "clean",
                f"Máscara editada · {pending['plan']['masked_pixels']:,} píxeles",
                self.ai_panel.STATUS_WARNING,
            )

    def apply_clean_mask_preview(self) -> None:
        pending = self._pending_clean_plan
        if not pending or pending.get("key") != self._active_key():
            self._toast("Sin máscara", "Primero genera y revisa la máscara OCR.")
            return
        image_path, plan = pending["path"], pending["plan"]
        keep_mask_brush = bool(plan.get("manual"))

        def finish(result: dict) -> None:
            self._show_cleaning(result)
            if keep_mask_brush:
                self._set_brush_mode("mask")

        # The overlay is only a review aid.  Remove it before inference starts
        # instead of waiting for LaMa's success callback; otherwise a slow or
        # failed inference leaves the red mask apparently stuck on the page.
        self.cancel_clean_mask_preview(silent=True)
        self._set_brush_mode(None)
        self._start_task(
            lambda progress, cancelled: self.cleaner.clean_prepared(
                image_path, plan, progress, cancelled, plan.get("_source_states")
            ),
            "Limpieza",
            "La máscara revisada se aplicó sin modificar las zonas borradas.",
            finish,
            stage="Aplicando limpieza según el fondo…",
        )

    def cancel_clean_mask_preview(self, silent: bool = False) -> None:
        self._clean_mask_request_id += 1
        self._pending_clean_plan = None
        self.canvas_shell.canvas.clear_mask_preview()
        self.ai_panel.set_mask_preview_active(False)
        if self.canvas_shell.canvas.brush_mode == "mask_erase":
            self._set_brush_mode(None)
        if not silent:
            self._toast("Máscara cancelada", "No se aplicó ninguna limpieza.")

    def clean_chapter(self) -> None:
        if not self.project.pages:
            self._toast("Abre un capítulo", "Carga imágenes antes de limpiar el capítulo.")
            return
        locked_pages = [
            page for page in self.project.pages
            if self._retouch_layer_state(page_key(page))["automatic"].get("locked", False)
        ]
        if locked_pages:
            self._toast("Capas bloqueadas", f"Desbloquea Limpieza automática en {len(locked_pages)} página(s).")
            return
        pages = [page for page in self.project.pages if page.path and self.page_regions.get(str(page.path))]
        if not pages:
            self._toast("No hay cajas en el capítulo", "Detecta o dibuja cajas en las páginas antes de ejecutar el lote.")
            return

        def clean_pages(progress, cancelled):
            results = {}
            for index, page in enumerate(pages, start=1):
                if cancelled():
                    return {}
                start = int((index - 1) * 100 / len(pages))
                span = max(1, int(100 / len(pages)))
                page_stage = f"Página {index} de {len(pages)} · limpieza"
                progress(start, page_stage, page_key(page))
                results[str(page.path)] = self.cleaner.clean(
                    page.path,
                    self.page_regions[str(page.path)],
                    lambda value, offset=start, amount=span, label=page_stage, current=page_key(page): progress(
                        min(99, offset + int(value * amount / 100)), label, current,
                    ),
                    cancelled,
                    copy.deepcopy(self._source_layer_states(page_key(page))),
                )
            progress(100)
            return results

        self._start_task(
            clean_pages, "Limpieza de capítulo",
            f"Se limpiaron temporalmente {len(pages)} página(s) con cajas.",
            self._show_chapter_cleaning, stage="Limpiando capítulo en proceso aislado…",
        )

    def _show_cleaning(self, result: dict) -> None:
        active_key = self._active_key()
        if active_key and self._retouch_layer_state(active_key)["automatic"].get("locked", False):
            self._toast("Limpieza bloqueada", "Desbloquea la capa Limpieza automática para aplicar el resultado.")
            return
        for patch in result.get("patches", []):
            patch.setdefault("id", uuid.uuid4().hex)
        review_entries = list(result.pop("review_entries", []))
        residual_passes = int(result.get("residual_passes", 0))
        residual_note = f" · {residual_passes} repaso(s) de residuos" if residual_passes else ""
        self.ai_panel.set_status(
            "clean",
            f"LaMa listo · {result.get('runtime', result.get('provider', 'ONNX Runtime'))}{residual_note}",
            self.ai_panel.STATUS_OK,
        )
        if self.project.pages:
            page = self.project.active_page
            key = str(page.path) if page.path else page.name
            self._quality_review_entries[key] = review_entries
            previous = self.clean_results.get(key, {"patches": []})
            targets = result.get("targets", [])

            def overlaps_target(patch: dict) -> bool:
                pixels = patch["pixels"]
                px1, py1 = int(patch["x"]), int(patch["y"])
                px2, py2 = px1 + pixels.shape[1], py1 + pixels.shape[0]
                for target in targets:
                    tx1, ty1 = int(target["x"]), int(target["y"])
                    tx2 = tx1 + int(target["width"])
                    ty2 = ty1 + int(target["height"])
                    if min(px2, tx2) > max(px1, tx1) and min(py2, ty2) > max(py1, ty1):
                        return True
                return False

            retained = [
                patch for patch in previous["patches"]
                if patch_layer(patch) != "automatic" or not overlaps_target(patch)
            ]
            merged = [*retained, *result["patches"]]
            self.clean_results[key] = {**result, "patches": merged}
            self.canvas_shell.canvas.apply_cleaning(merged, replace=True)
            self._refresh_image_layers(key)
        else:
            self.canvas_shell.canvas.apply_cleaning(result["patches"], replace=True)
        if not result["patches"]:
            self._toast("Sin tinta detectada", "Ajusta las cajas al texto antes de limpiar.")
        else:
            self._record_history()
            QTimer.singleShot(0, self.open_clean_quality)

    def open_clean_quality(self) -> None:
        key = self._active_key()
        result = self.clean_results.get(key or "")
        if not key or not result or not any(
            patch_layer(patch) == "automatic" for patch in result.get("patches", [])
        ):
            self._toast("Sin limpieza para revisar", "Aplica primero la limpieza automática a una o más cajas.")
            return
        original = self.canvas_shell.canvas.original_image()
        if original.isNull():
            return
        if self._quality_dialog is not None:
            self._quality_dialog.close()
        dialog = CleaningQualityDialog(
            original,
            {**result, "review_entries": self._quality_review_entries.get(key, [])},
            self,
        )
        dialog.accepted_patch.connect(self._quality_patch_accepted)
        dialog.repeat_requested.connect(self._quality_repeat_requested)
        dialog.correct_requested.connect(self._quality_correct_requested)
        dialog.finished.connect(lambda _value: setattr(self, "_quality_dialog", None))
        self._quality_dialog = dialog
        dialog.show()
        dialog.raise_()

    def _quality_patch_accepted(self, _patch_id: str) -> None:
        key = self._active_key()
        self._refresh_image_layers(key)
        self._history_timer.start()

    def _quality_repeat_requested(self, target: dict) -> None:
        if not target:
            return
        if self._quality_dialog is not None:
            self._quality_dialog.close()
        self._prepare_clean_mask([target])

    def _quality_correct_requested(self, payload: dict) -> None:
        if not self.project.pages:
            return
        target = dict(payload.get("target", {}))
        entries = copy.deepcopy(payload.get("entries", []))
        if not entries and target:
            key = self._active_key()
            result = self.clean_results.get(key or "", {})
            match = next((
                patch for patch in result.get("patches", [])
                if patch_layer(patch) == "automatic" and patch.get("target", {}) == target
            ), None)
            if match is not None and match.get("mask") is not None:
                entries = [{
                    "x": int(match.get("x", 0)), "y": int(match.get("y", 0)),
                    "mask": np.ascontiguousarray(match["mask"].copy()),
                    "target": target, "method": "lama", "fill_color": None,
                }]
        if not entries:
            self._toast("Sin máscara editable", "Repite la detección de esta caja para reconstruirla.")
            return
        if self._quality_dialog is not None:
            self._quality_dialog.close()
        page = self.project.active_page
        plan = {
            "entries": entries, "targets": [target], "manual": True,
            "masked_pixels": sum(int(np.count_nonzero(entry["mask"])) for entry in entries),
            "_source_states": copy.deepcopy(self._source_layer_states(self._active_key())),
        }
        self._show_mask_preview(self._active_key(), page.path, plan)

    def _show_chapter_cleaning(self, results: dict) -> None:
        for key, result in results.items():
            self._quality_review_entries[key] = list(result.pop("review_entries", []))
            for patch in result.get("patches", []):
                patch.setdefault("id", uuid.uuid4().hex)
        self.clean_results.update(results)
        if self.project.pages:
            page = self.project.active_page
            result = results.get(str(page.path) if page.path else page.name)
            if result:
                self.canvas_shell.canvas.apply_cleaning(result["patches"], replace=True)
            self._refresh_image_layers(self._active_key())
        if results:
            self._record_history()

    def apply_text(self) -> None:
        if self._history_timer.isActive():
            self._history_timer.stop()
            self._commit_debounced_state()
        text = self.inspector.text_editor.toPlainText().strip()
        index = self.layers.current_index()
        if not text or not 0 <= index < len(self.regions):
            self._toast("Texto o capa faltante", "Selecciona una capa y escribe su texto final.")
            return
        region = self.regions[index]
        region["translation"] = text
        region["applied_text"] = text
        if not region.get("style"):
            region["style"] = TypographyManager.normalized(
                self.page_styles.get(self._active_key() or "", {}).get("typography", DEFAULT_STYLE)
            )
        region["typeset_completed"] = True
        self._store_active_regions(self.regions)
        self._refresh_page_text(self._active_key())
        self.canvas_shell.canvas.add_text([r.get("applied_text", "") for r in self.regions], self.regions)
        self._record_history()
        self._toast("Texto aplicado", f"Se actualizó la capa {index + 1}.")

    def save_current(self) -> None:
        if not self.project.pages:
            self._toast("Sin imagen", "Abre un capítulo antes de guardar.")
            return
        page = self.project.active_page
        format_name = self.inspector.export_format()
        extension = ".jpg" if format_name == "JPG" else f".{format_name.lower()}"
        default_folder = page.path.parent / "Exportado" if page.path else Path.cwd()
        default_path = default_folder / f"{Path(page.name).stem}{extension}"
        filters = {"PNG": "PNG (*.png)", "JPG": "JPEG (*.jpg *.jpeg)", "WEBP": "WebP (*.webp)", "PSD": "Photoshop PSD (*.psd)"}
        filename, _ = QFileDialog.getSaveFileName(self, "Exportar página final", str(default_path), filters.get(format_name, "Todos (*.*)"))
        if not filename:
            return
        destination = Path(filename).with_suffix(extension)
        key = page_key(page)
        regions = copy.deepcopy(self.page_regions.get(key, []))
        clean_result = self.clean_results.get(key)
        styles = copy.deepcopy(self.page_styles.get(key, {}))
        watermark = self._watermark_for_page(page)
        if watermark:
            styles["watermark"] = watermark
        self._start_task(
            lambda progress, cancelled: self._export_one(destination, page, regions, clean_result, styles, format_name, progress, cancelled),
            "Exportación",
            f"Página guardada sin cajas y a {page.width} × {page.height} px.",
        )

    @staticmethod
    def _export_one(destination, page, regions, clean_result, styles, format_name, progress, cancelled):
        from core.export_manager import export_page
        if cancelled():
            return None
        progress(10)
        export_page(destination, page, regions, clean_result, styles, format_name)
        progress(100)
        return str(destination)

    def save_all(self) -> None:
        if not self.project.pages:
            self._toast("Sin capítulo", "Abre un capítulo antes de exportar.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Directorio para exportar el capítulo")
        if not folder:
            return
        format_name = self.inspector.export_format()
        extension = ".jpg" if format_name == "JPG" else f".{format_name.lower()}"
        pages = list(self.project.pages)
        active_index = self.project.active_index
        regions_by_page = copy.deepcopy(self.page_regions)
        clean_by_page = dict(self.clean_results)
        styles_by_page = copy.deepcopy(self.page_styles)
        watermark_settings = copy.deepcopy(self.watermark_settings)
        destination = Path(folder)

        def export_chapter(progress, cancelled):
            from core.export_manager import export_page
            exported = 0
            watermark_plan = chapter_watermark_settings([
                dict(name=page.name, width=page.width, height=page.height,
                     regions=regions_by_page.get(page_key(page), [])) for page in pages
            ], watermark_settings)
            for index, page in enumerate(pages, start=1):
                if cancelled():
                    break
                progress(
                    int((index - 1) * 100 / len(pages)),
                    f"Página {index} de {len(pages)} · exportación", page_key(page),
                )
                key = page_key(page)
                output = destination / f"{Path(page.name).stem}{extension}"
                page_styles = copy.deepcopy(styles_by_page.get(key, {}))
                if page.name in watermark_plan:
                    page_styles["watermark"] = watermark_plan[page.name]
                export_page(output, page, regions_by_page.get(key, []), clean_by_page.get(key), page_styles, format_name)
                exported += 1
                progress(int(index * 100 / len(pages)), f"Página {index} de {len(pages)} · exportación", key)
            return exported

        self._start_task(export_chapter, "Exportación del capítulo", f"Se exportarán {len(pages)} páginas a resolución original.")

    def save_project(self) -> None:
        if not self.project.pages:
            self._toast("Sin proyecto", "Abre un capítulo antes de guardar el proyecto.")
            return
        destination = self.project_file
        if destination is None:
            page = self.project.active_page
            default = (page.path.parent if page.path else Path.cwd()) / f"{(page.path.parent.name if page.path else 'proyecto')}.mseproj"
            filename, _ = QFileDialog.getSaveFileName(self, "Guardar proyecto editable", str(default), "Proyecto KuroPanel (*.mseproj)")
            if not filename:
                return
            destination = Path(filename)
            if destination.suffix.lower() != ".mseproj":
                destination = destination.with_suffix(".mseproj")
        try:
            snapshot = self._snapshot()
            if destination.exists():
                self.recovery.backup(destination)
            save_project_bundle(
                destination, self.project.pages, self.project.active_index,
                snapshot["page_regions"], snapshot["clean_results"], snapshot["page_texts"], snapshot["page_styles"],
                snapshot["style_presets"], self.active_font_profile, self.active_font_type,
                snapshot["effect_presets"],
                snapshot["watermark_settings"],
            )
        except Exception as error:
            self._toast("No se pudo guardar", str(error))
            return
        self.project_file = destination
        self._dirty = False
        self.recovery.clear_recovery()
        self.setWindowTitle(f"KuroPanel Studio — {destination.stem}")
        self._toast("Proyecto guardado", f"Cajas, textos, estilos y limpiezas: {destination.name}")

    def open_project(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Abrir proyecto editable", "", "Proyecto KuroPanel (*.mseproj)")
        if not filename:
            return
        source = Path(filename)
        try:
            state = load_project_bundle(source)
        except Exception as error:
            self._toast("No se pudo abrir", str(error))
            return
        if not state["pages"]:
            self._toast("Proyecto incompleto", "No se encontró ninguna imagen original del proyecto.")
            return
        self._restore_project_state(state)
        self.project_file = source
        self.setWindowTitle(f"KuroPanel Studio — {source.stem}")
        self.history.reset(self._snapshot())
        self._dirty = False
        self._update_history_actions()
        detail = f"{len(self.project.pages)} páginas restauradas."
        if state["missing"]:
            detail += f" No se encontraron {len(state['missing'])} originales."
        self._toast("Proyecto abierto", detail)

    def _restore_project_state(self, state: dict) -> None:
        saved_profile = state.get("project_profile", "")
        saved_type = state.get("project_profile_type", "")
        if not saved_type and saved_profile:
            matches = [
                project_type for project_type in self.font_profiles.project_types()
                if saved_profile in self.font_profiles.projects(project_type)
            ]
            saved_type = matches[0] if len(matches) == 1 else ""
        if self.font_profiles.has_project(saved_type, saved_profile):
            self.active_font_type = saved_type
            self.active_font_profile = saved_profile
            self.settings.update_profile(saved_type, saved_profile)
        self._restoring_state = True
        try:
            self._displayed_page_path = None
            self._page_view_states.clear()
            if self._prefetch_task is not None:
                self._prefetch_task.cancel()
                self._prefetch_task = None
            self.prefetch_pool.clear()
            self.images.clear()
            self._page_failures.clear()
            self.project.pages = state["pages"]
            self.project.active_index = state["active_index"]
            self._configure_psd_watcher(reset_signatures=True)
            self.page_regions = state["page_regions"]
            self.clean_results = state["clean_results"]
            self._quality_review_entries.clear()
            self.page_texts = state["page_texts"]
            self.page_styles = state["page_styles"]
            self.style_presets = state.get("style_presets", {})
            self._install_project_typography_defaults()
            restored_effects = state.get("effect_presets", {})
            self.effect_presets = {
                name: TypographyManager.normalized(style)
                for name, style in {**BUILTIN_EFFECT_PRESETS, **restored_effects}.items()
            }
            self.watermark_settings = normalized_watermark(state.get("watermark_settings"))
            if self.watermark_dialog is not None:
                self.watermark_dialog.set_settings(self.watermark_settings)
            self.effects_panel.set_presets(self.effect_presets)
            self.images_panel.refresh_pages(self.project.pages)
            self._refresh_page_statuses()
            self.images_panel.list.blockSignals(True)
            self.images_panel.list.setCurrentRow(self.project.active_index)
            self.images_panel.list.blockSignals(False)
            self._set_page(self.project.active_index)
        finally:
            self._restoring_state = False
        self._refresh_font_profiles()

    def _autosave(self) -> None:
        if not self.project.pages or not self._dirty or self._autosave_task is not None:
            return
        snapshot = self._snapshot()
        pages = list(self.project.pages)
        active_index = self.project.active_index
        active_type = self.active_font_type
        active_profile = self.active_font_profile
        regions = copy.deepcopy(snapshot["page_regions"])
        clean_results = {
            key: {**result, "patches": [dict(patch) for patch in result.get("patches", [])]}
            for key, result in snapshot["clean_results"].items()
        }
        texts = copy.deepcopy(snapshot["page_texts"])
        styles = copy.deepcopy(snapshot["page_styles"])
        presets = copy.deepcopy(snapshot["style_presets"])
        effect_presets = copy.deepcopy(snapshot["effect_presets"])
        watermark_settings = copy.deepcopy(snapshot["watermark_settings"])
        destination = self.recovery.autosave_path
        task = ModelTask(lambda progress, cancelled: save_project_bundle(
            destination, pages, active_index, regions, clean_results, texts, styles, presets,
            active_profile, active_type,
            effect_presets,
            watermark_settings,
        ) if not cancelled() else None)
        self._autosave_task = task
        task.signals.completed.connect(lambda _result: setattr(self, "_autosave_task", None))
        task.signals.failed.connect(lambda _error: setattr(self, "_autosave_task", None))
        self.io_pool.start(task)

    def _offer_recovery(self) -> None:
        if not self.recovery.has_recovery() or self.project.pages:
            return
        answer = QMessageBox.question(
            self, "Recuperar trabajo", "Se encontró un autoguardado de una sesión anterior. ¿Deseas recuperarlo?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            self.recovery.clear_recovery()
            return
        try:
            state = load_project_bundle(self.recovery.autosave_path)
            if not state["pages"]:
                raise RuntimeError("No se encontraron las imágenes originales.")
            self._restore_project_state(state)
            self.project_file = None
            self.history.reset(self._snapshot())
            self._dirty = True
            self._update_history_actions()
            self._toast("Trabajo recuperado", "Guárdalo como proyecto para conservar esta sesión.")
        except Exception as error:
            self._toast("No se pudo recuperar", str(error))

    def closeEvent(self, event) -> None:
        self._remember_displayed_page_view()
        if self._update_check_task is not None:
            self._update_check_task.cancel()
        if self._update_download_task is not None:
            self._update_download_task.cancel()
        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
        self.prefetch_pool.clear()
        self._psd_watch_debounce.stop()
        self._psd_poll_timer.stop()
        if self._psd_sync_task is not None:
            self._psd_sync_task.cancel()
        self._watermark_persist_timer.stop()
        self._persist_watermark_preferences()
        if self.project.pages and self._dirty:
            try:
                snapshot = self._snapshot()
                save_project_bundle(
                    self.recovery.autosave_path, self.project.pages, self.project.active_index,
                    snapshot["page_regions"], snapshot["clean_results"], snapshot["page_texts"],
                    snapshot["page_styles"], snapshot["style_presets"],
                    self.active_font_profile, self.active_font_type,
                    snapshot["effect_presets"],
                    snapshot["watermark_settings"],
                )
            except Exception:
                pass
        else:
            self.recovery.clear_recovery()
        self.cleaner.close()
        super().closeEvent(event)

    @staticmethod
    def _toast_kind(title: str, detail: str = "") -> str:
        normalized = f"{title} {detail}".casefold()
        if any(word in normalized for word in ("error", "falló", "no se pudo", "cerró inesperadamente")):
            return "error"
        if any(word in normalized for word in (
            "sin ", "no hay", "bloqueado", "faltante", "espera", "configura",
            "cancelad", "selecciona", "primero", "agrega",
        )):
            return "warning"
        if any(word in normalized for word in ("pincel", "cuentagotas", "editor", "proceso en curso")):
            return "info"
        return "success"

    def _layout_toasts(self) -> None:
        if not self._active_toasts:
            return
        # Notifications live over the right inspector, never over the page.
        # This is particularly important on long webtoons where a wide toast
        # previously hid the box being painted.
        anchor = self.inspector if hasattr(self, "inspector") else self
        origin = anchor.mapTo(self, QPoint(0, 0)) if anchor is not self else QPoint(0, 0)
        left = max(8, min(origin.x(), self.width() - 188))
        width = max(180, min(300, self.width() - left - 8))
        top = origin.y() + 8
        for toast in reversed(self._active_toasts):
            toast.setFixedWidth(width)
            toast.adjustSize()
            toast.move(self.width() - toast.width() - 8, top)
            top += toast.height() + 6

    def _remove_toast(self, toast: ToastNotification) -> None:
        if toast not in self._active_toasts:
            return
        self._active_toasts.remove(toast)
        toast.hide()
        toast.deleteLater()
        self._layout_toasts()

    def _toast(self, title: str, detail: str, kind: str | None = None) -> None:
        semantic_kind = kind or self._toast_kind(title, detail)
        key = (str(title), str(detail), str(semantic_kind))
        for current in list(self._active_toasts):
            if getattr(current, "message_key", None) == key:
                self._remove_toast(current)
        toast = ToastNotification(title, detail, self, semantic_kind)
        self._active_toasts.append(toast)
        while len(self._active_toasts) > 2:
            self._remove_toast(self._active_toasts[0])
        toast.adjustSize()
        toast.dismissed.connect(lambda current=toast: self._remove_toast(current))
        self._layout_toasts()
        toast.raise_()
        toast.show()
        duration = 5200 if semantic_kind == "error" else (3800 if semantic_kind == "warning" else 2800)
        QTimer.singleShot(duration, lambda current=toast: self._remove_toast(current))
