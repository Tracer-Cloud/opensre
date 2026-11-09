from __future__ import annotations

from typing import Any

from integrations.sqs.tools.sqs_queue_attributes_tool import _map_get_sqs_queue_attributes
from tools.registry import get_registered_tool


def _output(queues: list[dict[str, Any]]) -> dict[str, Any]:
    return {"source": "sqs", "available": True, "total_queues": len(queues), "queues": queues}


def test_records_entry_with_queue_totals() -> None:
    evidence: dict[str, Any] = {}
    queues: list[dict[str, Any]] = [
        {"name": "payments", "visible_count": 120, "in_flight_count": 4, "has_dlq": True},
        {"name": "emails", "visible_count": 0, "in_flight_count": 8, "has_dlq": False},
        {"name": "broken", "attributes_error": "denied"},
    ]

    _map_get_sqs_queue_attributes(evidence, _output(queues), {})

    assert evidence["sqs_queues"] == queues
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_sqs_queue_attributes"
    assert entries[0]["label"] == "SQS Queues"
    assert entries[0]["summary"] == (
        "3 queues, 120 visible, 12 in-flight, 1 with DLQ across 2 measured, 1 unreadable"
    )


def test_no_entry_when_no_queues() -> None:
    evidence: dict[str, Any] = {}

    _map_get_sqs_queue_attributes(evidence, _output([]), {})
    _map_get_sqs_queue_attributes(evidence, {"queues": "not-a-list"}, {})
    _map_get_sqs_queue_attributes(evidence, {}, {})

    assert evidence == {}


def test_tool_carries_mapper() -> None:
    registered = get_registered_tool("get_sqs_queue_attributes")
    assert registered is not None
    assert registered.evidence_mapper is _map_get_sqs_queue_attributes


def test_repeated_calls_accumulate_and_keep_one_entry() -> None:
    evidence: dict[str, Any] = {}
    first = [
        {
            "name": "payments",
            "url": "https://sqs/payments",
            "visible_count": 10,
            "in_flight_count": 0,
            "has_dlq": True,
        }
    ]
    second = [
        {
            "name": "payments",
            "url": "https://sqs/payments",
            "visible_count": 12,
            "in_flight_count": 0,
            "has_dlq": True,
        },
        {"name": "emails", "url": "https://sqs/emails", "visible_count": 3, "in_flight_count": 2},
    ]

    _map_get_sqs_queue_attributes(evidence, _output(first), {"queue_name_prefix": "pay"})
    _map_get_sqs_queue_attributes(evidence, _output(second), {"queue_name_prefix": ""})

    assert [q["name"] for q in evidence["sqs_queues"]] == ["payments", "emails"]
    assert evidence["sqs_queues"][0]["visible_count"] == 12
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["summary"] == "2 queues, 15 visible, 2 in-flight, 1 with DLQ"


def test_unreadable_queues_are_not_reported_as_zero() -> None:
    evidence: dict[str, Any] = {}
    queues: list[dict[str, Any]] = [
        {"name": "denied", "url": "https://sqs/denied", "attributes_error": "denied"},
        {
            "name": "partial",
            "url": "https://sqs/partial",
            "visible_count": None,
            "in_flight_count": None,
            "has_dlq": False,
        },
    ]

    _map_get_sqs_queue_attributes(evidence, _output(queues), {})

    summary = evidence["catalog_entries"][0]["summary"]
    assert summary == "2 queues, 2 unreadable"
    assert "visible" not in summary
