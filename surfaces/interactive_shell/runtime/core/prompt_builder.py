"""Prompt lifecycle and rendering glue for the interactive REPL loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText
from rich.console import Console

from surfaces.interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import input_prompt
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
from surfaces.interactive_shell.ui.input_prompt.resize import install_shrink_resize_guard
from surfaces.interactive_shell.ui.input_prompt.style import refresh_prompt_theme
from surfaces.interactive_shell.ui.prompt_visibility import typing_box_hidden
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region
from surfaces.shared.terminal.banner import render_launch_banner
from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes
from surfaces.shared.terminal.components.rendering import repl_clear_screen

# Brief pause so a CPR reply still in flight lands in the stdin buffer before the
# non-blocking drain runs; without it the reply leaks into this prompt as literal bytes.
_CPR_SETTLE_SECONDS = 0.05


class PromptBuilder:
    """Own prompt-toolkit setup, prompt rendering, and prompt redraw hooks."""

    def __init__(
        self,
        session: Session,
        state: ReplState,
        spinner: SpinnerState,
        pt_session: PromptSession[str] | None = None,
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

    def _composer_hidden(self) -> bool:
        """True while structured input (confirmation, menus) owns the keyboard.

        The prompt reads this to collapse the free-text composer box so it does
        not sit under the pending choice.
        """
        return typing_box_hidden(self.session, self.state)

    def setup(self) -> None:
        if self.pt_session is None:
            self.pt_session = input_prompt.build_prompt_session(
                self.session,
                hide_composer=self._composer_hidden,
            )
            self.session.terminal.prompt_history_backend = self.pt_session.history

        cancel_kb = build_cancel_key_bindings(self.state)
        install_session_key_bindings(self.pt_session, cancel_kb)

        self.pt_app = self.pt_session.app
        install_shrink_resize_guard(self.pt_app, rerender_banner=self._rerender_banner_if_idle)
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
        # Arrow-navigable Yes/No for the execution-confirmation gate: ↑/↓ move the
        # selection, Enter (or a/b/y/n) delivers it. Installed after the redraw
        # hook so a selection change repaints immediately.
        confirm_kb = install_confirmation_key_bindings(self.state, self._invalidate_prompt)
        install_session_key_bindings(self.pt_session, confirm_kb)
        # Ctrl+P (and Alt/Option+P) expands/collapses the pinned plan while one
        # is on screen.
        plan_kb = install_plan_expand_key_bindings(
            self.state,
            lambda: self.session.task_plan is not None and bool(self.session.task_plan.steps),
            self._invalidate_prompt,
        )
        install_session_key_bindings(self.pt_session, plan_kb)
        output_kb = install_output_expand_key_bindings(
            self.session.terminal.has_collapsed_tool_output,
            self.session.terminal.next_collapsed_output_for_expand,
            self._expand_collapsed_output,
        )
        install_session_key_bindings(self.pt_session, output_kb)

    def _rerender_banner_if_idle(self) -> bool:
        """Clear the viewport and reprint the launch banner at the new width; True when done.

        The banner is static scrollback laid out for the width it was printed
        at; a resize reflows it into sliced / wrapped garbage. While nothing
        has been submitted the screen holds only the banner and the prompt, so
        it is safe to clear and redraw both. Once a turn exists the banner sits
        in scrollback above the conversation and is left alone.

        No startup spin here — SIGWINCH must stay instant.
        """
        if self.session.terminal.submitted_turn_count > 0 or self.pt_app is None:
            return False
        repl_clear_screen()
        drain_stale_cpr_bytes()
        console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        render_launch_banner(console, session=self.session, animate=False)
        return True

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
        return render_prompt_region(self.session, self.state, self.spinner)

    def _accept_prompt_buffer(self, buffer: Buffer) -> bool:
        """Queue accepted text while keeping the prompt application alive."""
        # Enter during confirmation is handled by the Yes/No bindings; never
        # treat residual buffer text as a submitted message while the gate is up.
        if self.state.is_awaiting_confirmation():
            return True
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

    async def suspend(self) -> None:
        """Release stdin while an exclusive picker or wizard is running."""
        task = self._prompt_task
        if task is None:
            return
        if not task.done() and self.pt_app is not None and self.pt_app.is_running:
            self.pt_app.exit(result="")
        await asyncio.gather(task, return_exceptions=True)
        if self._prompt_task is task:
            self._prompt_task = None

    async def close(self) -> None:
        """Stop the persistent prompt application during shell shutdown."""
        task = self._prompt_task
        self._prompt_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def read_prompt_text(self) -> str:
        if self.pt_session is None:
            raise RuntimeError("PromptBuilder.setup() must run before reading prompts")

        if self.session.terminal.pending_theme_refresh:
            self.session.terminal.pending_theme_refresh = False
            refresh_prompt_theme(self.session)
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
