from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from app.cli.args import parse_args, write_json


def test_parse_args_input_group() -> None:
    args = parse_args(["--input", "alert.json"])
    assert args.input == "alert.json"
    assert args.input_json is None
    assert args.interactive is False
    assert args.print_template is None

    args = parse_args(["--input-json", '{"title": "test"}'])
    assert args.input_json == '{"title": "test"}'

    args = parse_args(["--interactive"])
    assert args.interactive is True

    args = parse_args(["--print-template", "datadog"])
    assert args.print_template == "datadog"

    with pytest.raises(SystemExit):
        parse_args(["--input", "alert.json", "--interactive"])


def test_parse_args_evaluate() -> None:
    args = parse_args(["--evaluate"])
    assert args.evaluate is True

    args = parse_args([])
    assert args.evaluate is False


def test_write_json_to_file(tmp_path: Path) -> None:
    output_file = tmp_path / "output.json"
    data = {"key": "value"}
    write_json(data, str(output_file))

    assert output_file.exists()
    assert json.loads(output_file.read_text(encoding="utf-8")) == data


def test_write_json_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    data = {"key": "value"}
    write_json(data, None)

    captured = capsys.readouterr()
    assert json.loads(captured.out) == data
