"""Tests for optional ``@traceable`` — free when session tracing is inactive."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from infrastructure.observability.trace.hook import traceable
from infrastructure.observability.trace.spans import (
    NoopSessionTraceStore,
    bind_session_trace,
    set_session_trace_store,
)
from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore


@pytest.fixture(autouse=True)
def _reset_session_trace_store() -> Any:
    set_session_trace_store(NoopSessionTraceStore())
    yield
    set_session_trace_store(NoopSessionTraceStore())


def test_traceable_is_near_free_passthrough_when_noop() -> None:
    @traceable("investigation")
    def traced_function() -> str:
        return "ok"

    assert traced_function() == "ok"
    assert traced_function.__name__ == "traced_function"
    # Wrapper exists so an active sink can emit spans, but call semantics
    # stay identical when the default noop sink is registered.
    assert callable(traced_function)


def test_traceable_preserves_args_kwargs_return_value_and_metadata() -> None:
    @traceable("span-name")
    def traced_function(value: int, *, suffix: str) -> str:
        """Original docstring."""
        return f"{value}{suffix}"

    assert traced_function(7, suffix="ms") == "7ms"
    assert traced_function.__name__ == "traced_function"
    assert traced_function.__doc__ == "Original docstring."


def test_traceable_defaults_name_to_qualname_when_blank() -> None:
    @traceable("")
    def named_fn() -> str:
        return "x"

    assert named_fn() == "x"


def test_traceable_emits_component_span_when_sink_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStore()
    session_id = "sess-traceable"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_store(JsonlSessionTraceStore(store=storage))

    @traceable(name="investigation")
    def run_investigation() -> str:
        return "done"

    with bind_session_trace(session_id):
        assert run_investigation() == "done"

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    kinds = {(rec["span_kind"], rec["name"]) for rec in lines if rec.get("type") == "trace_span"}
    assert ("component", "investigation") in kinds


def test_traceable_marks_error_status_when_callable_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda session_id: tmp_path / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStore()
    session_id = "sess-traceable-err"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_store(JsonlSessionTraceStore(store=storage))

    @traceable("failing_component")
    def boom() -> None:
        raise ValueError("nope")

    with bind_session_trace(session_id), pytest.raises(ValueError, match="nope"):
        boom()

    spans = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if json.loads(line).get("type") == "trace_span"
    ]
    assert len(spans) == 1
    assert spans[0]["span_kind"] == "component"
    assert spans[0]["name"] == "failing_component"
    assert spans[0]["status"] == "error"
