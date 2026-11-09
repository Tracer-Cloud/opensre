"""Concurrency coverage for shared feedback persistence."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from gateway.core.storage.feedback import append_feedback_entry

_THREAD_TIMEOUT_SECONDS = 1.0


def test_concurrent_feedback_entries_remain_distinct_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "feedback.jsonl"
    results: list[bool] = []
    results_lock = threading.Lock()

    def append(index: int) -> None:
        written = append_feedback_entry({"index": index}, path=path)
        with results_lock:
            results.append(written)

    workers = [threading.Thread(target=append, args=(index,)) for index in range(20)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(_THREAD_TIMEOUT_SECONDS)

    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 20
    assert all(results)
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {entry["index"] for entry in entries} == set(range(20))


def test_feedback_write_failure_is_contained(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    assert not append_feedback_entry({"verdict": "good"}, path=directory)


def test_feedback_serialization_failure_is_contained(tmp_path: Path) -> None:
    path = tmp_path / "feedback.jsonl"

    assert not append_feedback_entry({"unsupported": object()}, path=path)
    assert path.read_text(encoding="utf-8") == ""
