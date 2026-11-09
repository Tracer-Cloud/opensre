from __future__ import annotations

import json

from infrastructure.analytics.prompt_log.sinks.local_jsonl import (
    append_prompt_log_record,
)


def test_append_prompt_log_record_writes_jsonl(tmp_path) -> None:
    log_path = tmp_path / "prompt_log.jsonl"
    append_prompt_log_record(path=log_path, record={"prompt": "hello", "response": "world"})
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["prompt"] == "hello"
    assert payload["response"] == "world"


def test_append_prompt_log_record_rotates_when_size_exceeded(tmp_path) -> None:
    log_path = tmp_path / "prompt_log.jsonl"
    log_path.write_text("x" * 200, encoding="utf-8")
    append_prompt_log_record(
        path=log_path,
        record={"prompt": "hello", "response": "world"},
        max_bytes=100,
    )
    backup = log_path.with_name(log_path.name + ".1")
    assert backup.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
