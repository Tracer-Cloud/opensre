"""Unit tests for integrations.ec2.tools.ec2_instances_by_tag_tool."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.aws.topology_helper import extract_ec2_instances_params
from integrations.ec2.tools.ec2_instances_by_tag_tool import (
    _is_available,
    ec2_instances_by_tag,
)
from integrations.ec2.tools.ec2_instances_by_tag_tool._evidence import (
    map_ec2_instances_by_tag as _map_ec2_instances_by_tag,
)


class _FakeAWSBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def describe_instances_by_tag(
        self,
        tier: str = "",
        instance_ids: list[str] | None = None,
        vpc_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append({"tier": tier, "instance_ids": instance_ids, "vpc_id": vpc_id})
        return {
            "source": "ec2",
            "available": True,
            "tier": tier,
            "vpc_id": vpc_id,
            "total_instances": 4,
            "instances": [{"instance_id": f"i-{i}", "tier": tier or "web"} for i in range(4)],
            "by_tier": {"web": [f"i-{i}" for i in range(4)]},
            "tiers_detected": ["web"],
            "error": None,
        }


def test_is_available_requires_ec2_source_and_topology_hints() -> None:
    assert _is_available({}) is False
    assert _is_available({"ec2": {"connection_verified": True}}) is False
    assert _is_available({"ec2": {"connection_verified": True, "tiers": ["web"]}}) is True
    assert _is_available({"ec2": {"connection_verified": True, "vpc_id": "vpc-1"}}) is True
    assert _is_available({"ec2": {"_backend": object(), "instance_ids": ["i-1"]}}) is True


def test_extract_params_passes_single_tier_through() -> None:
    params = extract_ec2_instances_params(
        {"ec2": {"tiers": ["web"], "vpc_id": "vpc-1", "region": "eu-west-1"}}
    )
    assert params["tier"] == "web"
    assert params["vpc_id"] == "vpc-1"
    assert params["region"] == "eu-west-1"


def test_extract_params_blanks_tier_when_multitier() -> None:
    """Multi-tier topology must rely on by_tier grouping, not a single tier filter."""
    params = extract_ec2_instances_params({"ec2": {"tiers": ["web", "worker"], "vpc_id": "vpc-1"}})
    assert params["tier"] == ""
    assert params["vpc_id"] == "vpc-1"


def test_extract_params_raises_when_ec2_source_missing() -> None:
    with pytest.raises(ValueError):
        extract_ec2_instances_params({})


def test_backend_short_circuits_boto3() -> None:
    backend = _FakeAWSBackend()
    result = ec2_instances_by_tag(tier="web", aws_backend=backend)
    assert result["source"] == "ec2"
    assert result["available"] is True
    assert backend.calls == [{"tier": "web", "instance_ids": [], "vpc_id": ""}]


def test_returns_error_when_no_filters_and_no_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without tier/instance_ids/vpc_id, the tool must refuse to call boto3."""

    def _fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("execute_aws_sdk_call must not be invoked")

    monkeypatch.setattr(
        "integrations.ec2.tools.ec2_instances_by_tag_tool.execute_aws_sdk_call", _fail
    )
    out = ec2_instances_by_tag()
    assert out["available"] is False
    assert "required" in out["error"]


def test_real_path_paginates_and_filters_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination follows NextToken; terminated/stopped instances are dropped."""
    calls: list[dict[str, Any]] = []

    def _execute(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        page = len(calls)
        if page == 1:
            return {
                "success": True,
                "data": {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-active",
                                    "Tags": [{"Key": "tier", "Value": "web"}],
                                    "State": {"Name": "running"},
                                    "VpcId": "vpc-1",
                                    "Placement": {"AvailabilityZone": "us-east-1a"},
                                },
                                {
                                    "InstanceId": "i-stopped",
                                    "Tags": [{"Key": "tier", "Value": "web"}],
                                    "State": {"Name": "stopped"},
                                },
                            ]
                        }
                    ],
                    "NextToken": "tok-2",
                },
            }
        return {
            "success": True,
            "data": {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-page2",
                                "Tags": [{"Key": "tier", "Value": "worker"}],
                                "State": {"Name": "running"},
                                "VpcId": "vpc-1",
                            }
                        ]
                    }
                ]
            },
        }

    monkeypatch.setattr(
        "integrations.ec2.tools.ec2_instances_by_tag_tool.execute_aws_sdk_call", _execute
    )
    out = ec2_instances_by_tag(vpc_id="vpc-1")
    assert out["available"] is True
    assert out["total_instances"] == 2  # stopped instance dropped
    assert sorted(out["tiers_detected"]) == ["web", "worker"]
    assert out["by_tier"] == {"web": ["i-active"], "worker": ["i-page2"]}
    assert len(calls) == 2  # paginated
    assert calls[1]["parameters"]["NextToken"] == "tok-2"


def test_summary_block_is_agent_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool output must include a precomputed summary the LLM can quote directly."""

    def _execute(**_: Any) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": f"i-w{i}",
                                "Tags": [{"Key": "tier", "Value": "web"}],
                                "State": {"Name": "running"},
                                "VpcId": "vpc-1",
                                "Placement": {"AvailabilityZone": "us-east-1a"},
                            }
                            for i in range(4)
                        ]
                        + [
                            {
                                "InstanceId": "i-k1",
                                "Tags": [{"Key": "tier", "Value": "worker"}],
                                "State": {"Name": "running"},
                                "VpcId": "vpc-1",
                                "Placement": {"AvailabilityZone": "us-east-1b"},
                            }
                        ]
                    }
                ]
            },
        }

    monkeypatch.setattr(
        "integrations.ec2.tools.ec2_instances_by_tag_tool.execute_aws_sdk_call", _execute
    )
    out = ec2_instances_by_tag(vpc_id="vpc-1")
    summary = out["summary"]
    assert summary["by_tier_counts"] == {"web": 4, "worker": 1}
    assert summary["total_active"] == 5
    assert summary["primary_tier"] == "web"
    assert summary["vpcs_in_scope"] == ["vpc-1"]
    assert summary["azs_in_scope"] == ["us-east-1a", "us-east-1b"]


def test_real_path_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.ec2.tools.ec2_instances_by_tag_tool.execute_aws_sdk_call",
        lambda **_: {"success": False, "error": "AccessDenied"},
    )
    out = ec2_instances_by_tag(tier="web")
    assert out["available"] is False
    assert "Failed" in out["error"]


class TestMapEc2InstancesByTag:
    def test_records_entry_with_primary_tier(self) -> None:
        evidence: dict[str, Any] = {}

        _map_ec2_instances_by_tag(
            evidence,
            {
                "available": True,
                "total_instances": 5,
                "instances": [{"instance_id": "i-1"}],
                "summary": {"primary_tier": "web"},
                "tiers_detected": ["web", "worker"],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "ec2_instances_by_tag"
        assert entries[0]["summary"] == "5 active instance(s), primary tier 'web', 2 tiers"

    def test_records_entry_without_tier_clause_when_single_tier(self) -> None:
        evidence: dict[str, Any] = {}

        _map_ec2_instances_by_tag(
            evidence,
            {
                "available": True,
                "total_instances": 4,
                "instances": [{"instance_id": "i-1"}],
                "summary": {"primary_tier": "web"},
                "tiers_detected": ["web"],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"] == "4 active instance(s), primary tier 'web'"
        )

    def test_qualifies_count_when_truncated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_ec2_instances_by_tag(
            evidence,
            {
                "available": True,
                "total_instances": 5000,
                "truncated": True,
                "instances": [{"instance_id": "i-1"}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "5000+ active instance(s)"

    def test_records_nothing_when_no_instances(self) -> None:
        evidence: dict[str, Any] = {}

        _map_ec2_instances_by_tag(
            evidence, {"available": True, "total_instances": 0, "instances": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_ec2_instances_by_tag(evidence, {"available": False, "error": "AccessDenied"}, {})

        assert "catalog_entries" not in evidence
