"""Report where agent turn time goes, read from recorded session traces.

Run from the repository root:

    uv run python -m tests.benchmarks.agent_latency.trace_report

It reads the ``trace_span`` records the interactive shell already writes to each
session log and makes no model, network, or provider call. A turn's wall time is
mostly model round trips, so the report counts them: how many a turn needs, how
many tool calls each response carries, and how many responses did nothing but
write the plan or search for a tool.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config.constants.tool_discovery import TOOL_SEARCH_NAME
from core.agent_harness.session.persistence.paths import sessions_dir
from core.agent_harness.task_plan.evidence import UPDATE_PLAN_TOOL

REPORT_SCHEMA_VERSION = 1
DEFAULT_SESSION_COUNT = 50

#: Tools whose call moves no work forward when it is the only call of a response.
_BOOKKEEPING_ONLY_TOOLS = frozenset({UPDATE_PLAN_TOOL, TOOL_SEARCH_NAME})
#: The model-span name of a call with no model behind it (a scripted slash turn).
_SCRIPTED_MODEL_NAME = "invoke"
#: A call that ran this long and reported no usage waited on a request that never answered.
_STALLED_CALL_MS = 60_000


@dataclass(frozen=True, slots=True)
class LatencyReport:
    """Round-trip counts and timings over a set of recorded turns."""

    sessions: int = 0
    #: Turns that made at least one real model call.
    turns: int = 0
    model_calls: int = 0
    turn_ms_p50: int = 0
    turn_ms_p90: int = 0
    model_call_ms_p50: int = 0
    model_call_ms_p90: int = 0
    #: ``None`` when no recorded call was streamed.
    first_event_ms_p50: int | None = None
    model_calls_per_turn_p50: int = 0
    model_calls_per_turn_p90: int = 0
    #: Share of turn wall time spent waiting on the model.
    model_time_share: float = 0.0
    #: Responses by how many tool calls they carried.
    tool_calls_per_response: dict[int, int] = field(default_factory=dict)
    #: Of the responses that called a tool, the share that called more than one.
    batched_response_share: float = 0.0
    #: Responses whose only calls wrote the plan or searched for a tool.
    bookkeeping_only_calls: int = 0
    #: Calls that waited at least a minute and returned no usage.
    stalled_calls: int = 0


@dataclass(slots=True)
class _Response:
    """One model call and the tools its response ran."""

    duration_ms: int
    attributes: dict[str, Any]
    tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Turn:
    wall_ms: int
    responses: list[_Response]


def _percentile(values: Sequence[int], quantile: float) -> int:
    """Nearest-rank percentile; ``0`` for no values."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * quantile), len(ordered) - 1)]


def _spans(path: Path) -> Iterator[dict[str, Any]]:
    """Timed trace spans of one session log, in the order they closed."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line cut short by a crash
            if (
                isinstance(record, dict)
                and record.get("type") == "trace_span"
                and isinstance(record.get("duration_ms"), int)
            ):
                yield record


def _turns(spans: Iterable[dict[str, Any]]) -> Iterator[_Turn]:
    """Turns that made a real model call, rebuilt from spans in closing order.

    A span closes after the spans it contains: a model call, then the tools its
    response ran, then the loop that held them. A turn whose loop never closed
    (the process died mid-turn) has no wall time and is left out.
    """
    responses: list[_Response] = []
    for span in spans:
        kind, name = span.get("span_kind"), str(span.get("name"))
        if kind == "llm" and name != _SCRIPTED_MODEL_NAME:
            responses.append(_Response(int(span["duration_ms"]), span.get("attributes") or {}))
        elif kind == "tool" and responses:
            responses[-1].tools.append(name)
        elif kind == "loop":
            if responses:
                yield _Turn(int(span["duration_ms"]), responses)
            responses = []


def summarize_sessions(paths: Iterable[Path]) -> LatencyReport:
    """Summarize the recorded turns of the given session logs."""
    session_count = 0
    turns: list[_Turn] = []
    for path in paths:
        session_count += 1
        turns.extend(_turns(_spans(path)))

    responses = [response for turn in turns for response in turn.responses]
    call_ms = [response.duration_ms for response in responses]
    wall_ms = [turn.wall_ms for turn in turns]
    calls_per_turn = [len(turn.responses) for turn in turns]
    first_event_ms = [
        value
        for response in responses
        if isinstance(value := response.attributes.get("first_event_ms"), int)
    ]
    tool_calls_per_response = Counter(
        count
        for response in responses
        if isinstance(count := response.attributes.get("tool_call_count"), int)
    )
    with_tools = sum(n for calls, n in tool_calls_per_response.items() if calls >= 1)
    batched = sum(n for calls, n in tool_calls_per_response.items() if calls > 1)
    return LatencyReport(
        sessions=session_count,
        turns=len(turns),
        model_calls=len(responses),
        turn_ms_p50=_percentile(wall_ms, 0.5),
        turn_ms_p90=_percentile(wall_ms, 0.9),
        model_call_ms_p50=_percentile(call_ms, 0.5),
        model_call_ms_p90=_percentile(call_ms, 0.9),
        first_event_ms_p50=_percentile(first_event_ms, 0.5) if first_event_ms else None,
        model_calls_per_turn_p50=_percentile(calls_per_turn, 0.5),
        model_calls_per_turn_p90=_percentile(calls_per_turn, 0.9),
        model_time_share=round(sum(call_ms) / sum(wall_ms), 3) if sum(wall_ms) else 0.0,
        tool_calls_per_response=dict(sorted(tool_calls_per_response.items())),
        batched_response_share=round(batched / with_tools, 3) if with_tools else 0.0,
        bookkeeping_only_calls=sum(
            1
            for response in responses
            if response.tools and all(tool in _BOOKKEEPING_ONLY_TOOLS for tool in response.tools)
        ),
        stalled_calls=sum(
            1
            for response in responses
            if response.attributes.get("input_tokens") is None
            and response.duration_ms >= _STALLED_CALL_MS
        ),
    )


def recent_session_logs(directory: Path, count: int) -> list[Path]:
    """The ``count`` most recently written session logs under ``directory``."""
    logs = sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[:count]


def report_payload(report: LatencyReport) -> dict[str, Any]:
    """The report as JSON-serializable data, versioned for later comparison."""
    return {"schema_version": REPORT_SCHEMA_VERSION, **asdict(report)}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Directory of session logs (default: this installation's sessions).",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=DEFAULT_SESSION_COUNT,
        help=f"How many of the most recent sessions to read (default {DEFAULT_SESSION_COUNT}).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    directory = args.dir if args.dir is not None else sessions_dir()
    report = summarize_sessions(recent_session_logs(directory, max(args.sessions, 1)))
    print(json.dumps(report_payload(report), indent=2))


if __name__ == "__main__":
    main()
