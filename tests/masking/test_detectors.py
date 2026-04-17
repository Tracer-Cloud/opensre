"""Tests for identifier detectors — coverage and false-positive bounds."""

from __future__ import annotations

import pytest

from app.masking.detectors import find_identifiers
from app.masking.policy import ALL_KINDS, MaskingPolicy


def _policy(kinds: tuple[str, ...] = ALL_KINDS) -> MaskingPolicy:
    return MaskingPolicy.model_validate({"enabled": True, "kinds": kinds})


def test_no_matches_when_disabled() -> None:
    disabled = MaskingPolicy.model_validate({"enabled": False, "kinds": ALL_KINDS})
    assert find_identifiers("pod etl-worker-7d9f8b-xkp2q is crashing", disabled) == []


def test_pod_name_detected() -> None:
    matches = find_identifiers("pod etl-worker-7d9f8b-xkp2q is crashing", _policy())
    kinds = {m.kind for m in matches}
    assert "pod" in kinds
    pod_match = next(m for m in matches if m.kind == "pod")
    assert pod_match.value == "etl-worker-7d9f8b-xkp2q"


def test_namespace_detected_with_colon_syntax() -> None:
    matches = find_identifiers("kube_namespace:tracer-test failing", _policy())
    ns_match = next(m for m in matches if m.kind == "namespace")
    assert ns_match.value == "tracer-test"


def test_namespace_detected_with_equals_syntax() -> None:
    matches = find_identifiers("ns=payments-prod error", _policy())
    ns_match = next(m for m in matches if m.kind == "namespace")
    assert ns_match.value == "payments-prod"


def test_cluster_detected() -> None:
    matches = find_identifiers("eks_cluster:prod-us-east-1 has issues", _policy())
    cluster_match = next(m for m in matches if m.kind == "cluster")
    assert cluster_match.value == "prod-us-east-1"


def test_service_name_detected() -> None:
    matches = find_identifiers("service:checkout-api response timeout", _policy())
    svc_match = next(m for m in matches if m.kind == "service_name")
    assert svc_match.value == "checkout-api"


def test_hostname_kind_kind_control_plane() -> None:
    matches = find_identifiers("host kind-control-plane is down", _policy())
    host = next(m for m in matches if m.kind == "hostname")
    assert host.value == "kind-control-plane"


def test_hostname_ec2_internal_style() -> None:
    matches = find_identifiers("from ip-10-0-1-23.ec2.internal", _policy())
    host = next(m for m in matches if m.kind == "hostname")
    assert host.value.startswith("ip-10-0-1-23")


def test_account_id_detected() -> None:
    matches = find_identifiers("aws account 123456789012 alert", _policy())
    account_match = next(m for m in matches if m.kind == "account_id")
    assert account_match.value == "123456789012"


def test_ip_address_detected() -> None:
    matches = find_identifiers("connected from 192.168.1.100", _policy())
    ip_match = next(m for m in matches if m.kind == "ip_address")
    assert ip_match.value == "192.168.1.100"


def test_email_detected() -> None:
    matches = find_identifiers("alert triggered by alice@example.com", _policy())
    email_match = next(m for m in matches if m.kind == "email")
    assert email_match.value == "alice@example.com"


@pytest.mark.parametrize(
    "benign",
    [
        "happy birthday alice",
        "the quick brown fox jumps",
        "logs from 2026-04-17 show errors",
        "count was 1234 today",
        "version 3.9.18 released",
    ],
)
def test_benign_text_is_not_masked(benign: str) -> None:
    matches = find_identifiers(benign, _policy())
    assert matches == [], f"unexpected match on benign text: {benign!r} -> {matches}"


def test_only_selected_kinds_run() -> None:
    matches = find_identifiers(
        "kube_namespace:prod host kind-control-plane",
        _policy(kinds=("namespace",)),
    )
    kinds = {m.kind for m in matches}
    assert kinds == {"namespace"}


def test_overlapping_matches_resolved_longest_wins() -> None:
    # "ip-10-0-1-23.ec2.internal" should match as a single hostname even though
    # "10.0.1.23" would match the IP regex inside the larger string. The
    # hostname regex doesn't produce that, but the resolver should still
    # correctly return a single hostname span without an embedded IP match.
    text = "from host ip-10-0-1-23.ec2.internal node"
    matches = find_identifiers(text, _policy())
    hostnames = [m for m in matches if m.kind == "hostname"]
    assert len(hostnames) == 1


def test_hostname_regex_handles_adversarial_input_quickly() -> None:
    """Regression test for ReDoS flagged by CodeQL on the hostname regex.

    A string with many alternating '-' and '.' characters should not cause
    exponential backtracking. We cap the runtime at 1 second; with the
    previous vulnerable pattern this would hang well beyond that.
    """
    import time

    adversarial = "ip-10-0-1-23" + ("-." * 50) + "X"
    start = time.perf_counter()
    # Trigger all detectors — the hostname detector was the vulnerable one.
    find_identifiers(adversarial, _policy())
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"hostname regex took {elapsed:.2f}s on adversarial input"


def test_extra_regex_pattern_is_applied() -> None:
    policy = MaskingPolicy.model_validate(
        {
            "enabled": True,
            "kinds": ("email",),
            "extra_patterns": {"jira_key": r"\b([A-Z]+-\d+)\b"},
        }
    )
    matches = find_identifiers("ticket OPS-1234 assigned", policy)
    jira = next(m for m in matches if m.kind == "jira_key")
    assert jira.value == "OPS-1234"
