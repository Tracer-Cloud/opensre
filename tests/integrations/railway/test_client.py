from __future__ import annotations

import subprocess

import pytest

from integrations.config_models import RailwayIntegrationConfig
from integrations.railway import classify
from integrations.railway.client import RailwayClient, RailwayOperationError


def _completed(
    *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["railway"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_classify_normalizes_default_railway_scope() -> None:
    config, service = classify(
        {
            "token": " railway-token ",
            "project": " project-id ",
            "service": " service-id ",
            "environment": " production ",
        },
        "railway-record",
    )

    assert service == "railway"
    assert config is not None
    assert config.token == "railway-token"
    assert config.project == "project-id"
    assert config.service == "service-id"
    assert config.environment == "production"
    assert config.integration_id == "railway-record"


def test_catalog_loads_railway_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.catalog import load_env_integrations

    monkeypatch.setenv("RAILWAY_TOKEN", "railway-token")
    monkeypatch.setenv("RAILWAY_PROJECT", "project-id")
    monkeypatch.setenv("RAILWAY_SERVICE", "service-id")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    railway = next(record for record in load_env_integrations() if record["service"] == "railway")

    assert railway["credentials"]["token"] == "railway-token"
    assert railway["credentials"]["project"] == "project-id"


def test_client_uses_default_scope_when_tool_omits_overrides() -> None:
    client = RailwayClient(
        RailwayIntegrationConfig(
            project="project-id", service="service-id", environment="production"
        )
    )

    assert client.resolve_scope() == ("project-id", "service-id", "production")


def test_client_rejects_missing_scope_after_defaults() -> None:
    client = RailwayClient(RailwayIntegrationConfig())

    with pytest.raises(RailwayOperationError, match="project, service, and environment"):
        client.resolve_scope()


def test_probe_reports_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RailwayClient(RailwayIntegrationConfig())
    monkeypatch.setattr("integrations.railway.client.shutil.which", lambda _command: None)

    probe = client.probe_access()

    assert probe.status == "missing"
