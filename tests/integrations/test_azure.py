from __future__ import annotations

from integrations.azure.verifier import verify_azure
from integrations.config_models import AzureIntegrationConfig


def test_azure_max_results_clamped_to_hard_limit() -> None:
    cfg = AzureIntegrationConfig.model_validate(
        {
            "workspace_id": "workspace",
            "access_token": "token",
            "max_results": 400,
        }
    )

    assert cfg.max_results == 200


def test_verify_azure_missing_workspace_id() -> None:
    result = verify_azure("local env", {"access_token": "token"})

    assert result["status"] == "missing"
    # Azure verifier intentionally returns one shared message for either missing field.
    assert result["detail"] == "Missing workspace_id or access_token."


def test_verify_azure_missing_access_token() -> None:
    result = verify_azure("local env", {"workspace_id": "workspace"})

    assert result["status"] == "missing"
    # Azure verifier intentionally returns one shared message for either missing field.
    assert result["detail"] == "Missing workspace_id or access_token."


def test_verify_azure_passes_with_default_endpoint() -> None:
    result = verify_azure(
        "local env",
        {"workspace_id": "workspace", "access_token": "token"},
    )

    assert result["status"] == "passed"
    assert result["detail"].endswith("via https://api.loganalytics.io.")


def test_verify_azure_passes_with_custom_endpoint() -> None:
    result = verify_azure(
        "local env",
        {
            "workspace_id": "workspace",
            "access_token": "token",
            "endpoint": "https://custom.loganalytics.example.com",
        },
    )

    assert result["status"] == "passed"
    assert result["detail"].endswith("via https://custom.loganalytics.example.com.")
