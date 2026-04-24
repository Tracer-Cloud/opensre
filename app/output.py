"""
Output utilities shared across nodes.

- Progress tracking: live in-place spinner that resolves to a final dot line
- Investigation header display
- Debug output (verbose mode)
- Environment detection (rich TTY vs plain text)
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.text import Text


def get_output_format() -> str:
    """Return 'rich' for interactive TTY, 'text' otherwise.

    Respects the ``NO_COLOR`` environment variable (https://no-color.org/).
    """
    if fmt := os.getenv("TRACER_OUTPUT_FORMAT"):
        return fmt
    if os.getenv("NO_COLOR") is not None:
        return "text"
    if os.getenv("SLACK_WEBHOOK_URL"):
        return "text"
    return "rich" if sys.stdout.isatty() else "text"


_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_WHITE = "\033[37m"
_GREEN = "\033[1;32m"
_RED = "\033[1;31m"


def _ansi(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Node labels and cycling loading verbs
# ─────────────────────────────────────────────────────────────────────────────

_NODE_LABELS: dict[str, str] = {
    "extract_alert": "Reading alert",
    "resolve_integrations": "Loading integrations",
    "plan_actions": "Planning",
    "investigate": "Gathering evidence",
    "diagnose_root_cause": "Diagnosing",
    "publish_findings": "Publishing",
}

_LOADING_VERBS: dict[str, list[str]] = {
    "extract_alert": [
        "parsing alert metadata",
        "classifying severity",
        "extracting pipeline context",
    ],
    "resolve_integrations": ["checking integrations", "loading credentials"],
    "plan_actions": [
        "assessing available sources",
        "identifying evidence gaps",
        "deciding next steps",
        "prioritising queries",
    ],
    "investigate": [
        "loading OpenSRE dataset telemetry",
        "querying logs",
        "fetching metrics",
        "scanning monitors",
        "correlating events",
        "pulling error traces",
    ],
    "diagnose_root_cause": [
        "correlating evidence",
        "validating hypotheses",
        "cross-checking claims",
        "reasoning about failure",
        "scoring confidence",
    ],
    "publish_findings": ["assembling report", "formatting findings"],
}


def _node_label(node_name: str) -> str:
    return _NODE_LABELS.get(node_name, node_name.replace("_", " ").title())


# ─────────────────────────────────────────────────────────────────────────────
# Message humanisation
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_DISPLAY: dict[str, str] = {
    "query_datadog_all": "Datadog",
    "query_datadog_logs": "Datadog logs",
    "query_datadog_monitors": "Datadog monitors",
    "query_datadog_events": "Datadog events",
    "query_grafana_logs": "Grafana Loki",
    "query_grafana_traces": "Grafana Tempo",
    "query_grafana_metrics": "Grafana Mimir",
    "query_grafana_alert_rules": "Grafana alerts",
    "query_betterstack_logs": "Better Stack logs",
    "get_cloudwatch_logs": "CloudWatch",
    "get_error_logs": "error logs",
    "get_failed_jobs": "batch jobs",
    "get_sre_guidance": "SRE runbook",
    "get_lambda_invocation_logs": "Lambda logs",
    "get_lambda_errors": "Lambda errors",
    "inspect_s3_object": "S3",
    "get_s3_object": "S3 audit",
    "inspect_lambda_function": "Lambda config",
    "get_failed_tools": "tool results",
}


def _humanise_message(message: str) -> str:
    if not message:
        return ""
    m = re.match(r"Planned actions:\s*\[(.+)\]", message)
    if m:
        raw = re.findall(r"'([^']+)'", m.group(1))
        return ", ".join(_ACTION_DISPLAY.get(a, a.replace("_", " ")) for a in raw)
    if "No new actions" in message:
        return ""
    if "integrations" in message.lower() or "resolved" in message.lower():
        m2 = re.search(r"\[(.+)\]", message)
        if m2 and (services := re.findall(r"'([^']+)'", m2.group(1))):
            return ", ".join(services)
    m3 = re.match(r"validity:(\d+%)", message)
    if m3:
        return f"confidence {m3.group(1)}"
    return re.sub(r"^datadog:", "", message)


def _fmt_timing(elapsed_ms: int) -> str:
    return f"{elapsed_ms / 1000:.1f}s" if elapsed_ms >= 1000 else f"{elapsed_ms}ms"


# ─────────────────────────────────────────────────────────────────────────────
# Live spinner
# ─────────────────────────────────────────────────────────────────────────────

_FRAMES = ("◐", "◓", "◑", "◒")
_FRAME_SECS = 0.10
_VERB_SECS = 2.5


class _LiveSpinner:
    """Animated in-place spinner. Resolves to a static dot line on stop().

    Supports dynamic subtext via :meth:`update_subtext` so callers can
    replace the cycling verb with real-time status (e.g. tool calls).
    """

    def __init__(self, node_name: str) -> None:
        self._label = _node_label(node_name)
        self._verbs = _LOADING_VERBS.get(node_name, ["working"])
        self._t0 = time.monotonic()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._lock = threading.Lock()
        self._override_text: str | None = None
        self._override_until: float = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self, event: ProgressEvent) -> None:
        self._done.set()
        self._thread.join()
        _write("\033[2K\r" + self._resolved_line(event) + "\n")

    def update_subtext(self, text: str, duration: float = 4.0) -> None:
        """Replace the cycling verb with *text* for *duration* seconds."""
        with self._lock:
            self._override_text = text
            self._override_until = time.monotonic() + duration

    def _elapsed(self) -> float:
        return time.monotonic() - self._t0

    def _current_verb(self) -> str:
        with self._lock:
            if self._override_text and time.monotonic() < self._override_until:
                return self._override_text
            if self._override_text and time.monotonic() >= self._override_until:
                self._override_text = None
        return self._verbs[int(self._elapsed() / _VERB_SECS) % len(self._verbs)]

    def _spinner_line(self) -> str:
        frame = _ansi(_FRAMES[int(self._elapsed() / _FRAME_SECS) % len(_FRAMES)], _DIM)
        verb = _ansi(self._current_verb(), _DIM)
        return f"  {frame}  {_ansi(self._label, _BOLD, _WHITE)}  {verb}"

    def _resolved_line(self, event: ProgressEvent) -> str:
        err = event.status == "error"
        dot = _ansi("●", _RED if err else _GREEN)
        label = _ansi(self._label, _BOLD, _WHITE)
        timing = _ansi(_fmt_timing(event.elapsed_ms), _DIM)
        parts = [f"  {dot}  {label}  {timing}"]
        if msg := _humanise_message(event.message or ""):
            parts.append(_ansi(msg, _RED if err else _DIM))
        return "  ".join(parts)

    def _loop(self) -> None:
        while not self._done.wait(_FRAME_SECS):
            _write("\033[2K\r" + self._spinner_line())


# ─────────────────────────────────────────────────────────────────────────────
# Progress event + tracker
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProgressEvent:
    node_name: str
    elapsed_ms: int
    fields_updated: list[str] = field(default_factory=list)
    status: str = "completed"
    message: str | None = None


class ProgressTracker:
    """One spinner per node, started on .start() and resolved in-place on .complete()/.error()."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self._start_times: dict[str, float] = {}
        self._spinners: dict[str, _LiveSpinner] = {}
        self._rich = get_output_format() == "rich"

    def start(self, node_name: str, message: str | None = None) -> None:
        self._start_times[node_name] = time.monotonic()
        self.events.append(
            ProgressEvent(node_name=node_name, elapsed_ms=0, status="started", message=message)
        )
        if self._rich:
            s = _LiveSpinner(node_name)
            self._spinners[node_name] = s
            s.start()
        else:
            print(f"  … {_node_label(node_name)}")

    def complete(
        self, node_name: str, fields_updated: list[str] | None = None, message: str | None = None
    ) -> None:
        self._finish(node_name, "completed", fields_updated or [], message)

    def error(self, node_name: str, message: str) -> None:
        self._finish(node_name, "error", [], message)

    def update_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        """Push a live status string into the active spinner for *node_name*."""
        if spinner := self._spinners.get(node_name):
            spinner.update_subtext(text, duration)

    def _finish(
        self, node_name: str, status: str, fields_updated: list[str], message: str | None
    ) -> None:
        elapsed_ms = int(
            (time.monotonic() - self._start_times.pop(node_name, time.monotonic())) * 1000
        )
        event = ProgressEvent(
            node_name=node_name,
            elapsed_ms=elapsed_ms,
            fields_updated=fields_updated,
            status=status,
            message=message,
        )
        self.events.append(event)
        if self._rich and (spinner := self._spinners.pop(node_name, None)):
            spinner.stop(event)
            return
        mark = "✗" if status == "error" else "●"
        line = f"  {mark} {_node_label(node_name)}  {_fmt_timing(elapsed_ms)}"
        if msg := _humanise_message(message or ""):
            line += f"  {msg}"
        print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton tracker
# ─────────────────────────────────────────────────────────────────────────────

_tracker: ProgressTracker | None = None


def get_tracker(*, reset: bool = False) -> ProgressTracker:
    global _tracker
    if _tracker is None or reset:
        _tracker = ProgressTracker()
    return _tracker


def reset_tracker() -> ProgressTracker:
    """Kept for backward compatibility with existing call sites."""
    return get_tracker(reset=True)


# ─────────────────────────────────────────────────────────────────────────────
# Investigation header
# ─────────────────────────────────────────────────────────────────────────────


def render_investigation_header(
    alert_name: str, pipeline_name: str, severity: str, alert_id: str | None = None
) -> None:
    sev_color = "red" if severity.lower() == "critical" else "yellow"
    fields = [
        ("  Alert      ", alert_name, "bold white"),
        ("  Pipeline   ", pipeline_name, "cyan"),
        ("  Severity   ", severity, f"bold {sev_color}"),
    ]
    if alert_id:
        fields.append(("  Alert ID   ", alert_id, "dim"))

    if get_output_format() == "rich":
        console = Console(highlight=False)
        console.print()
        for label, value, style in fields:
            console.print(Text.assemble((label, "dim"), (value, style)))
        console.print()
    else:
        print()
        for label, value, _ in fields:
            print(f"{label}{value}")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# Debug output
# ─────────────────────────────────────────────────────────────────────────────


def _is_verbose() -> bool:
    if os.getenv("TRACER_VERBOSE", "").lower() in ("1", "true", "yes"):
        return True
    try:
        from app.cli.context import is_debug, is_verbose

        return is_verbose() or is_debug()
    except Exception:  # noqa: BLE001
        return False


def debug_print(message: str) -> None:
    if not _is_verbose():
        return
    if get_output_format() == "rich":
        Console().print(f"[dim]{message}[/]")
    else:
        print(f"DEBUG: {message}")
