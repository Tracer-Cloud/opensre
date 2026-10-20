"""Background task lifecycle for the interactive REPL runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from rich.console import Console

from core.domain.alerts import inbox as _alert_inbox
from infrastructure.scheduling.scheduler.background_service import (
    restart_stale_background_service,
)
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.startup.deferred_work import DeferredJob
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.alerts import drain_and_render_incoming
from surfaces.shared.integration_telemetry import capture_github_connection_snapshot

log = logging.getLogger(__name__)

#: Hands a one-shot thread job to whoever decides when it may start.
DeferThreadJob = Callable[[str, DeferredJob], None]


class BackgroundTaskPool:
    """Start background workers and drain their user-visible output."""

    def __init__(
        self,
        session: Session,
        state: ReplState,
        spinner: SpinnerState,
        inbox: _alert_inbox.AlertInbox | None,
        prompt_invalidator: Callable[[], None],
        *,
        defer_thread_job: DeferThreadJob | None = None,
    ) -> None:
        self.session = session
        self.state = state
        self.spinner = spinner
        self.inbox = inbox
        self.prompt_invalidator = prompt_invalidator
        self.tasks: list[tuple[str, asyncio.Task[None]]] = []
        self._defer_thread_job = defer_thread_job

    def start_all(
        self,
        processor_coro: Callable[[], Coroutine[Any, Any, None]],
    ) -> list[tuple[str, asyncio.Task[None]]]:
        self.tasks = [
            ("processor", asyncio.create_task(processor_coro())),
            ("alert watcher", asyncio.create_task(self._alert_watcher())),
            ("spinner ticker", asyncio.create_task(self._spinner_ticker())),
        ]
        session = self.session
        thread_jobs: tuple[tuple[str, Callable[[], None]], ...] = (
            # The scheduler check is cheap and finishes a deferred upgrade, so it
            # runs first: an exit during the snapshot must not skip it.
            ("scheduler build check", _restart_stale_scheduler),
            ("GitHub connection snapshot", lambda: capture_github_connection_snapshot(session)),
        )
        for label, job in thread_jobs:
            if self._defer_thread_job is not None:
                self._defer_thread_job(label, job)
            else:
                self.tasks.append((label, asyncio.create_task(asyncio.to_thread(job))))
        return self.tasks

    def drain_turn_start_output(self, console: Console) -> None:
        if self.inbox is not None:
            try:
                drain_and_render_incoming(self.session, console, self.inbox)
            except Exception as exc:
                log.warning("Error draining alerts at turn start: %s", exc)

    async def _alert_watcher(self) -> None:
        if self.inbox is None:
            return
        alert_console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        drain_and_render_incoming(self.session, alert_console, self.inbox)
        while not self.state.exit_requested:
            try:
                await asyncio.to_thread(self.inbox.pending_event.wait, timeout=1)
            except asyncio.CancelledError:
                return
            try:
                drain_and_render_incoming(self.session, alert_console, self.inbox)
            except Exception as exc:
                log.warning("Error draining incoming alerts: %s", exc)

    async def _spinner_ticker(self) -> None:
        # prompt_async's refresh_interval alone is not guaranteed to drive
        # visible prompt redraws while patch_stdout(raw=True) is active and
        # the LLM stream is writing rapidly. This task explicitly invalidates
        # the prompt at 100 ms intervals so the braille glyph cycles smoothly.
        tick_s = 0.1
        was_streaming = False
        while not self.state.exit_requested:
            try:
                await asyncio.sleep(tick_s)
            except asyncio.CancelledError:
                return
            streaming = self.spinner.streaming
            # Invalidate while streaming, plus one extra tick on the
            # streaming->idle edge so the prompt repaints without the stale
            # spinner/phase label instead of waiting for unrelated I/O.
            if streaming or was_streaming:
                self.prompt_invalidator()
            was_streaming = streaming


def _restart_stale_scheduler() -> None:
    """Finish a scheduler upgrade that was deferred while a run was in flight."""
    try:
        restart_stale_background_service()
    except Exception:
        log.debug("Scheduler build check failed", exc_info=True)
