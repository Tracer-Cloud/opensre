"""Tests for SQSQueueAttributesTool (function-based, @tool decorated).

Covers the acceptance criteria from #2803: schema shape, availability
(planner-selectable off the account-level ``aws`` integration), and extraction,
plus runtime behavior — attribute normalization, the stuck-consumer /
poison-pill fixture, per-queue error isolation, and the synthetic
``aws_backend`` short-circuit that keeps scenario runs off real AWS.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.sqs import (
    DEFAULT_SQS_MAX_QUEUES,
    DEFAULT_SQS_REGION,
    coerce_sqs_max_queues,
    sqs_extract_params,
    sqs_is_available,
)
from integrations.sqs.tools.sqs_queue_attributes_tool import get_sqs_queue_attributes
from tests.tools.conftest import BaseToolContract

_RT = get_sqs_queue_attributes.__opensre_registered_tool__

_TOOL_PATH = "integrations.sqs.tools.sqs_queue_attributes_tool.execute_aws_sdk_call"

_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/payments-processing"
_DLQ_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/payments-dlq"


def _list_ok(*urls: str, next_token: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {"QueueUrls": list(urls)}
    if next_token:
        data["NextToken"] = next_token
    return {"success": True, "data": data}


def _attrs_ok(**attrs: str) -> dict[str, Any]:
    return {"success": True, "data": {"Attributes": dict(attrs)}}


class _FakeAWSBackend:
    """Minimal AWSBackend stand-in that records get_sqs_queue_attributes calls."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get_sqs_queue_attributes(
        self,
        queue_name_prefix: str = "",
        max_queues: int = DEFAULT_SQS_MAX_QUEUES,
        region: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "queue_name_prefix": queue_name_prefix,
                "max_queues": max_queues,
                "region": region,
            }
        )
        return self.response


class TestSQSQueueAttributesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _RT


# ─────────────────────────────────────────────────────────────────────────────
# Discovery + schema shape
# ─────────────────────────────────────────────────────────────────────────────


def test_tool_is_registered_on_chat_surface() -> None:
    assert _RT.name == "get_sqs_queue_attributes"
    assert _RT.source == "sqs"
    assert set(_RT.surfaces) == {"chat"}


def test_input_schema_exposes_expected_params() -> None:
    props = _RT.input_schema["properties"]
    assert set(props) == {"queue_name_prefix", "max_queues", "region"}
    assert _RT.input_schema.get("required", []) == []
    assert props["max_queues"]["minimum"] == 1
    assert props["max_queues"]["maximum"] == 100
    assert props["max_queues"]["default"] == DEFAULT_SQS_MAX_QUEUES


def test_injected_backend_is_hidden_from_the_planner() -> None:
    """aws_backend is runtime-injected, so the LLM must never see it."""
    assert _RT.injected_params == ("aws_backend",)
    assert "aws_backend" not in _RT.public_input_schema.get("properties", {})


# ─────────────────────────────────────────────────────────────────────────────
# Availability — rides on the account-level ``aws`` integration
# ─────────────────────────────────────────────────────────────────────────────


def test_available_with_generic_aws_integration_only() -> None:
    """Regression: SQS has no per-resource identifier, so a plain ``aws``
    integration (the common single-credential setup) must enable the tool.
    Requiring a dedicated ``sqs`` record would leave it permanently dark."""
    assert sqs_is_available({"aws": {"connection_verified": True}}) is True
    assert sqs_is_available({"aws": {"role_arn": "arn:aws:iam::1:role/r"}}) is True
    assert sqs_is_available({"aws": {"credentials": {"access_key_id": "AK"}}}) is True


def test_available_with_synthetic_backend() -> None:
    assert sqs_is_available({"aws": {"ec2_backend": object()}}) is True


def test_unavailable_without_aws_config() -> None:
    assert sqs_is_available({}) is False
    assert sqs_is_available({"aws": {}}) is False


# ─────────────────────────────────────────────────────────────────────────────
# Param extraction
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_params_prefers_explicit_region() -> None:
    params = sqs_extract_params({"aws": {"region": "eu-west-1"}})
    assert params["region"] == "eu-west-1"
    assert params["aws_backend"] is None


def test_extract_params_falls_back_to_default_region(monkeypatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    assert sqs_extract_params({"aws": {}})["region"] == DEFAULT_SQS_REGION


def test_extract_params_forwards_synthetic_backend() -> None:
    backend = object()
    assert sqs_extract_params({"aws": {"ec2_backend": backend}})["aws_backend"] is backend


def test_coerce_max_queues_clamps_and_defaults() -> None:
    assert coerce_sqs_max_queues(5) == 5
    assert coerce_sqs_max_queues(0) == 1
    assert coerce_sqs_max_queues(5000) == 100
    assert coerce_sqs_max_queues("garbage") == DEFAULT_SQS_MAX_QUEUES
    assert coerce_sqs_max_queues(None) == DEFAULT_SQS_MAX_QUEUES


# ─────────────────────────────────────────────────────────────────────────────
# Runtime behavior
# ─────────────────────────────────────────────────────────────────────────────


@patch(_TOOL_PATH)
def test_normal_backlog_is_parsed(mock_call) -> None:
    mock_call.side_effect = [
        _list_ok(_QUEUE_URL),
        _attrs_ok(
            ApproximateNumberOfMessages="4821",
            ApproximateNumberOfMessagesNotVisible="12",
            VisibilityTimeout="300",
            RedrivePolicy='{"deadLetterTargetArn":"arn:aws:sqs:us-east-1:1:dlq","maxReceiveCount":5}',
        ),
    ]

    result = get_sqs_queue_attributes(queue_name_prefix="payments-")

    assert result["available"] is True
    assert result["total_queues"] == 1
    queue = result["queues"][0]
    assert queue["name"] == "payments-processing"
    assert queue["visible_count"] == 4821
    assert queue["in_flight_count"] == 12
    assert queue["visibility_timeout_seconds"] == 300
    assert queue["has_dlq"] is True
    assert queue["redrive_policy"]["maxReceiveCount"] == 5
    assert queue["is_fifo"] is False


@patch(_TOOL_PATH)
def test_stuck_consumer_poison_pill_shape_is_surfaced(mock_call) -> None:
    """The #2803 incident shape: queue looks empty, every consumer is pinned
    holding the same redelivered message, and there is no DLQ to break the cycle."""
    mock_call.side_effect = [
        _list_ok(_QUEUE_URL),
        _attrs_ok(
            ApproximateNumberOfMessages="0",
            ApproximateNumberOfMessagesNotVisible="3",
            VisibilityTimeout="30",
        ),
    ]

    queue = get_sqs_queue_attributes()["queues"][0]

    assert queue["visible_count"] == 0
    assert queue["in_flight_count"] == 3
    assert queue["visibility_timeout_seconds"] == 30
    assert queue["has_dlq"] is False
    assert queue["redrive_policy"] is None


@patch(_TOOL_PATH)
def test_missing_numeric_attributes_are_none_not_zero(mock_call) -> None:
    """Absent != empty: a missing count must not read as a real measurement of 0."""
    mock_call.side_effect = [_list_ok(_QUEUE_URL), _attrs_ok()]

    queue = get_sqs_queue_attributes()["queues"][0]

    assert queue["visible_count"] is None
    assert queue["in_flight_count"] is None


@patch(_TOOL_PATH)
def test_malformed_redrive_policy_still_reports_a_dlq(mock_call) -> None:
    mock_call.side_effect = [
        _list_ok(_QUEUE_URL),
        _attrs_ok(RedrivePolicy="not-json"),
    ]

    queue = get_sqs_queue_attributes()["queues"][0]

    assert queue["has_dlq"] is True
    assert queue["redrive_policy"] == {"raw": "not-json"}


@patch(_TOOL_PATH)
def test_fifo_queue_detected_from_attribute(mock_call) -> None:
    mock_call.side_effect = [
        _list_ok(f"{_QUEUE_URL}.fifo"),
        _attrs_ok(FifoQueue="true"),
    ]

    assert get_sqs_queue_attributes()["queues"][0]["is_fifo"] is True


@patch(_TOOL_PATH)
def test_prefix_is_passed_through_and_omitted_when_blank(mock_call) -> None:
    mock_call.side_effect = [_list_ok(), _list_ok()]

    get_sqs_queue_attributes(queue_name_prefix="payments-")
    assert mock_call.call_args.kwargs["parameters"]["QueueNamePrefix"] == "payments-"

    get_sqs_queue_attributes()
    assert "QueueNamePrefix" not in mock_call.call_args.kwargs["parameters"]


@patch(_TOOL_PATH)
def test_list_queues_failure_returns_unavailable_without_raising(mock_call) -> None:
    mock_call.return_value = {"success": False, "error": "AccessDenied: sqs:ListQueues"}

    result = get_sqs_queue_attributes()

    assert result["available"] is False
    assert result["source"] == "sqs"
    assert result["queues"] == []
    # Upstream detail stays in the logs, not in the agent-facing payload.
    assert "AccessDenied" not in result["error"]


@patch(_TOOL_PATH)
def test_one_unreadable_queue_does_not_sink_the_others(mock_call) -> None:
    mock_call.side_effect = [
        _list_ok(_QUEUE_URL, _DLQ_URL),
        {"success": False, "error": "AccessDenied"},
        _attrs_ok(ApproximateNumberOfMessages="7"),
    ]

    result = get_sqs_queue_attributes()

    assert result["available"] is True
    assert result["total_queues"] == 2
    assert "attributes_error" in result["queues"][0]
    assert result["queues"][1]["visible_count"] == 7


@patch(_TOOL_PATH)
def test_empty_account_returns_no_queues(mock_call) -> None:
    mock_call.return_value = _list_ok()

    result = get_sqs_queue_attributes()

    assert result["available"] is True
    assert result["queues"] == []
    assert result["total_queues"] == 0


@patch(_TOOL_PATH)
def test_sanitizer_truncation_marker_is_not_treated_as_a_queue(mock_call) -> None:
    """The transport sanitizer can append a '... (N more items truncated)'
    string to an oversized list; it is not a queue URL."""
    mock_call.side_effect = [
        {"success": True, "data": {"QueueUrls": [_QUEUE_URL, "... (40 more items truncated)"]}},
        _attrs_ok(ApproximateNumberOfMessages="1"),
    ]

    result = get_sqs_queue_attributes()

    assert result["total_queues"] == 1
    assert result["queues"][0]["name"] == "payments-processing"


@patch(_TOOL_PATH)
def test_max_queues_bounds_the_per_queue_fanout(mock_call) -> None:
    # Real ListQueues honors MaxResults server-side, so it never returns more
    # than max_queues URLs regardless of how many queues actually match.
    urls = [f"https://sqs.us-east-1.amazonaws.com/1/q{i}" for i in range(2)]
    mock_call.side_effect = [_list_ok(*urls), *[_attrs_ok() for _ in range(2)]]

    result = get_sqs_queue_attributes(max_queues=2)

    assert result["total_queues"] == 2
    # 1 list call + exactly 2 attribute calls, not 5.
    assert mock_call.call_count == 3


@patch(_TOOL_PATH)
def test_truncated_reflects_next_token_not_url_count(mock_call) -> None:
    """Regression: ListQueues already caps results at max_queues, so a URL-count
    comparison against max_queues can never be true — it must be truncated=false
    even when a real page happens to be exactly max_queues long. The only valid
    truncation signal is whether AWS returned a NextToken."""
    urls = [f"https://sqs.us-east-1.amazonaws.com/1/q{i}" for i in range(2)]
    mock_call.side_effect = [
        _list_ok(*urls, next_token="page-2"),
        *[_attrs_ok() for _ in range(2)],
    ]

    result = get_sqs_queue_attributes(max_queues=2)

    assert result["total_queues"] == 2
    assert result["truncated"] is True


@patch(_TOOL_PATH)
def test_not_truncated_when_no_next_token(mock_call) -> None:
    urls = [f"https://sqs.us-east-1.amazonaws.com/1/q{i}" for i in range(2)]
    mock_call.side_effect = [_list_ok(*urls), *[_attrs_ok() for _ in range(2)]]

    result = get_sqs_queue_attributes(max_queues=2)

    assert result["truncated"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic backend short-circuit
# ─────────────────────────────────────────────────────────────────────────────


@patch(_TOOL_PATH)
def test_backend_short_circuits_and_never_calls_boto3(mock_call) -> None:
    backend = _FakeAWSBackend({"source": "sqs", "available": True, "queues": []})

    result = get_sqs_queue_attributes(
        queue_name_prefix="payments-",
        region="eu-west-1",
        aws_backend=backend,
    )

    assert result == {"source": "sqs", "available": True, "queues": []}
    mock_call.assert_not_called()
    assert backend.calls == [
        {
            "queue_name_prefix": "payments-",
            "max_queues": DEFAULT_SQS_MAX_QUEUES,
            "region": "eu-west-1",
        }
    ]
