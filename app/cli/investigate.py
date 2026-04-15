"""Shared investigation helpers for CLI entrypoints."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from langsmith import traceable

from app.config import LLMSettings

if TYPE_CHECKING:
    from app.remote.stream import StreamEvent
    from app.state import AgentState


def _call_run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    *,
    raw_alert: dict[str, Any],
) -> AgentState:
    """Import the heavy investigation runner only when execution starts."""
    from app.pipeline.runners import run_investigation

    return run_investigation(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
    )


def resolve_investigation_context(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None,
    pipeline_name: str | None,
    severity: str | None,
) -> tuple[str, str, str]:
    """Resolve investigation metadata from CLI overrides and payload defaults."""
    return (
        alert_name or raw_alert.get("alert_name") or "Incident",
        pipeline_name or raw_alert.get("pipeline_name") or "events_fact",
        severity or raw_alert.get("severity") or "warning",
    )


@traceable(name="investigation")
def run_investigation_cli(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Run the investigation and return the CLI-facing JSON payload."""
    LLMSettings.from_env()
    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )
    state = _call_run_investigation(
        resolved_alert_name,
        resolved_pipeline_name,
        resolved_severity,
        raw_alert=raw_alert,
    )
    slack_message = state["slack_message"]
    return {
        "report": slack_message,
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
        "is_noise": state.get("is_noise", False),
    }


def stream_investigation_cli(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> Iterator[StreamEvent]:
    """Stream investigation events locally via ``astream_events``.

    Bridges the async LangGraph streaming API into a synchronous iterator
    using a background thread + queue so events are yielded in real time
    (not batched).  The same ``StreamRenderer`` used for remote
    investigations can render local runs identically.
    """
    import queue
    import threading

    from app.pipeline.runners import astream_investigation

    LLMSettings.from_env()
    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )

    event_queue: queue.Queue[StreamEvent | Exception | None] = queue.Queue()

    def _run_async() -> None:
        loop = asyncio.new_event_loop()
        try:

            async def _pump() -> None:
                async for evt in astream_investigation(
                    resolved_alert_name,
                    resolved_pipeline_name,
                    resolved_severity,
                    raw_alert=raw_alert,
                ):
                    event_queue.put(evt)

            loop.run_until_complete(_pump())
        except Exception as exc:
            event_queue.put(exc)
        finally:
            event_queue.put(None)
            loop.close()

    thread = threading.Thread(target=_run_async, daemon=True)
    thread.start()

    while True:
        item = event_queue.get()
        if isinstance(item, Exception):
            thread.join()
            raise item
        if item is None:
            break
        yield item

    thread.join()


def run_investigation_cli_streaming(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Run the investigation with real-time streaming UI and return the result.

    Uses ``astream_events`` + ``StreamRenderer`` so the local CLI shows
    the same live tool-call and reasoning updates as a remote investigation.
    """
    from app.remote.renderer import StreamRenderer

    events = stream_investigation_cli(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )
    renderer = StreamRenderer()
    final_state = renderer.render_stream(events)
    return {
        "report": final_state.get("slack_message", final_state.get("report", "")),
        "problem_md": final_state.get("problem_md", ""),
        "root_cause": final_state.get("root_cause", ""),
        "is_noise": final_state.get("is_noise", False),
    }
