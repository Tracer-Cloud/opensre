"""Progress-tracker port (Protocol) + Noop default + injection helpers.

Core code (under ``core/domain/``, ``utils/``)
imports only from this module to report stage progress; the CLI
surface implements the Protocol and registers its concrete tracker
via :func:`set_progress_tracker` at boundary.

The Protocol surface is intentionally narrow: the methods listed here
are the ones core actually calls. The Rich-backed REPL tracker exposes
many more (subtext animation, ``print_above`` etc.), but those stay in
the adapter — they're not the core's concern.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class ProgressReporter(Protocol):
    """Stage-progress reporting contract for core code.

    The default in a process that hasn't registered an adapter is
    :class:`NoopProgressTracker` — all methods are no-ops. The CLI
    layer registers a Rich-backed implementation at startup via
    :func:`set_progress_tracker`.
    """

    def start(self, node_name: str, message: str | None = None) -> None:
        """Mark stage ``node_name`` as started, with an optional caption."""

    def complete(
        self,
        node_name: str,
        fields_updated: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        """Mark stage ``node_name`` as completed, optionally naming the
        state fields it produced and a closing caption.
        """

    def error(self, node_name: str, message: str) -> None:
        """Mark stage ``node_name`` as errored with a human-readable cause."""

    def record_tool_start(
        self,
        tool_name: str,
        tool_input: Any = None,
        *,
        event_key: str | None = None,
    ) -> None:
        """Record that ``tool_name`` has begun execution within the
        current stage. ``event_key`` is the per-invocation id used by
        the matching :meth:`record_tool_end` call.
        """

    def record_tool_end(
        self,
        tool_name: str,
        output: Any = None,
        *,
        event_key: str | None = None,
        tool_input: Any = None,
    ) -> None:
        """Record that ``tool_name`` finished, with optional output
        + the same ``event_key`` passed to ``record_tool_start`` so
        the adapter can pair start/end for timing.
        """

    def stop(self) -> None:
        """Tear down any active display/animation/watchers.

        Core callers invoke this when transitioning from progress
        rendering into final-report rendering, so the live display
        releases the terminal before plain text prints.
        """


class NoopProgressTracker:
    """Drop-on-floor implementation used when no adapter is registered.

    Conforms to :class:`ProgressReporter` structurally. Headless
    contexts (tests, non-TTY runs, scripted invocations) get this by
    default, so core code can call tracker methods unconditionally
    without checking for ``None``.
    """

    def start(self, node_name: str, message: str | None = None) -> None:
        _ = (node_name, message)

    def complete(
        self,
        node_name: str,
        fields_updated: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        _ = (node_name, fields_updated, message)

    def error(self, node_name: str, message: str) -> None:
        _ = (node_name, message)

    def record_tool_start(
        self,
        tool_name: str,
        tool_input: Any = None,
        *,
        event_key: str | None = None,
    ) -> None:
        _ = (tool_name, tool_input, event_key)

    def record_tool_end(
        self,
        tool_name: str,
        output: Any = None,
        *,
        event_key: str | None = None,
        tool_input: Any = None,
    ) -> None:
        _ = (tool_name, output, event_key, tool_input)

    def stop(self) -> None:
        return None


_tracker: ProgressReporter = NoopProgressTracker()
_tracker_factory: Callable[[], ProgressReporter] | None = None
_silenced: bool = False


def get_progress_tracker() -> ProgressReporter:
    """Return the currently-registered tracker (or the Noop default).

    When a CLI factory is registered and progress has not been silenced,
    the first call materializes the adapter lazily so ``ProgressReporter``
    is constructed after REPL boot (when ``_repl_progress_active()`` is
    accurate) rather than at process start-up.
    """
    global _tracker
    if not _silenced and isinstance(_tracker, NoopProgressTracker) and _tracker_factory is not None:
        set_progress_tracker(_tracker_factory())
    return _tracker


def set_progress_tracker_factory(factory: Callable[[], ProgressReporter] | None) -> None:
    """Register a lazy factory for the CLI progress tracker.

    Boundary code (typically ``install_product_adapters``)
    installs ``get_tracker`` here instead of constructing the Rich
    tracker eagerly at process start-up.
    """
    global _tracker_factory
    _tracker_factory = factory


def set_progress_tracker(tracker: ProgressReporter) -> None:
    """Install ``tracker`` as the active implementation. Called by the
    CLI boundary (or any other adapter) at startup so subsequent
    ``get_progress_tracker()`` calls return the real implementation.
    """
    global _tracker, _silenced
    _tracker = tracker
    if not isinstance(tracker, NoopProgressTracker):
        _silenced = False


def silence_progress_tracker() -> None:
    """Replace whatever is registered with a Noop tracker.

    Used at the pipeline entry point when a run shouldn't emit progress
    (e.g. headless runs whose output goes only to a sink).
    Calls ``stop()`` on the previous tracker first so any active
    display / watcher / animation it owns gets released — silencing a
    Rich tracker without stopping it leaks its toggle watcher and the
    Live display keeps holding the terminal.
    """
    global _silenced
    _silenced = True
    _tracker.stop()
    set_progress_tracker(NoopProgressTracker())
