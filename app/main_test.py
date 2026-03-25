from __future__ import annotations

from argparse import Namespace
from io import StringIO

import pytest

from app.alert_templates import build_alert_template
from app.main import _load_payload, _parse_payload_text


def test_parse_payload_text_rejects_invalid_json() -> None:
    with pytest.raises(SystemExit, match="Invalid alert JSON"):
        _parse_payload_text("{bad json", "test")


def test_parse_payload_text_rejects_non_object_json() -> None:
    with pytest.raises(SystemExit, match="must be a JSON object"):
        _parse_payload_text('["not", "an", "object"]', "test")


def test_load_payload_accepts_inline_json() -> None:
    payload = _load_payload(
        Namespace(
            input=None,
            input_json='{"alert_name":"HighErrorRate","severity":"critical"}',
            interactive=False,
        )
    )

    assert payload["alert_name"] == "HighErrorRate"
    assert payload["severity"] == "critical"


def test_load_payload_reads_file(tmp_path) -> None:
    path = tmp_path / "alert.json"
    path.write_text('{"pipeline_name":"payments_etl"}', encoding="utf-8")

    payload = _load_payload(
        Namespace(
            input=str(path),
            input_json=None,
            interactive=False,
        )
    )

    assert payload["pipeline_name"] == "payments_etl"


def test_load_payload_missing_file_exits_cleanly() -> None:
    with pytest.raises(SystemExit, match="Alert JSON file not found"):
        _load_payload(
            Namespace(
                input="/tmp/does-not-exist-alert.json",
                input_json=None,
                interactive=False,
            )
        )


def test_load_payload_reads_interactive_input(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO('{"alert_name":"PastedAlert"}'))

    payload = _load_payload(
        Namespace(
            input=None,
            input_json=None,
            interactive=True,
        )
    )

    assert payload["alert_name"] == "PastedAlert"


def test_build_alert_template_for_cli_output() -> None:
    payload = build_alert_template("datadog")

    assert payload["alert_source"] == "datadog"
    assert payload["pipeline_name"] == "payments_etl"
