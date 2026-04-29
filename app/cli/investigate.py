"""Shared investigation helpers for CLI entrypoints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from langsmith import traceable

from app.config import LLMSettings

if TYPE_CHECKING:
    from app.remote.stream import StreamEvent
    from app.state import AgentState

_logger = logging.getLogger(__name__)


def _call_run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    *,
    raw_alert: dict[str, Any],
    opensre_evaluate: bool = False,
) -> AgentState:
    """Import the heavy investigation runner only when execution starts."""
    from app.pipeline.runners import run_investigation

    return run_investigation(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
        opensre_evaluate=opensre_evaluate,
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
    opensre_evaluate: bool = False,
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
        opensre_evaluate=opensre_evaluate,
    )
    slack_message = state["slack_message"]
    out: dict[str, Any] = {
        "report": slack_message,
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
        "is_noise": state.get("is_noise", False),
    }
    if opensre_evaluate:
        ev = state.get("opensre_llm_eval")
        if isinstance(ev, dict) and ev:
            out["opensre_llm_eval"] = ev
        elif not (state.get("opensre_eval_rubric") or "").strip():
            out["opensre_llm_eval"] = {
                "skipped": True,
                "reason": (
                    "No scoring_points on this alert — nothing to judge against "
                    "(not an OpenRCA rubric payload, or field missing)."
                ),
            }
        else:
            out["opensre_llm_eval"] = {
                "skipped": True,
                "reason": "Evaluate was enabled but no judge output was recorded.",
            }
    return out


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
    renderer = StreamRenderer(local=True)
    final_state = renderer.render_stream(events)
    return {
        "report": final_state.get("slack_message", final_state.get("report", "")),
        "problem_md": final_state.get("problem_md", ""),
        "root_cause": final_state.get("root_cause", ""),
        "is_noise": final_state.get("is_noise", False),
    }


def run_investigation_for_session(
    *,
    alert_text: str,
    context_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a streaming investigation from a free-text alert description.

    Used by the REPL loop: wraps the user's text as the alert payload, runs
    the full pipeline with live streaming, and returns the final state so
    follow-ups and context accumulation can reference it.

    KeyboardInterrupt in the main thread is forwarded to the background
    asyncio loop as a task cancel, so Ctrl+C unwinds the in-flight LangGraph
    run cleanly instead of leaving it orphaned.
    """
    import queue
    import threading

    from app.pipeline.runners import astream_investigation
    from app.remote.renderer import StreamRenderer

    LLMSettings.from_env()
    raw_alert: dict[str, Any] = {"alert_name": "Interactive session", "message": alert_text}
    if context_overrides:
        raw_alert.setdefault("annotations", {}).update(context_overrides)

    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=None,
        pipeline_name=None,
        severity=None,
    )

    event_queue: queue.Queue[StreamEvent | BaseException | None] = queue.Queue()
    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    pump_task_ref: dict[str, asyncio.Task[None]] = {}

    def _run_async() -> None:
        loop = asyncio.new_event_loop()
        loop_ref["loop"] = loop
        try:

            async def _pump() -> None:
                async for evt in astream_investigation(
                    resolved_alert_name,
                    resolved_pipeline_name,
                    resolved_severity,
                    raw_alert=raw_alert,
                ):
                    event_queue.put(evt)

            task = loop.create_task(_pump())
            pump_task_ref["task"] = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                event_queue.put(KeyboardInterrupt("investigation cancelled"))
        except Exception as exc:  # noqa: BLE001
            event_queue.put(exc)
        finally:
            event_queue.put(None)
            loop.close()

    thread = threading.Thread(target=_run_async, daemon=True)
    thread.start()

    def _cancel_pump() -> None:
        loop = loop_ref.get("loop")
        task = pump_task_ref.get("task")
        if loop is None or task is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(task.cancel)

    def _events() -> Iterator[StreamEvent]:
        try:
            while True:
                item = event_queue.get()
                if isinstance(item, BaseException):
                    raise item
                if item is None:
                    return
                yield item
        except KeyboardInterrupt:
            _cancel_pump()
            raise

    renderer = StreamRenderer(local=True)
    try:
        final_state = renderer.render_stream(_events())
    except KeyboardInterrupt:
        _cancel_pump()
        raise
    finally:
        # Always join so unexpected exceptions from render_stream don't leak
        # the daemon thread and leave an orphaned LLM call running.
        thread.join(timeout=5)
        if thread.is_alive():
            _logger.warning(
                "investigation thread did not terminate within 5s after cancellation; "
                "an LLM call may still be in flight"
            )
    return dict(final_state)
