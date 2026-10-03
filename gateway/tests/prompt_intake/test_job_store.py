"""Tests for the prompt record file: what a killed writer may cost."""

from __future__ import annotations

from pathlib import Path

from gateway.core.prompt_intake import JsonlPromptJobStore


def _record(job_id: str, revision: int, state: str) -> dict[str, object]:
    return {"v": 1, "id": job_id, "revision": revision, "state": state}


def test_a_line_torn_by_a_killed_writer_costs_only_that_line(tmp_path: Path) -> None:
    # Arrange: a saved record, then a write cut off mid-line
    path = tmp_path / "prompt-jobs.jsonl"
    store = JsonlPromptJobStore(path)
    store.save(_record("p_a", 1, "needs_input"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"v":1,"id":"p_b","revis')

    # Act: the next write lands after the torn line, then the file is compacted
    store.save(_record("p_c", 1, "queued"))
    loaded_before = {record["id"] for record in store.load()}
    store.compact()

    # Assert: both whole records survive, and compaction drops the torn fragment
    assert loaded_before == {"p_a", "p_c"}
    assert {record["id"] for record in store.load()} == {"p_a", "p_c"}
    assert "p_b" not in path.read_text(encoding="utf-8")
