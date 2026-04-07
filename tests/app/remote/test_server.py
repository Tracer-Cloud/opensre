from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.remote.server import InvestigateRequest, investigate_stream
from app.remote.stream import StreamEvent


@pytest.mark.asyncio
async def test_investigate_stream_persists_state_on_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: dict[str, Any] = {}

    async def fake_astream_investigation(*args: object, **kwargs: object):
        yield StreamEvent(
            "events",
            data={"data": {"output": {"root_cause": "Schema mismatch", "report": "Fix upstream"}}},
            kind="on_chain_end",
        )
        await asyncio.sleep(0)
        yield StreamEvent("events", data={"data": {}}, kind="on_tool_start")

    def fake_persist_streamed_result(**kwargs: Any) -> None:
        persisted.update(kwargs)

    monkeypatch.setattr("app.config.LLMSettings.from_env", object)
    monkeypatch.setattr(
        "app.cli.investigate.resolve_investigation_context",
        lambda **_kwargs: ("test-alert", "etl_daily_orders", "critical"),
    )
    monkeypatch.setattr(
        "app.pipeline.runners.astream_investigation",
        fake_astream_investigation,
    )
    monkeypatch.setattr(
        "app.remote.server._persist_streamed_result",
        fake_persist_streamed_result,
    )

    response = await investigate_stream(InvestigateRequest(raw_alert={"alert_name": "PayloadAlert"}))
    iterator = response.body_iterator

    first_chunk = await anext(iterator)
    assert first_chunk

    await iterator.aclose()
    await asyncio.sleep(0)

    assert persisted["alert_name"] == "test-alert"
    assert persisted["pipeline_name"] == "etl_daily_orders"
    assert persisted["severity"] == "critical"
    assert persisted["state"]["root_cause"] == "Schema mismatch"
    assert persisted["state"]["report"] == "Fix upstream"
