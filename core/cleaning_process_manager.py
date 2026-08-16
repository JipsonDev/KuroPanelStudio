"""Persistent out-of-process proxy for the ONNX cleaning engine.

ONNX Runtime holds Python's GIL for much of ``InferenceSession`` creation on
Windows. A QRunnable therefore still freezes Qt while the 206 MB LaMa graph is
loaded. Keeping the real CleaningManager in a spawned process isolates that
cost and also preserves its sessions and mask cache between operations.
"""
from __future__ import annotations

import multiprocessing as mp
import threading
import traceback
from time import monotonic
from collections.abc import Callable
from pathlib import Path


def _cleaning_process_main(connection, cancel_event, models_root: str) -> None:
    """Own the heavy ONNX sessions for the lifetime of the application."""
    from core.cleaning_manager import CleaningManager

    engine = CleaningManager(Path(models_root))
    try:
        while True:
            request = connection.recv()
            if request.get("command") == "shutdown":
                break
            request_id = int(request["id"])
            command = str(request["command"])
            args = tuple(request.get("args", ()))
            engine.set_aggressiveness(int(request.get("aggressiveness", 90)))
            engine.set_device_mode(str(request.get("device_mode", "auto")))
            engine.set_resource_profile(str(request.get("resource_profile", "balanced")))

            def progress(value: int) -> None:
                connection.send(("progress", request_id, max(0, min(100, int(value)))))

            try:
                if command == "warm_up":
                    result = engine.warm_up(progress, cancel_event.is_set)
                elif command == "prepare_masks":
                    result = engine.prepare_masks(
                        args[0], args[1], progress, cancel_event.is_set,
                        args[2] if len(args) > 2 else None,
                    )
                elif command == "clean_prepared":
                    result = engine.clean_prepared(
                        args[0], args[1], progress, cancel_event.is_set,
                        args[2] if len(args) > 2 else None,
                    )
                elif command == "clean":
                    result = engine.clean(
                        args[0], args[1], progress, cancel_event.is_set,
                        args[2] if len(args) > 2 else None,
                    )
                elif command == "clean_mask":
                    result = engine.clean_mask(*args)
                    progress(100)
                elif command == "unload":
                    result = engine.unload_models()
                    progress(100)
                else:
                    raise ValueError(f"Comando de limpieza desconocido: {command}")
                connection.send((
                    "result", request_id, result,
                    {"provider_name": engine.provider_name, "runtime_label": engine.runtime_label},
                ))
            except BaseException as error:  # keep worker alive after recoverable model errors
                connection.send(("error", request_id, str(error), traceback.format_exc()))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class CleaningProcessManager:
    """Drop-in public API that executes CleaningManager in one warm process."""

    def __init__(self, models_root: Path) -> None:
        self.models_root = Path(models_root).resolve()
        self.aggressiveness = 90
        self.provider_name = "Modelo no cargado"
        self.runtime_label = "Proceso aislado sin iniciar"
        self._context = mp.get_context("spawn")
        self._process = None
        self._connection = None
        self._cancel_event = None
        self._request_id = 0
        self._command_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self.device_mode = "auto"
        self.resource_profile = "balanced"
        self.last_used = monotonic()

    def set_aggressiveness(self, value: int) -> None:
        self.aggressiveness = max(1, min(100, int(value)))

    def set_device_mode(self, mode: str) -> None:
        from core.performance_manager import normalize_device_mode
        self.device_mode = normalize_device_mode(mode)

    def set_resource_policy(self, policy) -> None:
        self.resource_profile = str(getattr(policy, "name", "balanced"))

    def _ensure_process(self) -> None:
        with self._state_lock:
            if self._process is not None and self._process.is_alive():
                return
            self._dispose_handles()
            parent, child = self._context.Pipe(duplex=True)
            cancel_event = self._context.Event()
            process = self._context.Process(
                target=_cleaning_process_main,
                args=(child, cancel_event, str(self.models_root)),
                name="Manhua-LaMa-Worker",
                daemon=True,
            )
            process.start()
            child.close()
            self._connection = parent
            self._cancel_event = cancel_event
            self._process = process

    def _dispose_handles(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except (OSError, EOFError):
                pass
        self._process = None
        self._cancel_event = None

    def _call(
        self,
        command: str,
        args: tuple,
        progress: Callable[[int], None],
        cancelled: Callable[[], bool],
    ):
        with self._command_lock:
            self._ensure_process()
            process = self._process
            connection = self._connection
            cancel_event = self._cancel_event
            if process is None or connection is None or cancel_event is None:
                raise RuntimeError("No se pudo iniciar el proceso aislado de LaMa.")
            cancel_event.clear()
            self._request_id += 1
            request_id = self._request_id
            try:
                connection.send({
                    "id": request_id,
                    "command": command,
                    "args": args,
                    "aggressiveness": self.aggressiveness,
                    "device_mode": self.device_mode,
                    "resource_profile": self.resource_profile,
                })
                cancellation_sent = False
                while True:
                    if cancelled() and not cancellation_sent:
                        cancel_event.set()
                        cancellation_sent = True
                    if connection.poll(0.070 if self.resource_profile == "low" else 0.035):
                        message = connection.recv()
                        if len(message) < 2 or int(message[1]) != request_id:
                            continue
                        kind = message[0]
                        if kind == "progress":
                            progress(int(message[2]))
                            continue
                        if kind == "error":
                            raise RuntimeError(str(message[2]))
                        if kind == "result":
                            metadata = dict(message[3] or {})
                            self.provider_name = str(metadata.get("provider_name", self.provider_name))
                            self.runtime_label = str(metadata.get("runtime_label", self.runtime_label))
                            self.last_used = monotonic()
                            return message[2]
                    if not process.is_alive():
                        self._dispose_handles()
                        raise RuntimeError(
                            "El proceso de LaMa se cerró inesperadamente. La próxima operación lo reiniciará."
                        )
            except (EOFError, BrokenPipeError, OSError) as error:
                self._dispose_handles()
                raise RuntimeError(
                    "Se perdió la comunicación con LaMa. La próxima operación reiniciará el modelo."
                ) from error
            finally:
                try:
                    cancel_event.clear()
                except (OSError, ValueError):
                    pass

    def warm_up(
        self,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        return str(self._call("warm_up", (), progress or (lambda _value: None), cancelled or (lambda: False)))

    def prepare_masks(self, image_path, regions, progress, cancelled, source_states=None) -> dict:
        return self._call("prepare_masks", (image_path, regions, source_states), progress, cancelled)

    def clean_prepared(self, image_path, plan, progress, cancelled, source_states=None) -> dict:
        return self._call("clean_prepared", (image_path, plan, source_states), progress, cancelled)

    def clean(self, image_path, regions, progress, cancelled, source_states=None) -> dict:
        return self._call("clean", (image_path, regions, source_states), progress, cancelled)

    def clean_mask(self, rgb, mask) -> object:
        return self._call("clean_mask", (rgb, mask), lambda _value: None, lambda: False)

    def unload(self) -> bool:
        if self._process is None or not self._process.is_alive():
            return False
        return bool(self._call("unload", (), lambda _value: None, lambda: False))

    def close(self) -> None:
        """Terminate promptly; closing the app must never wait for model loading."""
        with self._state_lock:
            process = self._process
            connection = self._connection
            if process is not None and process.is_alive():
                try:
                    if connection is not None:
                        connection.send({"command": "shutdown"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
                process.join(timeout=0.15)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.5)
            self._dispose_handles()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
