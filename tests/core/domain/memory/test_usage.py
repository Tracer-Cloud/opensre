"""Usage tracking: what counts as a use, and that recording never stalls a turn."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
from core.domain.memory import (
    record_memory_usage,
    render_prompt_index,
    render_relevant_memories,
    save_memory,
)
from core.domain.memory.files import memory_lock
from core.domain.memory.usage import load_usage


@pytest.fixture(autouse=True)
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    return tmp_path / "memory"


def _save(slug: str, description: str, body: str) -> None:
    assert save_memory(slug=slug, memory_type="infrastructure", description=description, body=body)


def test_showing_a_memory_in_the_prompt_counts_as_a_use() -> None:
    _save("redis-eviction", "Redis eviction policy", "allkeys-lru")
    _save("kafka-topics", "Kafka topic naming", "team.domain.event")

    render_relevant_memories("is redis eviction safe?")
    render_relevant_memories("what eviction does redis use?")

    usage = load_usage()
    assert usage["redis-eviction"].use_count == 2
    assert usage["redis-eviction"].last_used_at
    assert "kafka-topics" not in usage


def test_usage_orders_the_index_once_the_memories_change(monkeypatch: pytest.MonkeyPatch) -> None:
    stamp = "2026-09-01T08:00:00+00:00"
    monkeypatch.setattr("core.domain.memory.store._now_iso", lambda: stamp)
    _save("alpha-service", "Alpha service runbook", "body")
    _save("beta-service", "Beta service runbook", "body")
    assert render_prompt_index().splitlines()[0].startswith("- [infrastructure] alpha-service")

    record_memory_usage(["beta-service"], now=datetime(2026, 9, 2, tzinfo=UTC))
    _save("gamma-service", "Gamma service runbook", "body")

    assert render_prompt_index().splitlines()[0].startswith("- [infrastructure] beta-service")


def test_a_held_lock_skips_the_update_instead_of_stalling_the_turn() -> None:
    _save("redis-eviction", "Redis eviction policy", "allkeys-lru")

    with memory_lock():
        started = time.monotonic()
        record_memory_usage(["redis-eviction"])
        waited = time.monotonic() - started

    assert waited < 3.0
    assert "redis-eviction" not in load_usage()
