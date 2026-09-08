"""Interactive-shell adapters for :mod:`core.agent_harness.ports`: the console sink and error reporter.

Shared action-tool, reasoning-client and run-record providers live in
:mod:`core.agent_harness`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from core.agent_harness import OutputSink
from core.agent_harness.spi.defaults import DefaultErrorReporter
from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from surfaces.interactive_shell.ui import DIM

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.streaming import (
    StreamRenderResult,
    finish_deferred_closer,
    publish_full_response,
    render_response_header,
    stream_to_console,
    stream_to_console_state,
)
from surfaces.shared.error_handling.exception_reporting import report_exception


class ShellOutputSink:
    """:class:`core.agent_harness.ports.OutputSink` over a Rich console.

    The console may be rebound per turn (spinner-aware streaming console) so a
    long-lived :class:`~core.agent_harness.turns.action_driver.ActionTurnRunner`
    can keep the same sink object.
    """

    def __init__(self, console: Console, session: Session | None = None) -> None:
        self._console = console
        self._session = session
        self._stream_result: StreamRenderResult | None = None
        self._defer_want_me_to_closer = False

    def bind_console(self, console: Console) -> None:
        """Point subsequent output at ``console`` for the current turn."""
        self._console = console

    def _flush_pending_action_log(self) -> None:
        """Render the turn's buffered tool actions once, just before the reply.

        Flushing here (not per ReAct iteration) keeps same-kind calls that span
        iterations in one group, and keeps the log above the reply.
        """
        if self._session is None:
            return
        from surfaces.interactive_shell.ui.action_log import flush_action_log

        flush_action_log(self._console, self._session)

    def print(self, message: str = "") -> None:
        """Render harness text literally.

        Everything the harness prints here is data — tool output, model
        replies, skill bodies. A ``sed 's/\\]\\]>//'`` in a shell command
        reads to Rich as an unbalanced markup tag and raised ``MarkupError``,
        which took the whole turn down. Styled output goes through the
        ``render_*`` methods instead.
        """
        self._console.print(message, markup=False)

    def render_response_header(self, label: str) -> None:
        # No leading blank — Droid-dense turn stacking (user row → Ω reply).
        render_response_header(self._console, label)

    def render_plan_breakdown(self, breakdown: str) -> None:
        """Theme the post-execution checklist: primary steps, dim work notes."""
        from surfaces.interactive_shell.ui.task_plan import render_plan_breakdown

        render_plan_breakdown(self._console, breakdown)

    def render_error(self, message: str) -> None:
        self._console.print(f"[yellow]{escape(message)}[/]")
        # On a credit/billing wall, add the in-tool recovery hint.
        if CREDIT_EXHAUSTED_MARKER in message:
            self._console.print("[dim]Run /model to switch to another provider.[/]")
            self._console.print(
                "[dim]Or run /auth login <provider> to re-authenticate "
                "or add a different provider.[/]"
            )

    def set_tool_status(self, status: str) -> None:
        """Show live progress for the running turn.

        A chat placeholder holds one status line and rewrites it. A terminal has
        no placeholder to rewrite, so each status is its own dim line.
        """
        if status:
            self._console.print(f"[{DIM}]{escape(status)}[/]")

    def finalize(self, answer: str) -> None:
        """Do nothing: chat fills a placeholder here, and the terminal has none.

        Terminal output lands as it happens, so by the time the host finalizes
        an unstreamed turn there is nothing left to show.
        """
        _ = answer

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        # All tool iterations are done by the time the reply streams: flush the
        # buffered action log now so it sits above the reply, grouped as one.
        self._flush_pending_action_log()
        self._defer_want_me_to_closer = defer_want_me_to_closer
        if defer_want_me_to_closer:
            stream_result = stream_to_console_state(
                self._console,
                label=label,
                chunks=iter(chunks),
                suppress_if_starts_with=suppress_if_starts_with,
                defer_want_me_to_closer=True,
            )
            self._stream_result = stream_result
            return stream_result.text
        self._stream_result = None
        return stream_to_console(
            self._console,
            label=label,
            chunks=iter(chunks),
            suppress_if_starts_with=suppress_if_starts_with,
        )

    def finish_streamed_response(self, answer: str) -> None:
        """Flush a deferred / rewritten Want-me-to closer after gather normalize."""
        stream_result = self._stream_result
        defer = self._defer_want_me_to_closer
        self._stream_result = None
        self._defer_want_me_to_closer = False
        if not defer or stream_result is None:
            return
        if not stream_result.deferred_closer and answer == stream_result.text:
            return
        # Non-TTY deferred holds the entire answer until normalize.
        if not self._console.is_terminal and stream_result.deferred_closer:
            publish_full_response(self._console, answer)
            return
        finish_deferred_closer(
            self._console,
            answer,
            footer_elapsed_s=stream_result.footer_elapsed_s,
            footer_total_bytes=stream_result.footer_total_bytes,
        )


def resolve_output_sink(
    console: Console, output: OutputSink | None, session: Session | None = None
) -> OutputSink:
    """Return the caller's sink, or a shell sink bound to ``console`` and ``session``."""
    if output is not None:
        return output
    return ShellOutputSink(console, session)


class ShellErrorReporter:
    """:class:`~core.agent_harness.ports.ErrorReporter` for the REPL: debug log plus Sentry.

    Swallowed boundary exceptions (gather, answer, tool errors) are logged like
    the default reporter and forwarded to :func:`report_exception`, which
    filters expected failures before capturing.
    """

    def __init__(self) -> None:
        self._log = DefaultErrorReporter()

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        self._log.report(exc, context=context, expected=expected)
        report_exception(exc, context=context, expected=expected)


__all__ = ["ShellErrorReporter", "ShellOutputSink", "resolve_output_sink"]
