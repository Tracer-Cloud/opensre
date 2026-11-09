from __future__ import annotations

import pytest

from surfaces.shared.llm_setup import azure_validation


def test_format_validation_failure_lists_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        azure_validation,
        "list_azure_openai_deployments",
        lambda **_kwargs: ["gpt-4.1-mini"],
    )

    detail = azure_validation.format_validation_failure(
        deployment="gpt-4.1",
        base_url="https://example.openai.azure.com",
        api_key="test-key",
        api_version="2024-10-21",
        error=RuntimeError("Error code: 404 - DeploymentNotFound"),
    )

    assert "deployment 'gpt-4.1'" in detail
    assert "Available deployments: gpt-4.1-mini" in detail


def test_format_validation_failure_passthrough_for_other_errors() -> None:
    detail = azure_validation.format_validation_failure(
        deployment="gpt-4.1",
        base_url="https://example.openai.azure.com",
        api_key="test-key",
        api_version="2024-10-21",
        error=RuntimeError("connection reset"),
    )
    assert detail == "Validation request failed: connection reset"
