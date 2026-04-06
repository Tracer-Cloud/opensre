"""Terminal renderer for remote agent streaming events.

Reuses spinner and label patterns from app.output so that remote investigation
output looks identical to a local ``opensre investigate`` run.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

from app.output import (
    ProgressTracker,
    get_output_format,
    render_investigation_header,
)
from app.remote.stream import StreamEvent

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_WHITE = "\033[37m"
_CYAN = "\033[1;36m"


class StreamRenderer:
    """Renders a stream of LangGraph SSE events as live terminal progress.

    Wraps ProgressTracker to show the same spinners and resolved-dot lines
    that local investigations produce, driven by remote streaming events.
    """

    def __init__(self) -> None:
        self._tracker = ProgressTracker()
        self._active_node: str | None = None
        self._events_received: int = 0
        self._node_names_seen: list[str] = []
        self._final_state: dict[str, Any] = {}
        self._stream_completed = False

    @property
    def events_received(self) -> int:
        return self._events_received

    @property
    def node_names_seen(self) -> list[str]:
        return list(self._node_names_seen)

    @property
    def final_state(self) -> dict[str, Any]:
        return dict(self._final_state)

    @property
    def stream_completed(self) -> bool:
        return self._stream_completed

    def render_stream(self, events: Iterator[StreamEvent]) -> dict[str, Any]:
        """Consume a full event stream and render progress to the terminal.

        Returns the accumulated final state dict.
        """
        _print_connection_banner()

        for event in events:
            self._handle_event(event)

        self._finish_active_node()
        self._print_report()
        return dict(self._final_state)

    def _handle_event(self, event: StreamEvent) -> None:
        self._events_received += 1

        if event.event_type == "metadata":
            return

        if event.event_type == "end":
            self._stream_completed = True
            self._finish_active_node()
            return

        if event.event_type == "updates":
            self._handle_update(event)
            return

    def _handle_update(self, event: StreamEvent) -> None:
        node = event.node_name
        if not node:
            return

        canonical = _canonical_node_name(node)

        if canonical != self._active_node:
            self._finish_active_node()
            self._active_node = canonical
            if canonical not in self._node_names_seen:
                self._node_names_seen.append(canonical)
            self._tracker.start(canonical)

        self._merge_state(event.data.get(node, event.data))

    def _finish_active_node(self) -> None:
        if self._active_node is None:
            return
        message = self._build_node_message(self._active_node)
        self._tracker.complete(self._active_node, message=message)
        self._active_node = None

    def _merge_state(self, update: Any) -> None:
        if isinstance(update, dict):
            self._final_state.update(update)

    def _build_node_message(self, node: str) -> str | None:
        if node == "plan_actions":
            actions = self._final_state.get("planned_actions", [])
            if actions:
                return f"Planned actions: {actions}"
        if node == "resolve_integrations":
            integrations = self._final_state.get("resolved_integrations", {})
            if integrations:
                names = list(integrations.keys())
                return f"Resolved: {names}"
        if node in {"diagnose", "diagnose_root_cause"}:
            score = self._final_state.get("validity_score")
            if score is not None:
                return f"validity:{int(score * 100)}%"
        return None

    def _print_report(self) -> None:
        alert_name = self._final_state.get("alert_name", "Unknown")
        pipeline = self._final_state.get("pipeline_name", "Unknown")
        severity = self._final_state.get("severity", "unknown")

        if alert_name != "Unknown" or pipeline != "Unknown":
            render_investigation_header(alert_name, pipeline, severity)

        root_cause = self._final_state.get("root_cause", "")
        report = self._final_state.get("report", "")

        if root_cause:
            _print_section("Root Cause", root_cause)
        if report:
            _print_section("Report", report)
        elif not root_cause:
            if self._final_state.get("is_noise"):
                _print_info("Alert classified as noise — no investigation needed.")
            elif self._events_received == 0:
                _print_info("No events received from the remote agent.")


def _canonical_node_name(name: str) -> str:
    """Map LangGraph node names to the canonical names used by ProgressTracker."""
    mapping = {
        "diagnose_root_cause": "diagnose_root_cause",
        "diagnose": "diagnose_root_cause",
        "publish_findings": "publish_findings",
        "publish": "publish_findings",
    }
    return mapping.get(name, name)


def _print_connection_banner() -> None:
    if get_output_format() == "rich":
        sys.stdout.write(
            f"\n  {_BOLD}{_CYAN}Remote Investigation{_RESET}"
            f"  {_DIM}streaming from deployed agent{_RESET}\n\n"
        )
    else:
        print("\n  Remote Investigation  streaming from deployed agent\n")
    sys.stdout.flush()


def _print_section(title: str, content: str) -> None:
    if get_output_format() == "rich":
        sys.stdout.write(f"\n  {_BOLD}{_WHITE}{title}{_RESET}\n")
        for line in content.strip().splitlines():
            sys.stdout.write(f"  {_DIM}{line}{_RESET}\n")
    else:
        print(f"\n  {title}")
        for line in content.strip().splitlines():
            print(f"  {line}")
    sys.stdout.flush()


def _print_info(message: str) -> None:
    if get_output_format() == "rich":
        sys.stdout.write(f"\n  {_DIM}{message}{_RESET}\n")
    else:
        print(f"\n  {message}")
    sys.stdout.flush()
