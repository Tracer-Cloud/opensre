"""Integration test: diagnose_root_cause unmasks LLM output.

With a seeded ``masking_map`` in state and masking enabled, verifies that
the fields returned by ``diagnose_root_cause`` contain the original
identifiers (unmasked) so user-facing display and downstream nodes work
with real values.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _enable_masking(monkeypatch) -> None:
    """Ensure masking is active for these tests regardless of host env."""
    monkeypatch.setenv("OPENSRE_MASK_ENABLED", "true")


def _state_with_masking(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "alert_name": "etl failure",
        "pipeline_name": "pipeline",
        "severity": "warning",
        "raw_alert": {"alert_name": "etl failure"},
        "evidence": {"error_logs": ["<POD_0> failing"]},
        "context": {"some": "context"},
        "investigation_loop_count": 0,
        "masking_map": {
            "<POD_0>": "etl-worker-7d9f8b-xkp2q",
            "<NAMESPACE_0>": "tracer-test",
        },
    }
    base.update(overrides)
    return base


def _mock_llm_response(text: str) -> object:
    response = MagicMock()
    response.content = text
    return response


def _mock_parse_result(root_cause: str) -> object:
    result = MagicMock()
    result.root_cause = root_cause
    result.root_cause_category = "deployment"
    result.causal_chain = [f"step referencing {root_cause}"]
    result.validated_claims = [
        {"claim": "<POD_0> entered CrashLoopBackOff", "validation_status": "validated"}
    ]
    result.non_validated_claims = []
    return result


def test_diagnose_unmasks_root_cause_and_claims() -> None:
    from app.nodes.root_cause_diagnosis import node as diag_node

    state = _state_with_masking()
    llm_response = _mock_llm_response(
        "ROOT_CAUSE: <POD_0> in <NAMESPACE_0> OOMKilled"
    )
    parsed = _mock_parse_result("<POD_0> in <NAMESPACE_0> OOMKilled")

    with (
        patch.object(diag_node, "get_llm_for_reasoning") as mock_llm_factory,
        patch.object(diag_node, "parse_root_cause", return_value=parsed),
        patch.object(diag_node, "is_clearly_healthy", return_value=False),
        patch.object(
            diag_node,
            "check_evidence_availability",
            return_value=(False, False, True),
        ),
        patch.object(diag_node, "validate_and_categorize_claims", return_value=([
            {"claim": "<POD_0> entered CrashLoopBackOff", "validation_status": "validated"}
        ], [])),
        patch.object(diag_node, "calculate_validity_score", return_value=0.9),
        patch.object(diag_node, "check_vendor_evidence_missing", return_value=False),
        patch.object(diag_node, "build_diagnosis_prompt", return_value="masked prompt"),
    ):
        # Build a chained mock: with_config(...).invoke(prompt) -> response
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = llm_response
        mock_llm = MagicMock()
        mock_llm.with_config.return_value = mock_chain
        mock_llm_factory.return_value = mock_llm

        result = diag_node.diagnose_root_cause(state)  # type: ignore[arg-type]

    # Root cause is now unmasked — contains the real pod + namespace
    assert "etl-worker-7d9f8b-xkp2q" in result["root_cause"]
    assert "tracer-test" in result["root_cause"]
    assert "<POD_0>" not in result["root_cause"]
    assert "<NAMESPACE_0>" not in result["root_cause"]

    # Validated claims also unmasked
    validated = result["validated_claims"]
    assert isinstance(validated, list)
    assert "etl-worker-7d9f8b-xkp2q" in validated[0]["claim"]


def test_diagnose_with_empty_masking_map_is_passthrough() -> None:
    from app.nodes.root_cause_diagnosis import node as diag_node

    state = _state_with_masking(masking_map={})
    llm_response = _mock_llm_response("ROOT_CAUSE: nothing to unmask")
    parsed = _mock_parse_result("plain root cause text")

    with (
        patch.object(diag_node, "get_llm_for_reasoning") as mock_llm_factory,
        patch.object(diag_node, "parse_root_cause", return_value=parsed),
        patch.object(diag_node, "is_clearly_healthy", return_value=False),
        patch.object(
            diag_node,
            "check_evidence_availability",
            return_value=(False, False, True),
        ),
        patch.object(diag_node, "validate_and_categorize_claims", return_value=([], [])),
        patch.object(diag_node, "calculate_validity_score", return_value=1.0),
        patch.object(diag_node, "check_vendor_evidence_missing", return_value=False),
        patch.object(diag_node, "build_diagnosis_prompt", return_value="prompt"),
    ):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = llm_response
        mock_llm = MagicMock()
        mock_llm.with_config.return_value = mock_chain
        mock_llm_factory.return_value = mock_llm

        result = diag_node.diagnose_root_cause(state)  # type: ignore[arg-type]

    assert result["root_cause"] == "plain root cause text"
