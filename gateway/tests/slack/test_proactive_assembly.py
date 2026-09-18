"""Slack adapters keep proactive reads and delivery on the originating thread."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from gateway.transports.slack.proactive import assembly


class _Messaging:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post_message(self, **kwargs: Any) -> str:
        self.posts.append(kwargs)
        return "200.2"


class _Runner:
    def __init__(self, *, context_reader: Any, delivery: Any) -> None:
        self.context_reader = context_reader
        self.delivery = delivery


def test_adapters_read_and_deliver_in_the_fixed_originating_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetches: list[dict[str, Any]] = []

    def _fetch(_target: object, **kwargs: Any) -> tuple[list[dict[str, str]], str]:
        fetches.append(kwargs)
        return ([{"text": "verified CI failure", "user": "U1", "ts": "100.2"}], "")

    def _runner(*, context_reader: Any, delivery: Any) -> _Runner:
        return _Runner(context_reader=context_reader, delivery=delivery)

    def _service(runner: _Runner) -> _Runner:
        return runner

    monkeypatch.setattr(assembly, "resolve_bot_token", lambda: (object(), ""))
    monkeypatch.setattr(assembly, "fetch_channel_messages", _fetch)
    monkeypatch.setattr(assembly, "ProactiveJudgementRunner", _runner)
    monkeypatch.setattr(assembly, "ProactiveMessageService", _service)
    messaging = _Messaging()

    runner = assembly.build_proactive_message_service(
        messaging=messaging,
        logger=logging.getLogger("test"),
    )
    context = runner.context_reader(channel_id="C1", thread_ts="100.1", limit=20)
    delivered = runner.delivery(channel_id="C1", thread_ts="100.1", message="follow-up")

    assert fetches == [{"channel_id": "C1", "thread_ts": "100.1", "limit": 20}]
    assert context["status"] == "read"
    assert context["message_count"] == 1
    assert messaging.posts == [{"channel": "C1", "thread_ts": "100.1", "text": "follow-up"}]
    assert delivered == "200.2"
