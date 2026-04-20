from __future__ import annotations

import pathlib
from io import StringIO

import pytest

from app.cli.alert_templates import build_alert_template
from app.cli.payload import load_file, load_payload, parse_payload_text


def test_parse_payload_text_rejects_invalid_json() -> None:
    with pytest.raises(SystemExit, match="Invalid alert JSON"):
        parse_payload_text("{bad json", "test")


def test_parse_payload_text_rejects_non_object_json() -> None:
    with pytest.raises(SystemExit, match="must be a JSON object"):
        parse_payload_text('["not", "an", "object"]', "test")


def test_load_payload_accepts_inline_json() -> None:
    payload = load_payload(
        input_path=None,
        input_json='{"alert_name":"HighErrorRate","severity":"critical"}',
        interactive=False,
    )
    assert payload["alert_name"] == "HighErrorRate"
    assert payload["severity"] == "critical"


def test_load_payload_reads_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "alert.json"
    path.write_text('{"pipeline_name":"payments_etl"}', encoding="utf-8")

    payload = load_payload(input_path=str(path), input_json=None, interactive=False)
    assert payload["pipeline_name"] == "payments_etl"


def test_load_payload_missing_file_exits_cleanly() -> None:
    with pytest.raises(SystemExit, match="Alert file not found"):
        load_payload(
            input_path="/tmp/does-not-exist-alert.json",
            input_json=None,
            interactive=False,
        )


def test_load_payload_reads_interactive_input(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO('{"alert_name":"PastedAlert"}'))

    payload = load_payload(input_path=None, input_json=None, interactive=True)
    assert payload["alert_name"] == "PastedAlert"


def test_load_file_extracts_json_from_markdown(tmp_path) -> None:
    md = tmp_path / "alert.md"
    md.write_text('# Alert\n```json\n{"title":"MyAlert"}\n```\n', encoding="utf-8")

    payload = load_file(str(md))
    assert payload["title"] == "MyAlert"


def test_load_file_returns_raw_text_for_plain_markdown(tmp_path) -> None:
    md = tmp_path / "notes.md"
    md.write_text("# Some alert\nNo JSON here.", encoding="utf-8")

    payload = load_file(str(md))
    assert "raw_text" in payload


def test_build_alert_template_for_cli_output() -> None:
    payload = build_alert_template("datadog")
    assert payload["alert_source"] == "datadog"
    assert payload["pipeline_name"] == "payments_etl"


def test_build_alert_template_generic_for_cli_output() -> None:
    payload = build_alert_template("generic")
    assert payload["alert_source"] == "generic"
    assert payload["pipeline_name"] == "payments_etl"


def test_build_alert_template_grafana_for_cli_output() -> None:
    payload = build_alert_template("grafana")
    assert payload["alert_source"] == "grafana"
    assert payload["pipeline_name"] == "payments_etl"


def test_build_alert_template_honeycomb_for_cli_output() -> None:
    payload = build_alert_template("honeycomb")
    assert payload["alert_source"] == "honeycomb"
    assert payload["pipeline_name"] == "checkout_api"


def test_build_alert_template_coralogix_for_cli_output() -> None:
    payload = build_alert_template("coralogix")
    assert payload["alert_source"] == "coralogix"
    assert payload["pipeline_name"] == "payments_worker"
