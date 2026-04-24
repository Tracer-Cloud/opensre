"""Tests for alert text formatting and enrichment (OpenSRE / structured payloads)."""

from __future__ import annotations

from app.nodes.extract_alert.extract import _format_raw_alert
from app.nodes.extract_alert.extract_node import _enrich_raw_alert
from app.nodes.extract_alert.models import AlertDetails


def test_format_raw_alert_openrca_keeps_json_not_text_only() -> None:
    alert = {
        "text": "Only the narrative paragraph.",
        "alert_source": "openrca_dataset",
        "commonLabels": {"pipeline_name": "market/cloudbed-1"},
        "pipeline_name": "market/cloudbed-1",
    }
    formatted = _format_raw_alert(alert)
    assert "openrca_dataset" in formatted
    assert "commonLabels" in formatted


def test_format_raw_alert_slack_text_only_when_no_structure() -> None:
    alert = {"text": "Hello team, the job failed."}
    assert _format_raw_alert(alert) == "Hello team, the job failed."


def test_enrich_preserves_openrca_alert_source_when_llm_guesses_eks() -> None:
    raw = {
        "alert_source": "openrca_dataset",
        "commonLabels": {"pipeline_name": "market/cloudbed-1"},
    }
    details = AlertDetails(
        is_noise=False,
        alert_name="x",
        pipeline_name="cloudbed-1",
        severity="high",
        alert_source="eks",
    )
    out = _enrich_raw_alert(raw, details)
    assert out["alert_source"] == "openrca_dataset"
