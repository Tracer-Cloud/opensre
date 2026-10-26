"""Evidence mapping for the ``execute_aws_operation`` tool."""

from __future__ import annotations

from typing import Any

from integrations.aws.tools.aws_operation_tool import _map_aws_operation
from tools.registry import get_registered_tool


def test_successful_operation_is_citeable_with_qualified_label() -> None:
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "found": True,
        "service": "ecs",
        "operation": "describe_tasks",
        "result": {"tasks": [], "failures": []},
    }

    # Act
    _map_aws_operation(evidence, output, {})

    # Assert: the source must match the tool name so the report can cite it, and
    # the label carries service.operation to tell two AWS calls apart.
    entry = evidence["catalog_entries"][0]
    assert entry["source"] == "execute_aws_operation"
    assert entry["label"] == "AWS ecs.describe_tasks"
    assert entry["summary"] == "2 top-level keys"


def test_failed_operation_records_no_entry() -> None:
    # Arrange: the failure branch carries an error and no result, so an entry
    # here would be citeable noise the agent could mistake for data.
    evidence: dict[str, Any] = {}

    # Act
    _map_aws_operation(
        evidence,
        {"found": False, "service": "ecs", "operation": "describe_tasks", "error": "denied"},
        {},
    )

    # Assert
    assert evidence == {}


def test_missing_result_key_summarizes_as_empty() -> None:
    # Arrange
    evidence: dict[str, Any] = {}

    # Act
    _map_aws_operation(
        evidence, {"found": True, "service": "rds", "operation": "describe_db_instances"}, {}
    )

    # Assert
    assert evidence["catalog_entries"][0]["summary"] == "empty result"


def test_empty_result_summarizes_as_empty() -> None:
    # Arrange
    evidence: dict[str, Any] = {}

    # Act
    _map_aws_operation(
        evidence,
        {"found": True, "service": "rds", "operation": "describe_db_instances", "result": {}},
        {},
    )

    # Assert
    assert evidence["catalog_entries"][0]["summary"] == "empty result"


def test_summary_reports_shape_without_leaking_payload_values() -> None:
    # Arrange: an operation may return secret metadata or role policies, and the
    # summary is rendered into the report a human reads.
    evidence: dict[str, Any] = {}

    # Act
    _map_aws_operation(
        evidence,
        {
            "found": True,
            "service": "ec2",
            "operation": "describe_instances",
            "result": [{"InstanceId": "i-secret"}, {"InstanceId": "i-also-secret"}],
        },
        {},
    )

    # Assert
    summary = evidence["catalog_entries"][0]["summary"]
    assert summary == "2 records"
    assert "secret" not in summary


def test_single_item_result_is_not_pluralized() -> None:
    # Arrange: the summary reaches a human reader, so "1 records" is a defect.
    evidence: dict[str, Any] = {}

    # Act
    _map_aws_operation(
        evidence,
        {"found": True, "service": "iam", "operation": "get_role", "result": {"Role": {}}},
        {},
    )

    # Assert
    assert evidence["catalog_entries"][0]["summary"] == "1 top-level key"


def test_registered_tool_carries_the_mapper() -> None:
    # Arrange / Act
    registered_tool = get_registered_tool("execute_aws_operation")

    # Assert
    assert registered_tool is not None
    assert registered_tool.evidence_mapper is _map_aws_operation
