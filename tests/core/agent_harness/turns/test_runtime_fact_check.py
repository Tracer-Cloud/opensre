"""Contradiction check for cloud facts: fires on a false claim, not on a topic."""

from __future__ import annotations

from core.agent_harness.turns.runtime_fact_check import (
    cloud_absence_correction,
    reply_contradicts_cloud_absence,
)

_HOST = "Jenyas-MacBook-Pro.local"
_NO_CLOUD = {"hostname": _HOST, "cloud_provider": "", "cloud_region": ""}

# The reply this check exists for, reproduced live on a laptop four times.
_HALLUCINATED_REPLY = (
    "The runtime environment is **development**.\n"
    f"- Host: `{_HOST}` (macOS)\n"
    "- Cloud: `aws` / `us-east-1`\n"
)


def test_fires_on_a_cloud_claim_for_a_host_with_no_cloud_detected() -> None:
    # Arrange / Act.
    contradicted = reply_contradicts_cloud_absence(_HALLUCINATED_REPLY, _NO_CLOUD)

    # Assert.
    assert contradicted is True


def test_silent_when_the_reply_discusses_a_cloud_without_claiming_to_run_there() -> None:
    """Deployment help must not be suppressed just for saying "AWS".

    The reply names AWS and a region but never claims *this* host is in it —
    it does not quote the host identity.
    """
    # Arrange.
    advice = (
        "To deploy the gateway, build the AMI and launch it in AWS. "
        "Pick a region such as us-east-1 and set OPENSRE_WEB_API_INGRESS_CIDR."
    )

    # Act / Assert.
    assert reply_contradicts_cloud_absence(advice, _NO_CLOUD) is False


def test_silent_when_a_cloud_really_was_detected() -> None:
    # Arrange.
    on_aws = {"hostname": _HOST, "cloud_provider": "aws", "cloud_region": "us-east-1"}

    # Act / Assert.
    assert reply_contradicts_cloud_absence(_HALLUCINATED_REPLY, on_aws) is False


def test_silent_when_cloud_detection_never_ran() -> None:
    """Absent keys are unknown, not "no cloud" — nothing authoritative to contradict."""
    # Arrange / Act / Assert.
    assert reply_contradicts_cloud_absence(_HALLUCINATED_REPLY, {"hostname": _HOST}) is False


def test_correction_names_the_host_and_forbids_a_cloud_field() -> None:
    # Arrange / Act.
    correction = cloud_absence_correction(_NO_CLOUD)

    # Assert — the retry is told what was wrong and what not to emit.
    assert _HOST in correction
    assert "not running in a recognised cloud environment" in correction
    assert "do not add a cloud field" in correction
