"""Subprocess presenter for non-TTY agent surfaces (gateway, scheduled, embedded).

Lives beside its process helpers in ``tools`` rather than in ``gateway``: every
non-TTY host needs it, and ``core.agent_harness`` may import neither package.
:func:`bootstrap.adapters.install_harness_adapters` registers it on the
harness port so the default agent can execute shell tools after process boot.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
from typing import Any

from tools.interactive_shell.shared import ExecutionPolicyResult
from tools.interactive_shell.subprocess import headless_subprocess_env

log = logging.getLogger(__name__)


class HeadlessSubprocessPresenter:
    """Minimal :class:`~tools.interactive_shell.subprocess.SubprocessPresenter` for gateway turns."""

    def __init__(self, session: Any, console: Any | None = None) -> None:
        self._session = session
        self._console = console

    @property
    def session(self) -> Any:
        return self._session

    def execution_allowed(
        self,
        policy: ExecutionPolicyResult,
        *,
        action_summary: str,
    ) -> bool:
        _ = (policy, action_summary)
        return True

    def print(self, message: str = "") -> None:
        if message:
            log.debug("subprocess: %s", message)

    def print_error(self, message: str) -> None:
        log.warning("subprocess error: %s", message)

    def print_highlight(self, message: str) -> None:
        log.info("subprocess: %s", message)

    def print_bold_command(self, display_command: str) -> None:
        log.info("$ %s", display_command)

    def print_command_output(self, text: str, *, style: str | None = None) -> None:
        _ = style
        if text.strip():
            log.debug("subprocess output: %s", text.strip())

    def print_plain(self, text: str) -> None:
        log.debug("subprocess: %s", text)

    def report_exception(self, exc: BaseException, *, context: str) -> None:
        log.exception("subprocess exception (%s)", context, exc_info=exc)

    def subprocess_env(self) -> dict[str, str]:
        return headless_subprocess_env()

    def start_task_output_streams(
        self,
        *,
        task: Any,
        proc: subprocess.Popen[Any],
        stdout_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
        stderr_capture: tempfile.SpooledTemporaryFile[bytes] | None = None,  # type: ignore[type-arg]
    ) -> list[threading.Thread]:
        _ = (task, proc, stdout_capture, stderr_capture)
        return []

    def join_task_output_streams(self, threads: list[threading.Thread]) -> None:
        _ = threads

    def start_background_cli_task(
        self,
        *,
        display_command: str,
        argv_list: list[str],
        timeout_seconds: int,
        kind: Any,
        use_pty: bool = False,
    ) -> Any:
        _ = (display_command, argv_list, timeout_seconds, kind, use_pty)
        raise RuntimeError("background CLI tasks are not supported in headless gateway mode")


def headless_subprocess_presenter_factory(
    session: Any,
    console: Any,
    confirm_fn: Any,
    is_tty: bool | None,
    action_already_listed: bool,
) -> HeadlessSubprocessPresenter:
    _ = (confirm_fn, is_tty, action_already_listed)
    return HeadlessSubprocessPresenter(session, console)


__all__ = ["HeadlessSubprocessPresenter", "headless_subprocess_presenter_factory"]
