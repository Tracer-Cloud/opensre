from __future__ import annotations

import json
from pathlib import Path

import pytest

from surfaces.cli.args import write_json


def test_write_json_prints_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"status": "ok", "count": 2}

    write_json(payload, None)

    assert capsys.readouterr().out == json.dumps(payload, indent=2) + "\n"


def test_write_json_writes_to_file(tmp_path: Path) -> None:
    payload = {"status": "ok", "count": 2}
    output_path = tmp_path / "result.json"

    write_json(payload, str(output_path))

    assert output_path.read_text(encoding="utf-8") == json.dumps(payload, indent=2) + "\n"
