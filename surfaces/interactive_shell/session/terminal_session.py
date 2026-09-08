"""Interactive-shell (terminal) session facet.

Groups the shell-surface-only session state (prompt-toolkit, theme, background jobs,
metrics, per-turn analytics staging) that ``core``, ``gateway``, and ``tools``
consumers never touch. Composed onto :class:`~surfaces.interactive_shell.session.session.Session`
as ``session.terminal`` and always present (empty for non-shell sessions), so shell
code accesses fields without a None-guard.

Populated cluster-by-cluster as the #3690 split lands; theme is the first cluster.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.constants.repl_autonomy import DEFAULT_AUTO_LEVEL, AutoLevel
from surfaces.interactive_shell.session.terminal_metrics import TerminalMetrics

if TYPE_CHECKING:
    from prompt_toolkit.history import History


#: How many capped tool peeks Ctrl+O can cycle through.
COLLAPSED_OUTPUT_RING_SIZE = 5

#: Bound each stashed peek so five ring slots cannot retain unbounded dumps.
COLLAPSED_STASH_MAX_CHARS = 32_000

#: Expand in-scrollback when the body fits; larger peeks open ``$PAGER`` / less.
INLINE_EXPAND_MAX_CHARS = 8_000
INLINE_EXPAND_MAX_LINES = 120


@dataclass
class ActionLogEntry:
    """One buffered tool call for the grouped, collapsible action log.

    ``kind`` is the section header (e.g. ``GitHub CLI``); ``concise`` is the
    one-line status shown in the section (no inline arguments); ``detail`` is
    the full call + result text revealed on Ctrl+O.
    """

    call_id: str
    kind: str
    concise: str
    detail: str = ""


@dataclass
class TerminalSession:
    """Shell-surface session state, composed onto ``Session`` for the interactive shell."""

    active_theme_name: str = "green"

    cli_command_group: Any = field(default=None, repr=False, compare=False)
    """The ``opensre`` Click command group the shell documents to the model.

    Handed in by the process entrypoint; ``None`` when the shell runs on its
    own, in which case grounding covers slash commands only."""
    """Interactive shell palette name for this REPL session (``/theme``, prompts)."""

    pending_theme_refresh: bool = False
    """When True, apply the active palette to prompt-toolkit before the next prompt."""

    trust_mode: bool = False
    """When True, confirmation prompts for elevated REPL actions are skipped."""

    auto_level: AutoLevel = DEFAULT_AUTO_LEVEL
    """Auto (Off|Low|Med|High) shown above the input box."""

    prompt_history_backend: History | None = None
    """The live ``prompt_toolkit.History`` object backing the input prompt.

    Stored here so ``/history`` and ``/privacy`` slash commands can mutate its
    ``paused`` flag (when it is a ``RedactingFileHistory``) without needing access to
    the ``PromptSession``."""

    prompt_app: Any = None
    """The prompt-toolkit ``Application`` instance for this session.

    Stored here (instead of accessed via ``get_app_or_none()``) so that worker-thread
    slash commands (e.g. ``/theme``) can refresh styles via ``call_soon_threadsafe`` on
    the main asyncio loop."""

    main_loop: Any = None
    """The asyncio event loop for the main REPL coroutine.

    Set once by ``InteractiveShellController.start_interactive_shell`` so worker-thread
    code can schedule prompt-toolkit updates on the main thread."""

    prompt_refresh_fn: Callable[[], None] | None = field(default=None, repr=False)
    """Loop-owned hook to apply pending prefill and redraw the active prompt."""

    fleet_sampler_starter: Callable[[], None] | None = field(default=None, repr=False)
    """Loop-owned hook to lazily start the fleet sampler on first live ``/fleet`` use.

    Set by the interactive-shell controller so the sampler (and its ``psutil`` dependency)
    stays out of base REPL startup and only runs when fleet monitoring is actually
    requested. Thread-safe: the starter marshals task creation onto the REPL event loop."""

    pending_prompt_default: str | None = None
    """When set, the next interactive prompt is pre-filled with this string (then cleared)."""

    pending_prompt_plain_turn: bool = False
    """When True alongside ``pending_prompt_autosubmit``, the submitted prefill runs
    as an ordinary typed turn: the prompt bar (and its spinner) stays up and no
    ``/goal`` work-turn label is painted. Set by :meth:`set_auto_prompt`."""

    pending_prompt_autosubmit: bool = False
    """When True alongside ``pending_prompt_default``, the prefilled prompt is
    submitted automatically instead of waiting for the user to press Enter.

    Used to auto-launch an interactive command the agent decided to run (e.g.
    ``/integrations setup sentry``) so it flows through the normal
    exclusive-stdin dispatch path — the only place an interactive child process
    gets clean stdin."""

    last_input_autosubmitted: bool = False
    """True when the next ``render_submitted_prompt`` came from autosubmit.

    Set when ``/goal set`` (or similar) queues the condition and the prompt
    loop accepts it without Enter. Cleared when the submitted line is painted
    so the work turn can show a distinct ``↗ /goal`` marker above ``[N] ❯``.
    """

    awaiting_handoff_answer: bool = False
    """True when the next submitted line answers a human hand-off question.

    Set by ``ask_user_choice`` (and the ``/choose`` pick). Cleared when the
    submitted prompt is painted so the answer uses the brand colour."""

    pending_choice_response: str | None = None
    goal_paint_signature: tuple[object, ...] | None = None
    """What the last session-goal block showed; unchanged goals repaint as one line."""
    """Selected label while its synthetic answer turn awaits a response.

    The response composer consumes the label to hide a pure acknowledgement
    while preserving meaningful follow-up the selected option unlocks."""

    collapsed_tool_outputs: list[str] = field(default_factory=list)
    """Ring of the last N capped tool peeks (newest last). Ctrl+O cycles."""

    _collapsed_expand_next: int = -1
    """Index into :attr:`collapsed_tool_outputs` for the next Ctrl+O press."""

    inline_tool_results: bool = False
    """True when this turn already printed tool results under their call lines.

    The closing reply must not repeat those results. The response composer
    reads and clears the flag."""

    action_log_entries: list[ActionLogEntry] = field(default_factory=list)
    """Tool calls buffered for the current turn's grouped action log (call order).

    The observer appends each call here instead of printing it live; the batch
    flushes as bordered, collapsible sections above the reply (Ctrl+O expands
    the detail)."""

    pending_confirm_options: tuple[tuple[str, str], ...] | None = None
    """Rows the next execution confirmation should offer, or None for Yes/No.

    Set by the execution gate just before it calls the confirm function; the
    REPL confirm path reads and clears it so the arrow-nav shows the same rows
    (e.g. an "always allow" row for an auto-level gate)."""

    exclusive_stdin_active: bool = False
    """True while a turn is running with exclusive stdin reserved (no live prompt).

    Inline picker/wizard slash commands must dispatch immediately during these
    turns instead of re-queueing via ``set_auto_command``, which would loop."""

    dispatch_active: bool = False
    """True while ``run_agent_turn`` is executing (any turn, not only exclusive-stdin).

    ``set_auto_command`` must not ``validate_and_handle`` while this is set —
    nesting another ``execute_shell_turn`` inside ``/goal set`` doubled the
    PostHog answer before the outer turn finished."""

    history_generation: int = 0
    """Incremented on /new so background task watchers can skip stale history writes."""

    metrics: TerminalMetrics = field(default_factory=TerminalMetrics)
    """Interactive-shell turn/intervention analytics counters (see ``/status``)."""

    submitted_turn_count: int = 0
    """Prompts the user has submitted this session; drives the ``[N]`` prompt label.

    Deliberately independent of ``session.history``: one submitted request may
    append many history rows (shell commands, tool executions), but it occupies
    exactly one numbered prompt line."""

    _turn_outcome_hint: str | None = field(default=None, repr=False, compare=False)
    """Optional structured outcome set by a terminal handler for analytics."""

    _pending_turn_llm: Any | None = field(default=None, repr=False, compare=False)
    """LLM run metadata (an ``LlmRunInfo``) staged by a terminal handler for the
    current turn's prompt-recorder flush. Consumed exactly once via
    ``pop_pending_turn_llm`` so it cannot leak into later turns."""

    _pending_turn_error: tuple[str, str] | None = field(default=None, repr=False, compare=False)
    """Structured ``(error_kind, message)`` staged by a failing handler for the
    current turn's prompt-recorder flush. Consumed exactly once via
    ``pop_pending_turn_error`` so it cannot leak into later turns."""

    # ── behavior over the fields above (Session delegates via ``session.terminal``) ──

    def claim_turn_number(self) -> int:
        """Advance and return the 1-based ``[N]`` number for a just-submitted prompt."""
        self.submitted_turn_count += 1
        return self.submitted_turn_count

    def has_collapsed_tool_output(self) -> bool:
        """True when Ctrl+O can expand at least one stashed peek."""
        return bool(self.collapsed_tool_outputs)

    @property
    def collapsed_tool_output(self) -> str | None:
        """Newest capped peek, or ``None`` when the ring is empty."""
        return self.collapsed_tool_outputs[-1] if self.collapsed_tool_outputs else None

    @collapsed_tool_output.setter
    def collapsed_tool_output(self, value: str | None) -> None:
        """Compat for direct assignment; ``None`` is a no-op (keeps the ring)."""
        if value is None:
            return
        self.stash_collapsed_tool_output(value)

    def stash_collapsed_tool_output(self, text: str | None) -> None:
        """Push a size-bounded peek onto the ring.

        ``None`` means the latest preview was not folded — leave earlier peeks
        reachable via Ctrl+O. Bodies longer than ``COLLAPSED_STASH_MAX_CHARS``
        are truncated so the ring cannot retain unbounded API dumps.
        """
        if text is None:
            return
        body = text
        if len(body) > COLLAPSED_STASH_MAX_CHARS:
            marker = "\n… (truncated for Ctrl+O stash)\n"
            keep = max(0, COLLAPSED_STASH_MAX_CHARS - len(marker))
            body = body[:keep].rstrip() + marker
        self.collapsed_tool_outputs.append(body)
        overflow = len(self.collapsed_tool_outputs) - COLLAPSED_OUTPUT_RING_SIZE
        if overflow > 0:
            del self.collapsed_tool_outputs[:overflow]
        self._collapsed_expand_next = len(self.collapsed_tool_outputs) - 1

    def next_collapsed_output_for_expand(self) -> str:
        """Return the next peek for Ctrl+O and advance toward older entries.

        First press after a stash shows the newest body; repeated presses cycle
        older peeks, then wrap back to newest.
        """
        ring = self.collapsed_tool_outputs
        if not ring:
            return ""
        idx = self._collapsed_expand_next
        if idx < 0 or idx >= len(ring):
            idx = len(ring) - 1
        body = ring[idx]
        self._collapsed_expand_next = idx - 1 if idx > 0 else len(ring) - 1
        return body

    def push_action_log(self, entry: ActionLogEntry) -> None:
        """Buffer one tool call for the current turn's grouped action log."""
        self.action_log_entries.append(entry)

    def append_action_result(self, call_id: str, result: str) -> None:
        """Attach a result line to the buffered call ``call_id`` (if present)."""
        for entry in reversed(self.action_log_entries):
            if entry.call_id == call_id:
                entry.detail = f"{entry.detail}\n{result}" if entry.detail else result
                return

    def has_action_log(self) -> bool:
        """True when at least one action is buffered for the current turn."""
        return bool(self.action_log_entries)

    def take_action_log(self) -> list[ActionLogEntry]:
        """Return the buffered action entries and clear the buffer."""
        entries = self.action_log_entries
        self.action_log_entries = []
        return entries

    def pop_pending_prompt_default(self) -> str:
        """Return pre-filled text for the next prompt line, if any, and clear it."""
        value = self.pending_prompt_default
        self.pending_prompt_default = None
        return value or ""

    def pop_pending_autosubmit(self) -> bool:
        """Return whether the pending prefill should auto-submit, and clear the flag."""
        value = self.pending_prompt_autosubmit
        self.pending_prompt_autosubmit = False
        return value

    def pop_pending_plain_turn(self) -> bool:
        """Return whether the pending autosubmit is a plain turn, and clear the flag."""
        value = self.pending_prompt_plain_turn
        self.pending_prompt_plain_turn = False
        return value

    def set_auto_prompt(self, text: str) -> None:
        """Queue *text* to be submitted as an ordinary turn, as if the user typed it.

        Unlike :meth:`set_auto_command`, the controller does not suspend the
        prompt for the turn, so the pinned-layout spinner keeps showing progress.
        """
        self.pending_prompt_default = text
        self.pending_prompt_autosubmit = True
        self.pending_prompt_plain_turn = True
        self.notify_prompt_changed()

    def set_auto_command(self, command: str) -> None:
        """Queue a command to run automatically on the next prompt iteration.

        Prefills the input with ``command`` and marks it for auto-submit, then
        refreshes the active prompt so the loop submits it without waiting for
        Enter. Lets the agent launch an interactive command (setup/connect)
        through the normal exclusive-stdin dispatch path rather than spawning it
        mid-turn, where it would fight the live prompt for stdin.
        """
        self.pending_prompt_default = command
        self.pending_prompt_autosubmit = True
        self.pending_prompt_plain_turn = False
        self.notify_prompt_changed()

    def notify_prompt_changed(self) -> None:
        """Redraw the active prompt (placeholder state and pending prefill)."""
        if self.prompt_refresh_fn is not None:
            self.prompt_refresh_fn()

    def ensure_fleet_sampler_started(self) -> None:
        """Request that the fleet sampler start (no-op if unwired or already running)."""
        if self.fleet_sampler_starter is not None:
            self.fleet_sampler_starter()

    def set_turn_outcome_hint(self, hint: str | None) -> None:
        """Attach a structured outcome for the current terminal handler."""
        self._turn_outcome_hint = hint.strip() if isinstance(hint, str) and hint.strip() else None

    def pop_turn_outcome_hint(self) -> str | None:
        """Return and clear any structured outcome hint for this turn."""
        hint = self._turn_outcome_hint
        self._turn_outcome_hint = None
        return hint

    def set_pending_turn_llm(self, run: Any | None) -> None:
        """Stage LLM run metadata for this turn's prompt-recorder flush."""
        self._pending_turn_llm = run

    def pop_pending_turn_llm(self) -> Any | None:
        """Return and clear staged LLM run metadata for this turn."""
        run = self._pending_turn_llm
        self._pending_turn_llm = None
        return run

    def set_pending_turn_error(self, kind: str, message: str) -> None:
        """Stage a structured turn error for this turn's prompt-recorder flush."""
        kind = kind.strip()
        message = message.strip()
        if kind or message:
            self._pending_turn_error = (kind or "error", message)

    def pop_pending_turn_error(self) -> tuple[str, str] | None:
        """Return and clear the staged structured turn error."""
        error = self._pending_turn_error
        self._pending_turn_error = None
        return error
