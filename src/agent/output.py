"""
Unified output management.

Consolidates all rendering into a single contract:
- Nodes only update state fields
- This module decides what/how to render
- Auto-detects Rich vs plain text based on environment
"""

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.panel import Panel


# ─────────────────────────────────────────────────────────────────────────────
# Output Format Detection
# ─────────────────────────────────────────────────────────────────────────────


def get_output_format() -> str:
    """
    Auto-detect output format based on environment.

    Priority:
    1. Explicit TRACER_OUTPUT_FORMAT env var
    2. CI environments -> text
    3. Interactive TTY -> rich
    4. Piped output -> text
    """
    # Explicit override takes precedence
    if fmt := os.getenv("TRACER_OUTPUT_FORMAT"):
        return fmt  # "rich" | "text"

    # CI environments get plain text
    ci_indicators = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILDKITE"]
    if any(os.getenv(var) for var in ci_indicators):
        return "text"

    # Slack webhook context gets text
    if os.getenv("SLACK_WEBHOOK_URL"):
        return "text"

    # Interactive terminal gets Rich
    if sys.stdout.isatty():
        return "rich"

    # Piped output (e.g., `make demo > log.txt`) gets text
    return "text"


# ─────────────────────────────────────────────────────────────────────────────
# Progress Event Tracking
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProgressEvent:
    """A single progress event from a node."""

    node_name: str
    elapsed_ms: int
    fields_updated: list[str] = field(default_factory=list)
    status: str = "completed"  # "started" | "completed" | "error"
    message: str | None = None


class ProgressTracker:
    """
    Tracks progress events during pipeline execution.

    Usage:
        tracker = ProgressTracker()
        tracker.start("frame_problem")
        # ... node runs ...
        tracker.complete("frame_problem", fields_updated=["alert_name", "evidence"])
    """

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self._start_times: dict[str, float] = {}
        self._console = Console() if get_output_format() == "rich" else None

    def start(self, node_name: str, message: str | None = None) -> None:
        """Record start of a node."""
        self._start_times[node_name] = time.time()
        event = ProgressEvent(
            node_name=node_name,
            elapsed_ms=0,
            status="started",
            message=message,
        )
        self.events.append(event)
        self._emit_progress(event)

    def complete(
        self,
        node_name: str,
        fields_updated: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        """Record completion of a node."""
        start_time = self._start_times.pop(node_name, time.time())
        elapsed_ms = int((time.time() - start_time) * 1000)

        event = ProgressEvent(
            node_name=node_name,
            elapsed_ms=elapsed_ms,
            fields_updated=fields_updated or [],
            status="completed",
            message=message,
        )
        self.events.append(event)
        self._emit_progress(event)

    def error(self, node_name: str, message: str) -> None:
        """Record error in a node."""
        start_time = self._start_times.pop(node_name, time.time())
        elapsed_ms = int((time.time() - start_time) * 1000)

        event = ProgressEvent(
            node_name=node_name,
            elapsed_ms=elapsed_ms,
            status="error",
            message=message,
        )
        self.events.append(event)
        self._emit_progress(event)

    def _emit_progress(self, event: ProgressEvent) -> None:
        """Emit a progress line to terminal."""
        fmt = get_output_format()

        if event.status == "started":
            line = f"[{event.node_name}] Starting..."
            if event.message:
                line = f"[{event.node_name}] {event.message}"
        elif event.status == "error":
            line = f"[{event.node_name}] ERROR: {event.message} ({event.elapsed_ms}ms)"
        else:
            fields_str = ", ".join(event.fields_updated[:3]) if event.fields_updated else ""
            if len(event.fields_updated) > 3:
                fields_str += f" +{len(event.fields_updated) - 3} more"
            line = f"[{event.node_name}] Done ({event.elapsed_ms}ms)"
            if fields_str:
                line += f" -> {fields_str}"
            if event.message:
                line += f" | {event.message}"

        if fmt == "rich" and self._console:
            status_style = {
                "started": "cyan",
                "completed": "green",
                "error": "red bold",
            }.get(event.status, "white")
            self._console.print(f"[{status_style}]{line}[/]")
        else:
            print(line)


# Global tracker instance - reset per pipeline run
_tracker: ProgressTracker | None = None


def get_tracker() -> ProgressTracker:
    """Get or create the global progress tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ProgressTracker()
    return _tracker


def reset_tracker() -> ProgressTracker:
    """Reset and return a fresh tracker for a new pipeline run."""
    global _tracker
    _tracker = ProgressTracker()
    return _tracker


# ─────────────────────────────────────────────────────────────────────────────
# Final Report Rendering
# ─────────────────────────────────────────────────────────────────────────────


def render_final_report(state: dict[str, Any]) -> None:
    """
    Render the final report exactly once.

    This is the ONLY place final output should be rendered.
    Reads state.slack_message and displays appropriately.
    """
    fmt = get_output_format()
    slack_message = state.get("slack_message", "")
    confidence = state.get("confidence", 0.0)
    validity_score = state.get("validity_score", 0.0)

    if not slack_message:
        if fmt == "rich":
            console = Console()
            console.print("[yellow]No report generated.[/]")
        else:
            print("No report generated.")
        return

    if fmt == "rich":
        console = Console()
        console.print()
        console.print(
            Panel(
                slack_message,
                title="RCA Report",
                border_style="green",
            )
        )
        console.print(
            f"\nInvestigation complete. Confidence: {confidence:.0%} | Validity: {validity_score:.0%}"
        )
    else:
        print("\n" + "=" * 60)
        print("RCA REPORT")
        print("=" * 60)
        print(slack_message)
        print("=" * 60)
        print(f"Investigation complete. Confidence: {confidence:.0%} | Validity: {validity_score:.0%}")


def render_investigation_header(
    alert_name: str,
    affected_table: str,
    severity: str,
) -> None:
    """Render the investigation start header."""
    fmt = get_output_format()

    if fmt == "rich":
        console = Console()
        severity_color = "red" if severity == "critical" else "yellow"
        console.print(
            Panel(
                f"Investigation Started\n\n"
                f"Alert: [bold]{alert_name}[/]\n"
                f"Table: [cyan]{affected_table}[/]\n"
                f"Severity: [{severity_color}]{severity}[/]",
                title="Pipeline Investigation",
                border_style="cyan",
            )
        )
    else:
        print("\n" + "-" * 40)
        print("PIPELINE INVESTIGATION")
        print("-" * 40)
        print(f"Alert: {alert_name}")
        print(f"Table: {affected_table}")
        print(f"Severity: {severity}")
        print("-" * 40)


# ─────────────────────────────────────────────────────────────────────────────
# Debug Output (verbose mode only)
# ─────────────────────────────────────────────────────────────────────────────


def is_verbose() -> bool:
    """Check if verbose output is enabled."""
    return os.getenv("TRACER_VERBOSE", "").lower() in ("1", "true", "yes")


def debug_print(message: str) -> None:
    """Print debug message only in verbose mode."""
    if not is_verbose():
        return

    fmt = get_output_format()
    if fmt == "rich":
        console = Console()
        console.print(f"[dim]{message}[/]")
    else:
        print(f"DEBUG: {message}")
