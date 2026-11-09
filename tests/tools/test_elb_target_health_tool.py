"""Unit tests for integrations.elb.tools.elb_target_health_tool."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.aws.topology_helper import extract_target_health_params
from integrations.elb.tools.elb_target_health_tool import (
    _is_available,
    get_elb_target_health,
)
from integrations.elb.tools.elb_target_health_tool._evidence import (
    map_get_elb_target_health as _map_get_elb_target_health,
)


class _FakeAWSBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def describe_target_health(
        self,
        target_group_arns: list[str] | None = None,
        target_group_arn: str = "",
        load_balancer_arn: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        arns = list(target_group_arns or [])
        if target_group_arn:
            arns.append(target_group_arn)
        self.calls.append({"tgs": arns, "lb": load_balancer_arn})
        return {
            "source": "ec2",
            "available": True,
            "target_groups": [{"TargetGroupArn": arns[0] if arns else "tg-x"}],
            "healthy_targets": [{"instance_id": "i-1", "state": "healthy"}],
            "unhealthy_targets": [],
            "instance_ids": ["i-1"],
            "error": None,
        }


def test_is_available_requires_topology_hints() -> None:
    assert _is_available({}) is False
    assert _is_available({"ec2": {"connection_verified": True}}) is False
    assert (
        _is_available({"ec2": {"connection_verified": True, "target_group_arns": ["tg-1"]}}) is True
    )
    assert (
        _is_available({"ec2": {"connection_verified": True, "load_balancer_arns": ["lb-1"]}})
        is True
    )
    assert _is_available({"ec2": {"_backend": object()}}) is True


def test_extract_params_passes_all_target_group_arns() -> None:
    """Multi-TG ALBs are common — silently truncating to the first ARN would
    hide whole tiers from the agent. The full list must round-trip."""
    params = extract_target_health_params(
        {
            "ec2": {
                "target_group_arns": ["tg-1", "tg-2"],
                "load_balancer_arns": ["lb-1"],
                "region": "eu-west-1",
            }
        }
    )
    assert params["target_group_arns"] == ["tg-1", "tg-2"]
    assert params["load_balancer_arn"] == "lb-1"
    assert params["region"] == "eu-west-1"


def test_extract_params_falls_back_to_load_balancer_when_no_target_group() -> None:
    params = extract_target_health_params({"ec2": {"load_balancer_arns": ["lb-1"]}})
    assert params["target_group_arns"] == []
    assert params["load_balancer_arn"] == "lb-1"


def test_extract_params_raises_when_ec2_source_missing() -> None:
    with pytest.raises(ValueError):
        extract_target_health_params({})


def test_backend_short_circuits_boto3() -> None:
    backend = _FakeAWSBackend()
    result = get_elb_target_health(target_group_arns=["tg-1", "tg-2"], aws_backend=backend)
    assert result["available"] is True
    assert result["instance_ids"] == ["i-1"]
    assert backend.calls == [{"tgs": ["tg-1", "tg-2"], "lb": ""}]


def test_singular_target_group_arn_alias_still_works() -> None:
    """Convenience alias for the singular form must merge with the plural list."""
    backend = _FakeAWSBackend()
    get_elb_target_health(target_group_arn="tg-legacy", aws_backend=backend)
    assert backend.calls == [{"tgs": ["tg-legacy"], "lb": ""}]


def test_returns_error_when_neither_arn_provided() -> None:
    out = get_elb_target_health()
    assert out["available"] is False
    assert "required" in out["error"]


def test_real_path_combines_groups_and_health(monkeypatch: pytest.MonkeyPatch) -> None:
    def _execute(**kwargs: Any) -> dict[str, Any]:
        if kwargs["operation_name"] == "describe_target_groups":
            return {
                "success": True,
                "data": {"TargetGroups": [{"TargetGroupArn": "tg-1"}]},
            }
        # describe_target_health
        return {
            "success": True,
            "data": {
                "TargetHealthDescriptions": [
                    {
                        "Target": {"Id": "i-healthy", "Port": 80},
                        "TargetHealth": {"State": "healthy"},
                    },
                    {
                        "Target": {"Id": "i-draining", "Port": 80},
                        "TargetHealth": {"State": "draining", "Reason": "Target.Deregistration"},
                    },
                ]
            },
        }

    monkeypatch.setattr(
        "integrations.elb.tools.elb_target_health_tool.execute_aws_sdk_call", _execute
    )
    out = get_elb_target_health(target_group_arn="tg-1")
    assert out["available"] is True
    assert [t["instance_id"] for t in out["healthy_targets"]] == ["i-healthy"]
    assert [t["instance_id"] for t in out["unhealthy_targets"]] == ["i-draining"]
    assert out["instance_ids"] == ["i-healthy", "i-draining"]


def test_summary_block_is_agent_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool output must include a precomputed summary the LLM can quote directly."""

    def _execute(**kwargs: Any) -> dict[str, Any]:
        if kwargs["operation_name"] == "describe_target_groups":
            return {
                "success": True,
                "data": {"TargetGroups": [{"TargetGroupArn": "tg-1"}]},
            }
        return {
            "success": True,
            "data": {
                "TargetHealthDescriptions": [
                    {
                        "Target": {"Id": f"i-h{i}", "Port": 80},
                        "TargetHealth": {"State": "healthy"},
                    }
                    for i in range(3)
                ]
                + [
                    {
                        "Target": {"Id": "i-d1", "Port": 80},
                        "TargetHealth": {"State": "draining"},
                    }
                ],
            },
        }

    monkeypatch.setattr(
        "integrations.elb.tools.elb_target_health_tool.execute_aws_sdk_call", _execute
    )
    out = get_elb_target_health(target_group_arn="tg-1")
    summary = out["summary"]
    assert summary["total_targets"] == 4
    assert summary["healthy_count"] == 3
    assert summary["unhealthy_count"] == 1
    assert summary["healthy_ratio_pct"] == 75.0
    assert summary["unhealthy_states"] == ["draining"]
    assert summary["target_group_count"] == 1


def test_partial_target_health_failure_marks_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When iterating multiple TGs, a per-TG describe_target_health failure must
    surface in api_errors and flip available=False — silently absorbing the
    error would let the agent conclude that a partially-queried tier is fully
    healthy when we actually have no coverage of it."""

    def _execute(**kwargs: Any) -> dict[str, Any]:
        if kwargs["operation_name"] == "describe_target_groups":
            return {
                "success": True,
                "data": {
                    "TargetGroups": [
                        {"TargetGroupArn": "tg-web"},
                        {"TargetGroupArn": "tg-worker"},
                    ]
                },
            }
        # describe_target_health: succeed for tg-web, fail for tg-worker
        if kwargs["parameters"]["TargetGroupArn"] == "tg-web":
            return {
                "success": True,
                "data": {
                    "TargetHealthDescriptions": [
                        {
                            "Target": {"Id": "i-w1", "Port": 80},
                            "TargetHealth": {"State": "healthy"},
                        },
                    ],
                },
            }
        return {"success": False, "error": "AccessDenied: tg-worker"}

    monkeypatch.setattr(
        "integrations.elb.tools.elb_target_health_tool.execute_aws_sdk_call", _execute
    )
    out = get_elb_target_health(target_group_arns=["tg-web", "tg-worker"])
    assert out["available"] is False, (
        "partial coverage must flip available to False so the agent doesn't "
        "treat the worker tier as fully healthy"
    )
    assert out["error"] is not None
    assert "Partial coverage" in out["error"]
    assert len(out["api_errors"]) == 1
    assert out["api_errors"][0]["target_group_arn"] == "tg-worker"
    # Healthy data from the successful TG is still returned (don't lose what we have).
    assert [t["instance_id"] for t in out["healthy_targets"]] == ["i-w1"]


def test_real_path_propagates_describe_groups_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.elb.tools.elb_target_health_tool.execute_aws_sdk_call",
        lambda **_: {"success": False, "error": "AccessDenied"},
    )
    out = get_elb_target_health(target_group_arn="tg-1")
    assert out["available"] is False
    assert "Failed" in out["error"]


class TestMapGetElbTargetHealth:
    def test_records_entry_with_unhealthy_states(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_elb_target_health(
            evidence,
            {
                "available": True,
                "healthy_targets": [{"instance_id": "i-1"}, {"instance_id": "i-2"}],
                "unhealthy_targets": [{"instance_id": "i-3"}],
                "target_groups": [{"TargetGroupArn": "tg-1"}],
                "summary": {"target_group_count": 1, "unhealthy_states": ["draining"]},
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_elb_target_health"
        assert (
            entries[0]["summary"]
            == "2 healthy, 1 unhealthy target(s) across 1 target group(s), states: draining"
        )

    def test_records_zero_unhealthy_as_a_genuine_finding(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_elb_target_health(
            evidence,
            {
                "available": True,
                "healthy_targets": [{"instance_id": "i-1"}],
                "unhealthy_targets": [],
                "target_groups": [{"TargetGroupArn": "tg-1"}],
                "summary": {"target_group_count": 1, "unhealthy_states": []},
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "1 healthy, 0 unhealthy target(s) across 1 target group(s)"
        )

    def test_records_nothing_when_no_targets(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_elb_target_health(
            evidence,
            {"available": True, "healthy_targets": [], "unhealthy_targets": []},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        """available=False also covers the partial-coverage case (api_errors
        non-empty) -- the tool sets it deliberately, so the mapper must not
        cite partial data as if it were complete."""
        evidence: dict[str, Any] = {}

        _map_get_elb_target_health(
            evidence,
            {
                "available": False,
                "healthy_targets": [{"instance_id": "i-1"}],
                "unhealthy_targets": [],
                "error": "Partial coverage: 1/2 target groups failed.",
            },
            {},
        )

        assert "catalog_entries" not in evidence
