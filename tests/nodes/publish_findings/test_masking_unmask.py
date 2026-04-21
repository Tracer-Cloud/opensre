"""Test: generate_report unmasks slack_message before delivery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _enable_masking(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_MASK_ENABLED", "true")


def _state_with_masking() -> dict[str, object]:
    return {
        "alert_name": "pipeline failure",
        "pipeline_name": "pipeline",
        "severity": "warning",
        "problem_md": "# Incident in <NAMESPACE_0>",
        "slack_message": "",
        "report": "",
        "masking_map": {
            "<POD_0>": "etl-worker-7d9f8b-xkp2q",
            "<NAMESPACE_0>": "tracer-test",
        },
        "root_cause": "etl-worker-7d9f8b-xkp2q OOMKilled in tracer-test",
        "evidence": {},
        "context": {},
        "resolved_integrations": {},
        "slack_context": {},
        "discord_context": {},
        "validated_claims": [],
        "non_validated_claims": [],
    }


def test_slack_message_is_unmasked_before_delivery() -> None:
    from app.nodes.publish_findings import node as pub_node

    masked_message = "Root cause: <POD_0> crashed in <NAMESPACE_0>. Impact: customer-facing."

    with (
        patch.object(pub_node, "build_report_context", return_value=MagicMock()),
        patch.object(pub_node, "format_slack_message", return_value=masked_message),
        patch.object(pub_node, "build_slack_blocks", return_value=[]),
        patch.object(pub_node, "render_report"),
        patch.object(pub_node, "open_in_editor"),
        patch.object(pub_node, "send_ingest", return_value=None),
        patch("app.utils.slack_delivery.send_slack_report", return_value=(False, None)),
        patch("app.utils.slack_delivery.build_action_blocks", return_value=[]),
    ):
        result = pub_node.generate_report(_state_with_masking())  # type: ignore[arg-type]

    assert "<POD_0>" not in result["slack_message"]
    assert "<NAMESPACE_0>" not in result["slack_message"]
    assert "etl-worker-7d9f8b-xkp2q" in result["slack_message"]
    assert "tracer-test" in result["slack_message"]


def test_empty_masking_map_is_passthrough() -> None:
    from app.nodes.publish_findings import node as pub_node

    state = _state_with_masking()
    state["masking_map"] = {}
    message_without_placeholders = "Plain report with no placeholders."

    with (
        patch.object(pub_node, "build_report_context", return_value=MagicMock()),
        patch.object(pub_node, "format_slack_message", return_value=message_without_placeholders),
        patch.object(pub_node, "build_slack_blocks", return_value=[]),
        patch.object(pub_node, "render_report"),
        patch.object(pub_node, "open_in_editor"),
        patch.object(pub_node, "send_ingest", return_value=None),
        patch("app.utils.slack_delivery.send_slack_report", return_value=(False, None)),
        patch("app.utils.slack_delivery.build_action_blocks", return_value=[]),
    ):
        result = pub_node.generate_report(state)  # type: ignore[arg-type]

    assert result["slack_message"] == message_without_placeholders
