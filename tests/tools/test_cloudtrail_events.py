"""Tests for CloudTrailEventsTool (function-based, @tool decorated).

Covers the acceptance criteria: schema shape, availability (mirrors an AWS
tool / planner-selectable), and extraction, plus runtime behavior — filter
priority, the time-window helper, response shaping, and the synthetic
``aws_backend`` short-circuit that keeps scenario runs off real AWS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from integrations.cloudtrail import (
    DEFAULT_CLOUDTRAIL_REGION,
    cloudtrail_extract_params,
    cloudtrail_is_available,
)
from integrations.cloudtrail.tools.cloudtrail_events_tool import (
    _map_lookup_cloudtrail_events,
    lookup_cloudtrail_events,
)
from tests.tools.conftest import BaseToolContract

_RT = lookup_cloudtrail_events.__opensre_registered_tool__


class _FakeAWSBackend:
    """Minimal AWSBackend stand-in that records lookup_events calls."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def lookup_events(
        self,
        lookup_attributes: list[dict[str, str]],
        duration_minutes: int = 60,
        max_results: int = 50,
        region: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "lookup_attributes": lookup_attributes,
                "duration_minutes": duration_minutes,
                "max_results": max_results,
                "region": region,
            }
        )
        return self.response


class TestCloudTrailEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return _RT


# ─────────────────────────────────────────────────────────────────────────────
# Discovery — the tool is auto-registered on the chat surface
# ─────────────────────────────────────────────────────────────────────────────


def test_tool_discovered_on_chat_surface() -> None:
    from tools.registry import get_registered_tools

    names = {t.name for t in get_registered_tools("chat")}
    assert "lookup_cloudtrail_events" in names


def test_cloudtrail_prioritized_but_not_auto_seeded_for_aws_alerts() -> None:
    """CloudTrail is prioritized for AWS alerts but left for the planner to call.

    It is intentionally NOT in the auto-seed map: an unscoped, account-wide
    lookup on every cloudwatch/eks/alertmanager alert would be noisy and press
    CloudTrail's low lookup rate limit. The prompt prioritization map nudges the
    planner to reach for it once it has a concrete resource/principal/time target.
    """
    from core.domain.alerts.alert_source import alert_source_routing

    table = alert_source_routing()
    for alert_source in ("cloudwatch", "eks", "alertmanager"):
        entry = table[alert_source]
        assert "cloudtrail" not in entry.seed_tool_sources
        assert "cloudtrail" in entry.relevance_tool_sources


# ─────────────────────────────────────────────────────────────────────────────
# Schema shape
# ─────────────────────────────────────────────────────────────────────────────


def test_schema_shape() -> None:
    assert _RT.name == "lookup_cloudtrail_events"
    assert _RT.source == "cloudtrail"
    schema = _RT.input_schema
    assert schema["type"] == "object"
    props = schema["properties"]
    # The three forensic filters + the window/region knobs are all present.
    for field in ("resource_name", "event_source", "username", "region", "duration_minutes"):
        assert field in props
    # All filters are optional — the tool is account-wide and selectable without one.
    assert schema["required"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Availability — mirrors an AWS tool (gates on the account-level "aws" source)
# ─────────────────────────────────────────────────────────────────────────────


def test_is_available_on_verified_aws() -> None:
    assert cloudtrail_is_available({"aws": {"connection_verified": True}}) is True


def test_is_available_on_role_or_credentials() -> None:
    assert cloudtrail_is_available({"aws": {"role_arn": "arn:aws:iam::1:role/r"}}) is True
    assert cloudtrail_is_available({"aws": {"credentials": {"access_key_id": "AKIA"}}}) is True


def test_is_available_on_injected_backend() -> None:
    # The synthetic harness injects the fixture backend as "ec2_backend" on the
    # "aws" source (see tests/synthetic/rds_postgres/run_suite.py).
    assert cloudtrail_is_available({"aws": {"ec2_backend": object()}}) is True


def test_not_available_without_aws() -> None:
    assert cloudtrail_is_available({}) is False
    assert cloudtrail_is_available({"aws": {}}) is False


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_params_region_from_source() -> None:
    params = cloudtrail_extract_params(
        {"aws": {"connection_verified": True, "region": "eu-west-1"}}
    )
    assert params["region"] == "eu-west-1"
    assert params["aws_backend"] is None


def test_extract_params_defaults_region(monkeypatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    params = cloudtrail_extract_params({"aws": {"connection_verified": True}})
    assert params["region"] == DEFAULT_CLOUDTRAIL_REGION


def test_extract_params_forwards_backend() -> None:
    backend = object()
    # Harness-shaped: the backend handle lives under "ec2_backend" on "aws".
    params = cloudtrail_extract_params({"aws": {"ec2_backend": backend}})
    assert params["aws_backend"] is backend


# ─────────────────────────────────────────────────────────────────────────────
# Runtime behavior
# ─────────────────────────────────────────────────────────────────────────────


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_lookup_success_and_shaping(mock_call) -> None:
    detail = json.dumps(
        {"awsRegion": "us-east-1", "sourceIPAddress": "1.2.3.4", "errorCode": "AccessDenied"}
    )
    mock_call.return_value = {
        "success": True,
        "data": {
            "Events": [
                {
                    "EventId": "evt-1",
                    "EventName": "DeleteSecurityGroup",
                    "EventTime": "2026-05-05T12:00:00Z",
                    "EventSource": "ec2.amazonaws.com",
                    "Username": "alice",
                    "ReadOnly": "false",
                    "AccessKeyId": "AKIAEXAMPLE",
                    "Resources": [
                        {"ResourceType": "AWS::EC2::SecurityGroup", "ResourceName": "sg-1"}
                    ],
                    "CloudTrailEvent": detail,
                }
            ]
        },
    }

    result = lookup_cloudtrail_events(event_source="ec2.amazonaws.com", duration_minutes=120)

    assert result["available"] is True
    assert result["total_events"] == 1
    event = result["events"][0]
    assert event["event_name"] == "DeleteSecurityGroup"
    assert event["username"] == "alice"
    # "false" -> real bool False (not the truthy string "false")
    assert event["read_only"] is False
    assert event["resources"] == [{"type": "AWS::EC2::SecurityGroup", "name": "sg-1"}]
    assert event["aws_region"] == "us-east-1"
    assert event["source_ip_address"] == "1.2.3.4"
    assert event["error_code"] == "AccessDenied"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_filter_priority_prefers_resource_name(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(
        resource_name="sg-1",
        username="alice",
        event_source="ec2.amazonaws.com",
    )

    sent = mock_call.call_args.kwargs["parameters"]
    assert sent["LookupAttributes"] == [{"AttributeKey": "ResourceName", "AttributeValue": "sg-1"}]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_filter_priority_username_over_event_source(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(username="alice", event_source="iam.amazonaws.com")

    sent = mock_call.call_args.kwargs["parameters"]
    assert sent["LookupAttributes"] == [{"AttributeKey": "Username", "AttributeValue": "alice"}]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_no_filter_omits_lookup_attributes(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = lookup_cloudtrail_events()

    sent = mock_call.call_args.kwargs["parameters"]
    assert "LookupAttributes" not in sent
    # The time window is always sent.
    assert "StartTime" in sent and "EndTime" in sent
    assert result["filter"] is None


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_duration_clamped_and_window_built(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = lookup_cloudtrail_events(duration_minutes=10**9)

    sent = mock_call.call_args.kwargs["parameters"]
    # End is after start; duration clamped to the 90-day max.
    assert sent["EndTime"] > sent["StartTime"]
    assert result["duration_minutes"] == 90 * 24 * 60


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_max_results_clamped(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(max_results=999)

    assert mock_call.call_args.kwargs["parameters"]["MaxResults"] == 50


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_truncated_when_next_token_present(mock_call) -> None:
    mock_call.return_value = {
        "success": True,
        "data": {"Events": [], "NextToken": "tok-abc"},
    }

    result = lookup_cloudtrail_events()

    # A NextToken means more events exist beyond this page — surface it.
    assert result["truncated"] is True
    assert result["next_token"] == "tok-abc"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_not_truncated_without_next_token(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = lookup_cloudtrail_events()

    assert result["truncated"] is False
    assert result["next_token"] is None


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_next_token_forwarded_to_api(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    lookup_cloudtrail_events(next_token="tok-page-2")

    assert mock_call.call_args.kwargs["parameters"]["NextToken"] == "tok-page-2"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_lookup_failure(mock_call) -> None:
    mock_call.return_value = {"success": False, "error": "ThrottlingException"}

    result = lookup_cloudtrail_events()

    assert result["available"] is False
    assert result["error"] == "Failed to look up CloudTrail events. Check server logs for details."


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_short_circuits_to_aws_backend(mock_call) -> None:
    backend = _FakeAWSBackend(
        response={
            "source": "cloudtrail",
            "available": True,
            "total_events": 0,
            "events": [],
            "error": None,
        }
    )

    result = lookup_cloudtrail_events(
        resource_name="sg-1",
        region="us-east-1",
        duration_minutes=30,
        max_results=25,
        aws_backend=backend,
    )

    mock_call.assert_not_called()
    assert result["available"] is True
    assert backend.calls == [
        {
            "lookup_attributes": [{"AttributeKey": "ResourceName", "AttributeValue": "sg-1"}],
            "duration_minutes": 30,
            "max_results": 25,
            "region": "us-east-1",
        }
    ]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_harness_shaped_aws_source_short_circuits_off_real_aws(mock_call) -> None:
    """Regression: the synthetic harness injects the backend as aws['ec2_backend'].

    Resolving via cloudtrail_extract_params (not injecting aws_backend directly)
    must pick up that handle and short-circuit the tool, so a synthetic run —
    e.g. an rds scenario with alert_source 'cloudwatch' — never reaches a real
    boto3 lookup_events call even when ambient AWS creds are present.
    """
    backend = _FakeAWSBackend(
        response={"source": "cloudtrail", "available": True, "total_events": 0, "events": []}
    )
    # Source dict shaped exactly like tests/synthetic/rds_postgres/run_suite.py.
    sources = {"aws": {"region": "us-east-1", "ec2_backend": backend}}

    params = cloudtrail_extract_params(sources)
    assert params["aws_backend"] is backend  # resolved from ec2_backend, not _backend

    result = lookup_cloudtrail_events(resource_name="sg-1", **params)

    mock_call.assert_not_called()
    assert backend.calls and backend.calls[0]["region"] == "us-east-1"
    assert result["available"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Live payload fixtures (#3583) — real LookupEvents response shapes
# ─────────────────────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cloudtrail"


def _fixture_payload(name: str) -> dict[str, Any]:
    payload = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    payload.pop("_comment", None)
    return payload


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_realistic_page_shapes_every_event(mock_call) -> None:
    """A faithful LookupEvents page (mixed principals, an errored call, a
    service event without Username) shapes into the exact per-event contract."""
    mock_call.return_value = {"success": True, "data": _fixture_payload("lookup_events_page.json")}

    result = lookup_cloudtrail_events(duration_minutes=120)

    assert result["available"] is True
    assert result["total_events"] == 3
    by_id = {event["event_id"]: event for event in result["events"]}

    # Full-dict equality: locks every key of the shaped-event contract,
    # including the ISO EventTime passthrough and event_source.
    assert by_id["8e3c6f2b-4a1d-4c8e-9f2a-1b7d3e5a9c01"] == {
        "event_id": "8e3c6f2b-4a1d-4c8e-9f2a-1b7d3e5a9c01",
        "event_name": "PutRolePolicy",
        "event_time": "2026-07-03T09:12:41+00:00",
        "event_source": "iam.amazonaws.com",
        "username": "deploy-bot",
        "read_only": False,
        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "resources": [{"type": "AWS::IAM::Role", "name": "payments-service-role"}],
        "resources_truncated": False,
        "aws_region": "us-east-1",
        "source_ip_address": "203.0.113.24",
        "error_code": None,
    }

    # A denied call surfaces its errorCode from the CloudTrailEvent record.
    denied = by_id["1f9a7c3e-6b2d-4a5f-8c1e-7d4b2a9f6e02"]
    assert denied["event_name"] == "DeleteSecurityGroup"
    assert denied["error_code"] == "Client.UnauthorizedOperation"
    assert denied["source_ip_address"] == "198.51.100.7"

    # AWS service events carry no Username or AccessKeyId — shaped as None,
    # never a KeyError, and ReadOnly "true" coerces to a real bool.
    service = by_id["5d2b8e4a-9c1f-4d7b-a3e6-8f5c2d1b9a03"]
    assert service["username"] is None
    assert service["access_key_id"] is None
    assert service["read_only"] is True
    assert service["resources"] == []

    # More matching events exist beyond this page — surfaced, not swallowed.
    assert result["truncated"] is True
    assert result["next_token"] == "AAAAfR3kNzXhCq9lEXAMPLEtokenXo1v"


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_success_payload_omits_error_key(mock_call) -> None:
    """The success payload must NOT carry an "error" key.

    The runtime tool loop (core.tool.execution._normalize_result) flags a result as a
    failure on the mere presence of an "error" key (is_error = "error" in raw) and
    replaces the whole payload with {"error": ...} before the agent sees it. A
    success dict with "error": None therefore hides every event from the
    investigation — regression guard against reintroducing it.
    """
    mock_call.return_value = {"success": True, "data": _fixture_payload("lookup_events_page.json")}
    result = lookup_cloudtrail_events(duration_minutes=60)

    assert result["available"] is True
    assert result["total_events"] == 3
    assert "error" not in result

    # Failure paths still carry a real message (correctly flagged as is_error).
    mock_call.return_value = {"success": False, "error": "ThrottlingException"}
    failed = lookup_cloudtrail_events()
    assert failed["available"] is False
    assert "error" in failed and failed["error"]


@patch("integrations.cloudtrail.tools.cloudtrail_events_tool.execute_aws_sdk_call")
def test_weird_page_survives_and_shapes_gracefully(mock_call) -> None:
    """Degenerate live payloads must degrade to partial events, never raise.

    The weird fixture packs the §1 cases: CloudTrailEvent as valid-but-non-dict
    JSON (null / bare string / list) or invalid JSON, an event with only
    EventId, ReadOnly as a real bool and as "True", and a null plus a
    _sanitize_response truncation marker inside Resources — the one place the
    marker is genuinely reachable (a single event CAN reference >MAX_LIST_ITEMS
    resources, e.g. CreateTags across a fleet). The marker as the final Events
    entry is a purely defensive case (LookupEvents pages cap at 50 <
    MAX_LIST_ITEMS, so the sanitizer cannot produce it there): it pins that
    non-dict Events entries are tolerated, not that the transport emits them.
    """
    mock_call.return_value = {"success": True, "data": _fixture_payload("lookup_events_weird.json")}

    result = lookup_cloudtrail_events()

    assert result["available"] is True
    # The trailing Events truncation marker (a str) is dropped, not shaped.
    assert result["total_events"] == 7
    by_id = {event["event_id"]: event for event in result["events"]}

    # Valid-but-non-dict CloudTrailEvent JSON -> detail fields None, no crash.
    for event_id in ("weird-null-detail", "weird-string-detail", "weird-list-detail"):
        event = by_id[event_id]
        assert event["aws_region"] is None
        assert event["source_ip_address"] is None
        assert event["error_code"] is None

    # Invalid JSON keeps its existing graceful path.
    assert by_id["weird-invalid-json-detail"]["aws_region"] is None

    # An event carrying only EventId shapes with every other field defaulted.
    bare = by_id["weird-bare-event"]
    assert bare["event_name"] is None
    assert bare["username"] is None
    assert bare["read_only"] is None
    assert bare["resources"] == []
    assert bare["resources_truncated"] is False

    # Non-dict Resources entries (null / truncation marker) are skipped; the
    # real resource survives, a bool ReadOnly passes through unchanged, and the
    # drop is surfaced so the planner knows the blast radius is understated.
    mixed = by_id["weird-resources-mixed"]
    assert mixed["resources"] == [{"type": "AWS::EC2::Instance", "name": "i-0abc123def456789a"}]
    assert mixed["resources_truncated"] is True
    assert mixed["read_only"] is True
    assert mixed["aws_region"] == "eu-west-1"

    # ReadOnly arrives with inconsistent casing in live payloads.
    assert by_id["weird-readonly-mixed-case"]["read_only"] is True

    assert result["truncated"] is True
    assert result["next_token"] == "AAAAweirdPageToken"


# ─────────────────────────────────────────────────────────────────────────────
# Evidence mapper
# ─────────────────────────────────────────────────────────────────────────────


class TestMapLookupCloudtrailEvents:
    def test_records_entry_with_write_and_error_counts(self) -> None:
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence,
            {
                "available": True,
                "total_events": 2,
                "truncated": False,
                "events": [
                    {"read_only": False, "error_code": None},
                    {"read_only": False, "error_code": "AccessDenied"},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "lookup_cloudtrail_events"
        assert entries[0]["summary"] == "2 event(s), 2 write, 1 with error"

    def test_qualifies_count_when_truncated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence,
            {
                "available": True,
                "total_events": 50,
                "truncated": True,
                "events": [{"read_only": True, "error_code": None}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "50+ event(s)"

    def test_qualifies_write_and_error_counts_when_truncated(self) -> None:
        """Regression: write_count/error_count are tallied over the returned
        page only -- on a truncated page they must be marked as a floor, not
        presented as exact totals."""
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence,
            {
                "available": True,
                "total_events": 50,
                "truncated": True,
                "events": [
                    {"read_only": False, "error_code": None},
                    {"read_only": False, "error_code": "AccessDenied"},
                ],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "50+ event(s), 2+ write, 1+ with error"

    def test_strips_carriage_returns_from_filter_value(self) -> None:
        """Regression: a filter value with bare \\r or \\r\\n line endings
        must not leave a literal carriage return in the report summary."""
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence,
            {
                "available": True,
                "total_events": 1,
                "truncated": False,
                "filter": {"AttributeKey": "Username", "AttributeValue": "alice\r\nbob\r"},
                "events": [{"read_only": True, "error_code": None}],
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "\r" not in summary

    def test_includes_filter_when_applied(self) -> None:
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence,
            {
                "available": True,
                "total_events": 1,
                "truncated": False,
                "filter": {"AttributeKey": "ResourceName", "AttributeValue": "sg-1"},
                "events": [{"read_only": True, "error_code": None}],
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "filtered by ResourceName='sg-1'" in summary

    def test_records_nothing_when_no_events(self) -> None:
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence, {"available": True, "total_events": 0, "events": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_lookup_cloudtrail_events(
            evidence, {"available": False, "error": "ThrottlingException"}, {}
        )

        assert "catalog_entries" not in evidence
