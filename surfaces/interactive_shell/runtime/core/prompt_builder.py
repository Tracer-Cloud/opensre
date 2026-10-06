"""Prompt lifecycle and rendering glue for the interactive REPL loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from rich.console import Console

from surfaces.interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import input_prompt
from surfaces.interactive_shell.ui.ci_fix_status import prompt_status_ansi
from surfaces.interactive_shell.ui.hooks import (
    install_confirmation_key_bindings,
    install_output_expand_key_bindings,
    install_plan_expand_key_bindings,
)
from surfaces.interactive_shell.ui.hooks.output_expand import expand_collapsed_output
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    build_cancel_key_bindings,
    install_session_key_bindings,
)
from surfaces.interactive_shell.ui.input_prompt.refresh import wire_prompt_refresh
from surfaces.interactive_shell.ui.input_prompt.style import refresh_prompt_theme
from surfaces.interactive_shell.ui.prompt_visibility import typing_box_hidden
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region
from surfaces.interactive_shell.ui.transcript_view import (
    TranscriptControl,
    TranscriptStore,
    render_for_scrollback,
)
from surfaces.shared.terminal.components.cpr_stdin import (
    drain_stale_cpr_bytes,
    strip_cpr_sequences,
)

# Brief pause so a CPR reply still in flight lands in the stdin buffer before the
# non-blocking drain runs; without it the reply leaks into this prompt as literal bytes.
_CPR_SETTLE_SECONDS = 0.05


def _plan_overlay_visible(session: Session, state: ReplState) -> bool:
    """True when Ctrl+P should expand a pinned checklist (local or gateway)."""
    local = session.task_plan
    gateway = state.gateway_plan
    return bool((local is not None and local.steps) or (gateway is not None and gateway.steps))


class PromptBuilder:
    """Own prompt-toolkit setup, prompt rendering, and prompt redraw hooks."""

    def __init__(
        self,
        session: Session,
        state: ReplState,
        spinner: SpinnerState,
        pt_session: PromptSession[str] | None = None,
        transcript: TranscriptStore | None = None,
    ) -> None:
        self.session = session
        self.state = state
        self.spinner = spinner
        self.pt_session = pt_session
        self.pt_app: Application[str] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._invalidate_prompt: Callable[[], None] | None = None
        self._submitted: asyncio.Queue[str] = asyncio.Queue()
        self._prompt_task: asyncio.Task[str] | None = None
        self._expand_in_flight: bool = False
        # True once this builder frames its own session (which installs the
        # status row). An injected ``pt_session`` keeps whatever layout it
        # arrived with, so its chrome has to stay in the message region.
        self._status_row_installed: bool = False
        self.transcript = transcript or TranscriptStore()
        # Set only when this builder makes the session: the full-screen view.
        self.transcript_view: TranscriptControl | None = None

    def _composer_hidden(self) -> bool:
        """True while structured input (confirmation, menus) owns the keyboard.

        The prompt reads this to collapse the free-text composer box so it does
        not sit under the pending choice.
        """
        return typing_box_hidden(self.session, self.state)

    def _status_line(self) -> str:
        """Permission/CI chrome for the fixed row under the composer.

        Stays DIM while a turn streams so the spinner above keeps the accent.
        """
        return strip_cpr_sequences(prompt_status_ansi(self.session, quiet=self.spinner.streaming))

    def setup(self) -> None:
        if self.pt_session is None:
            self.transcript_view = TranscriptControl(self.transcript)
            self.pt_session = input_prompt.build_prompt_session(
                self.session,
                hide_composer=self._composer_hidden,
                transcript=self.transcript_view,
                status_line=self._status_line,
            )
            self._status_row_installed = True
            self.session.terminal.prompt_history_backend = self.pt_session.history

        cancel_kb = build_cancel_key_bindings(self.state)
        install_session_key_bindings(self.pt_session, cancel_kb)

        self.pt_app = self.pt_session.app
        if self.transcript_view is not None:
            install_session_key_bindings(
                self.pt_session, _transcript_scroll_bindings(self.transcript_view)
            )
        self.pt_session.default_buffer.accept_handler = self._accept_prompt_buffer
        # While the Yes/No gate owns the keyboard the composer is hidden but its
        # buffer still receives unbound keys unless it is read-only. Lock it so
        # typeahead cannot accumulate under the overlay and submit after close.
        self.pt_session.default_buffer.read_only = Condition(self.state.is_awaiting_confirmation)
        self.loop = asyncio.get_running_loop()
        self.session.terminal.prompt_app = self.pt_app
        self.session.terminal.main_loop = self.loop
        self.state.bind_loop(self.loop)
        self._invalidate_prompt = wire_prompt_refresh(self.session, self.pt_app, self.loop)
        if self.transcript_view is not None:
            self.transcript.on_change = self._invalidate_prompt
            self.session.terminal.transcript = self.transcript
        # Arrow-navigable Yes/No for the execution-confirmation gate: ↑/↓ move the
        # selection, Enter (or a/b/y/n) delivers it. Installed after the redraw
        # hook so a selection change repaints immediately.
        confirm_kb = install_confirmation_key_bindings(self.state, self._invalidate_prompt)
        install_session_key_bindings(self.pt_session, confirm_kb)
        # Ctrl+P (and Alt/Option+P) expands/collapses the pinned plan while one
        # is on screen.
        plan_kb = install_plan_expand_key_bindings(
            self.state,
            lambda: _plan_overlay_visible(self.session, self.state),
            self._invalidate_prompt,
        )
        install_session_key_bindings(self.pt_session, plan_kb)
        output_kb = install_output_expand_key_bindings(
            self.session.terminal.has_collapsed_tool_output,
            self.session.terminal.next_collapsed_output_for_expand,
            self._expand_collapsed_output,
        )
        install_session_key_bindings(self.pt_session, output_kb)

    def _expand_collapsed_output(self, text: str) -> None:
        """Suspend the prompt and expand the next folded tool result (Ctrl+O).

        Single-flight: ignore further Ctrl+O while an expand is still running
        so key-mash cannot interleave ``run_in_terminal`` sessions.
        """
        if self._expand_in_flight:
            return
        self._expand_in_flight = True

        async def _run() -> None:
            try:
                await run_in_terminal(lambda: expand_collapsed_output(text), in_executor=False)
            finally:
                self._expand_in_flight = False

        if self.pt_app is not None and self.pt_app.is_running:
            self.pt_app.create_background_task(_run())
            return
        try:
            expand_collapsed_output(text)
        finally:
            self._expand_in_flight = False

    @property
    def invalidate_prompt(self) -> Callable[[], None]:
        if self._invalidate_prompt is None:
            raise RuntimeError("PromptBuilder.setup() must run before prompt invalidation")
        return self._invalidate_prompt

    def request_exit(self) -> None:
        if self.pt_app is None or self.loop is None:
            self.state.request_exit()
            return

        self.state.request_exit()

        def _exit_prompt_app(attempts_left: int = 5) -> None:
            if self.pt_app is not None and self.pt_app.is_running:
                self.pt_app.exit(result="")
                return
            if attempts_left > 0 and self.loop is not None:
                self.loop.call_later(0.02, _exit_prompt_app, attempts_left - 1)

        self.loop.call_soon_threadsafe(_exit_prompt_app)

    def message_with_spinner(self) -> ANSI:
        # Only a session this builder framed carries the status row under its
        # composer; an injected one keeps the chrome in the message region.
        return render_prompt_region(
            self.session,
            self.state,
            self.spinner,
            status_line=None if self._status_row_installed else self._status_line,
        )

    def _accept_prompt_buffer(self, buffer: Buffer) -> bool:
        """Queue accepted text while keeping the prompt application alive."""
        # Enter during confirmation is handled by the Yes/No bindings; never
        # treat residual buffer text as a submitted message while the gate is up.
        if self.state.is_awaiting_confirmation():
            return True
        if self.transcript_view is not None:
            self.transcript_view.scroll_to_bottom()
        self._submitted.put_nowait(buffer.text)
        return False

    def _start_prompt_if_needed(self) -> asyncio.Task[str]:
        if self.pt_session is None:
            raise RuntimeError("PromptBuilder.setup() must run before reading prompts")
        task = self._prompt_task
        if task is None:
            task = asyncio.create_task(
                self.pt_session.prompt_async(
                    message=self.message_with_spinner,
                    bottom_toolbar=self.spinner.toolbar_ansi,
                    refresh_interval=PROMPT_REFRESH_INTERVAL_S,
                    placeholder=self._prompt_placeholder,
                )
            )
            self._prompt_task = task
        return task

    def _restore_terminal_autowrap(self) -> None:
        """Restore the terminal mode owned by the live prompt and catch up scrollback."""
        if self.pt_app is None:
            return
        self.pt_app.output.enable_autowrap()
        self.pt_app.output.flush()
        self._write_transcript_to_scrollback()

    def _write_transcript_to_scrollback(self) -> None:
        """Append output shown only in the full-screen view to terminal scrollback.

        Runs once the app has left the alternate screen, so pickers and the
        shell after exit see the conversation in ordinary scrollback.
        """
        if self.transcript_view is None or self.pt_app is None:
            return
        entries = self.transcript.take_unflushed()
        if not entries:
            return
        output = self.pt_app.output
        output.write_raw(render_for_scrollback(entries, output.get_size().columns))
        output.flush()

    async def suspend(self) -> None:
        """Release stdin while an exclusive picker or wizard is running."""
        task = self._prompt_task
        if task is None:
            self._restore_terminal_autowrap()
            return
        try:
            if not task.done() and self.pt_app is not None and self.pt_app.is_running:
                self.pt_app.exit(result="")
            await asyncio.gather(task, return_exceptions=True)
            if self._prompt_task is task:
                self._prompt_task = None
        finally:
            self._restore_terminal_autowrap()

    async def close(self) -> None:
        """Stop the persistent prompt application during shell shutdown."""
        task = self._prompt_task
        self._prompt_task = None
        if task is None:
            self._restore_terminal_autowrap()
            return
        try:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._restore_terminal_autowrap()

    async def read_prompt_text(self) -> str:
        if self.pt_session is None:
            raise RuntimeError("PromptBuilder.setup() must run before reading prompts")

        if self.session.terminal.pending_theme_refresh:
            self.session.terminal.pending_theme_refresh = False
            refresh_prompt_theme(self.session)
        if self._prompt_task is None or self._prompt_task.done():
            # Only before (re)starting the prompt: a live prompt app is reading
            # stdin, and draining under it splits the sequences it is parsing.
            await asyncio.sleep(_CPR_SETTLE_SECONDS)
            drain_stale_cpr_bytes()

        prefilled = self.session.terminal.pop_pending_prompt_default()
        if prefilled and self.session.terminal.pop_pending_autosubmit():
            # Same paint path as Enter: mark so ``render_submitted_prompt`` can
            # label ``/goal`` work turns distinctly from the ``/goal set`` slash.
            # A plain auto prompt is submitted exactly as typed input instead.
            plain = self.session.terminal.pop_pending_plain_turn()
            self.session.terminal.last_input_autosubmitted = not plain
            return prefilled

        if prefilled:
            self.pt_session.default_buffer.text = prefilled

        prompt_task = self._start_prompt_if_needed()
        submitted = asyncio.create_task(self._submitted.get())
        try:
            done, _pending = await asyncio.wait(
                {prompt_task, submitted},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if prompt_task in done:
                submitted.cancel()
                await asyncio.gather(submitted, return_exceptions=True)
                self._prompt_task = None
                # The app left the screen (Ctrl-D, Ctrl-C): put the conversation
                # in scrollback before anything else prints there.
                self._write_transcript_to_scrollback()
                return await prompt_task
            return submitted.result()
        except BaseException:
            submitted.cancel()
            await asyncio.gather(submitted, return_exceptions=True)
            raise

    def _prompt_placeholder(self) -> FormattedText:
        # Options menus / confirmation own the keyboard — suppress free-text ghost.
        if typing_box_hidden(self.session, self.state):
            return FormattedText()
        return prompt_rendering.resolve_prompt_placeholder(self.session)

    def render_submitted_prompt(self, console: Console, text: str) -> None:
        # The between-turns blank row is placed inside ``render_submitted_prompt``
        # itself: the handoff-answer marker must hug the reply it answers, so the
        # gap falls after the marker rather than blanket-above the whole turn.
        prompt_rendering.render_submitted_prompt(console, self.session, text)


def _transcript_scroll_bindings(view: TranscriptControl) -> KeyBindings:
    """PageUp/PageDown page through the full-screen transcript."""
    bindings = KeyBindings()

    @bindings.add(Keys.PageUp, filter=~has_completions, eager=True)
    def _page_up(event: KeyPressEvent) -> None:
        view.page_up()
        event.app.invalidate()

    @bindings.add(Keys.PageDown, filter=~has_completions, eager=True)
    def _page_down(event: KeyPressEvent) -> None:
        view.page_down()
        event.app.invalidate()

    return bindings
