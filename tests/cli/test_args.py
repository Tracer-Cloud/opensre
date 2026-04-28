from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli.args import parse_args, write_json
from app.cli.constants import ALERT_TEMPLATE_CHOICES


def test_write_json_prints_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"status": "ok", "count": 2}

    write_json(payload, None)

    assert capsys.readouterr().out == json.dumps(payload, indent=2) + "\n"


def test_write_json_writes_to_file(tmp_path: Path) -> None:
    payload = {"status": "ok", "count": 2}
    output_path = tmp_path / "result.json"

    write_json(payload, str(output_path))

    assert output_path.read_text(encoding="utf-8") == json.dumps(payload, indent=2) + "\n"


@pytest.mark.parametrize(
    ("argv", "expected_error"),
    [
        (["--input", "alert.json", "--input-json", '{"alert":"test"}'], "--input-json"),
        (["--input", "alert.json", "--interactive"], "--interactive"),
        (
            ["--input-json", '{"alert":"test"}', "--print-template", ALERT_TEMPLATE_CHOICES[0]],
            "--print-template",
        ),
    ],
)
def test_parse_args_rejects_multiple_input_sources(
    argv: list[str], expected_error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(argv)

    assert exc_info.value.code == 2
    assert expected_error in capsys.readouterr().err


def test_parse_args_accepts_output_and_evaluate_flags() -> None:
    args = parse_args(["--input", "alert.json", "--output", "result.json", "--evaluate"])

    assert args.input == "alert.json"
    assert args.input_json is None
    assert args.interactive is False
    assert args.print_template is None
    assert args.output == "result.json"
    assert args.evaluate is True
