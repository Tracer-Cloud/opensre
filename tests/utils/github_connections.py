"""Trusted app records for tests whose subject assumes GitHub is connected."""

from typing import Any

import pytest

from config.constants.tracer import TRACER_JWT_TOKEN_ENV


def connect_github_app(monkeypatch: pytest.MonkeyPatch, token: str = "app-token") -> None:
    def load_connections(**_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "app-1",
                "service": "github",
                "status": "active",
                "origin": "webapp",
                "instances": [
                    {
                        "name": "default",
                        "tags": {"connection_origin": "webapp"},
                        "credentials": {
                            "auth_token": token,
                            "url": "https://api.githubcopilot.com/mcp/",
                            "mode": "streamable-http",
                        },
                    }
                ],
            }
        ]

    from infrastructure.harness_providers import integration_resolution as ports
    from integrations.harness_adapters import register_harness_adapters

    monkeypatch.setattr(ports, "_installed_adapters", ports._installed_adapters)
    register_harness_adapters()
    monkeypatch.delenv(TRACER_JWT_TOKEN_ENV, raising=False)
    monkeypatch.setattr("integrations.webapp_vault.fetch_webapp_org_integrations", lambda: None)
    monkeypatch.setattr("integrations.webapp_vault.webapp_vault_configured", lambda: False)
    monkeypatch.setattr(
        "integrations.account_integrations.load_account_integrations", load_connections
    )
