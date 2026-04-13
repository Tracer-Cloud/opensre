"""Analytics transport for the OpenSRE CLI."""

from __future__ import annotations

import atexit
import contextlib
import importlib.metadata
import os
import platform
import queue
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

import httpx

from app.analytics.events import Event
from app.cli.wizard.store import get_store_path

_CONFIG_DIR = get_store_path().parent
_ANONYMOUS_ID_PATH = _CONFIG_DIR / "anonymous_id"
_FIRST_RUN_PATH = _CONFIG_DIR / "installed"

_POSTHOG_API_KEY = "phc_zutpVhmQw7oUmMkbawKNdYCKQWjpfASATtf5ywB75W2"
_POSTHOG_HOST = "https://us.i.posthog.com"

_QUEUE_SIZE = 128
_SEND_TIMEOUT = 2.0
_SHUTDOWN_WAIT = 1.0

PropertyValue: TypeAlias = str | bool  # noqa: UP040
Properties: TypeAlias = dict[str, PropertyValue]  # noqa: UP040


@dataclass(frozen=True, slots=True)
class _Envelope:
    event: str
    properties: Properties


def _is_opted_out() -> bool:
    return (
        os.getenv("OPENSRE_ANALYTICS_DISABLED", "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _get_or_create_anonymous_id() -> str:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if _ANONYMOUS_ID_PATH.exists():
            existing = _ANONYMOUS_ID_PATH.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        new_id = str(uuid.uuid4())
        _ANONYMOUS_ID_PATH.write_text(new_id, encoding="utf-8")
        return new_id
    except OSError:
        return str(uuid.uuid4())


def _touch_once(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return False
        path.touch()
        return True
    except OSError:
        return False


def _cli_version() -> str:
    try:
        return importlib.metadata.version("opensre")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


_BASE_PROPERTIES: Final[Properties] = {
    "cli_version": _cli_version(),
    "python_version": platform.python_version(),
    "os_family": platform.system().lower(),
    "os_version": platform.release(),
    "$process_person_profile": False,
}


class Analytics:
    def __init__(self) -> None:
        self._disabled = _is_opted_out()
        self._anonymous_id = _get_or_create_anonymous_id()
        self._queue: queue.Queue[_Envelope | None] = queue.Queue(maxsize=_QUEUE_SIZE)
        self._pending_lock = threading.Lock()
        self._pending = 0
        self._drained = threading.Event()
        self._drained.set()
        self._worker: threading.Thread | None = None
        self._shutdown = False

        if not self._disabled:
            atexit.register(self.shutdown)

    def capture(self, event: Event, properties: Properties | None = None) -> None:
        if self._disabled or self._shutdown:
            return
        envelope = _Envelope(
            event=event.value,
            properties=_BASE_PROPERTIES | (properties or {}),
        )
        self._ensure_worker()
        try:
            with self._pending_lock:
                self._pending += 1
                self._drained.clear()
            self._queue.put_nowait(envelope)
        except queue.Full:
            self._mark_done()

    def shutdown(self, *, flush: bool = True, timeout: float = _SHUTDOWN_WAIT) -> None:
        if self._disabled or self._shutdown:
            return
        self._shutdown = True
        self._ensure_worker()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        if flush and self._worker is not None:
            self._drained.wait(timeout=timeout)
            self._worker.join(timeout=timeout)

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        worker = threading.Thread(target=self._worker_loop, name="opensre-analytics", daemon=True)
        worker.start()
        self._worker = worker

    def _worker_loop(self) -> None:
        with httpx.Client(timeout=_SEND_TIMEOUT) as client:
            while True:
                item = self._queue.get()
                if item is None:
                    self._queue.task_done()
                    break
                try:
                    self._send(client, item)
                finally:
                    self._queue.task_done()
                    self._mark_done()
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    if item is not None:
                        self._send(client, item)
                finally:
                    self._queue.task_done()
                    self._mark_done()

    def _send(self, client: httpx.Client, item: _Envelope) -> None:
        payload = {
            "api_key": _POSTHOG_API_KEY,
            "event": item.event,
            "properties": {
                "distinct_id": self._anonymous_id,
                "$lib": "opensre-cli",
                **item.properties,
            },
        }
        with contextlib.suppress(Exception):
            client.post(f"{_POSTHOG_HOST}/capture/", json=payload).raise_for_status()

    def _mark_done(self) -> None:
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._drained.set()


_instance: Analytics | None = None


def get_analytics() -> Analytics:
    global _instance  # noqa: PLW0603
    if _instance is None:
        _instance = Analytics()
    return _instance


def shutdown_analytics(*, flush: bool = True) -> None:
    if _instance is not None:
        _instance.shutdown(flush=flush)


def mark_install_detected() -> None:
    with contextlib.suppress(OSError):
        _FIRST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FIRST_RUN_PATH.touch(exist_ok=True)


def capture_first_run_if_needed() -> None:
    if _touch_once(_FIRST_RUN_PATH):
        get_analytics().capture(Event.INSTALL_DETECTED)
