"""Dynatrace configuration and classification tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from integrations.dynatrace import classify
from integrations.dynatrace.config import DynatraceIntegrationConfig


def test_config_normalizes_tenant_and_bearer_token() -> None:
    config = DynatraceIntegrationConfig(
        tenant_url=" https://abc12345.apps.dynatrace.com/ ",
        platform_token="Bearer fake-platform-token",
    )

    assert config.tenant_url == "https://abc12345.apps.dynatrace.com"
    assert config.platform_token == "fake-platform-token"


@pytest.mark.parametrize(
    "tenant_url",
    [
        "http://abc12345.apps.dynatrace.com",
        "https://dynatrace.example.com",
        "https://abc12345.apps.dynatrace.com/ui/apps",
        "https://abc12345.apps.dynatrace.com.evil.example",
    ],
)
def test_config_rejects_non_tenant_origins(tenant_url: str) -> None:
    with pytest.raises(ValidationError):
        DynatraceIntegrationConfig(tenant_url=tenant_url, platform_token="fake-token")


def test_classify_requires_both_credentials() -> None:
    configured, key = classify(
        {
            "tenant_url": "https://abc12345.apps.dynatrace.com",
            "platform_token": "fake-token",
        },
        "dynatrace-prod",
    )
    assert configured is not None
    assert configured.integration_id == "dynatrace-prod"
    assert key == "dynatrace"

    missing, missing_key = classify(
        {"tenant_url": "https://abc12345.apps.dynatrace.com"},
        "dynatrace-prod",
    )
    assert missing is None
    assert missing_key is None
