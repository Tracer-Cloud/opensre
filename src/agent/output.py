"""
Output utilities shared across nodes.

- Progress tracking for pipeline execution
- Debug output (verbose mode)
- Investigation header display
- Environment detection (Rich vs plain text)
"""

import os
import sys
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel

# ─────────────────────────────────────────────────────────────────────────────
# Output Format Detection
# ─────────────────────────────────────────────────────────────────────────────


def get_output_format() -> str:
    """Auto-detect output format based on environment."""
    if fmt := os.getenv("TRACER_OUTPUT_FORMAT"):
        return fmt

    ci_indicators = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILDKITE"]
    if any(os.getenv(var) for var in ci_indicators):
        return "text"

    if os.getenv("SLACK_WEBHOOK_URL"):
        return "text"

    if sys.stdout.isatty():
        return "rich"

    return "text"


# ─────────────────────────────────────────────────────────────────────────────
# Progress Tracking
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProgressEvent:
    """A single progress event from a node."""

    node_name: str
    elapsed_ms: int
    fields_updated: list[str] = field(default_factory=list)
    status: str = "completed"
    message: str | None = None


class ProgressTracker:
    """Tracks progress events during pipeline execution."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self._start_times: dict[str, float] = {}
        self._console = Console() if get_output_format() == "rich" else None

    def start(self, node_name: str, message: str | None = None) -> None:
        self._start_times[node_name] = time.time()
        event = ProgressEvent(node_name=node_name, elapsed_ms=0, status="started", message=message)
        self.events.append(event)
        self._emit_progress(event)

    def complete(
        self, node_name: str, fields_updated: list[str] | None = None, message: str | None = None
    ) -> None:
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
        start_time = self._start_times.pop(node_name, time.time())
        elapsed_ms = int((time.time() - start_time) * 1000)
        event = ProgressEvent(
            node_name=node_name, elapsed_ms=elapsed_ms, status="error", message=message
        )
        self.events.append(event)
        self._emit_progress(event)

    def _emit_progress(self, event: ProgressEvent) -> None:
        fmt = get_output_format()

        if event.status == "started":
            line = f"[{event.node_name}] {event.message or 'Starting...'}"
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
            style = {"started": "cyan", "completed": "green", "error": "red bold"}.get(
                event.status, "white"
            )
            self._console.print(f"[{style}]{line}[/]")
        else:
            print(line)


_tracker: ProgressTracker | None = None


def get_tracker() -> ProgressTracker:
    global _tracker
    if _tracker is None:
        _tracker = ProgressTracker()
    return _tracker


def reset_tracker() -> ProgressTracker:
    global _tracker
    _tracker = ProgressTracker()
    return _tracker


# ─────────────────────────────────────────────────────────────────────────────
# Display Functions
# ─────────────────────────────────────────────────────────────────────────────


def render_investigation_header(alert_name: str, pipeline_name: str, severity: str) -> None:
    """Render the investigation start header."""
    fmt = get_output_format()

    if fmt == "rich":
        severity_color = "red" if severity == "critical" else "yellow"
        Console().print(
            Panel(
                f"Investigation Started\n\n"
                f"Alert: [bold]{alert_name}[/]\n"
                f"Pipeline: [cyan]{pipeline_name}[/]\n"
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
        print(f"Pipeline: {pipeline_name}")
        print(f"Severity: {severity}")
        print("-" * 40)


# ─────────────────────────────────────────────────────────────────────────────
# Debug Output
# ─────────────────────────────────────────────────────────────────────────────


def is_verbose() -> bool:
    return os.getenv("TRACER_VERBOSE", "").lower() in ("1", "true", "yes")


def debug_print(message: str) -> None:
    if not is_verbose():
        return
    fmt = get_output_format()
    if fmt == "rich":
        Console().print(f"[dim]{message}[/]")
    else:
        print(f"DEBUG: {message}")
