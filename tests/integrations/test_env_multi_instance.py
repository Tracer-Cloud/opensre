"""Tests for ``*_INSTANCES`` env var parsing in load_env_integrations."""

from __future__ import annotations

import json

import pytest

from app.integrations.catalog import load_env_integrations


def _clear_env(monkeypatch) -> None:
    for key in (
        "GRAFANA_INSTANCES",
        "GRAFANA_INSTANCE_URL",
        "GRAFANA_READ_TOKEN",
        "DD_INSTANCES",
        "DD_API_KEY",
        "DD_APP_KEY",
        "DD_SITE",
        "HONEYCOMB_INSTANCES",
        "HONEYCOMB_API_KEY",
        "CORALOGIX_INSTANCES",
        "CORALOGIX_API_KEY",
        "AWS_INSTANCES",
        "AWS_ROLE_ARN",
        "AWS_EXTERNAL_ID",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_grafana_instances_json_produces_single_record_with_multiple_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #527 bug #2 regression: env multi-instance must NOT split into N
    records, which the merge_integrations_by_service chokepoint would collapse."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(
        "GRAFANA_INSTANCES",
        json.dumps(
            [
                {"name": "prod", "tags": {"env": "prod"}, "endpoint": "https://p", "api_key": "kp"},
                {"name": "staging", "tags": {"env": "staging"}, "endpoint": "https://s", "api_key": "ks"},
            ]
        ),
    )

    records = load_env_integrations()
    grafana_records = [r for r in records if r.get("service") == "grafana"]
    assert len(grafana_records) == 1  # ← must be exactly one
    instances = grafana_records[0]["instances"]
    assert [i["name"] for i in instances] == ["prod", "staging"]
    assert instances[0]["credentials"]["endpoint"] == "https://p"
    assert instances[1]["credentials"]["endpoint"] == "https://s"


def test_grafana_instances_accepts_nested_credentials_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(
        "GRAFANA_INSTANCES",
        json.dumps(
            [
                {
                    "name": "prod",
                    "tags": {"env": "prod"},
                    "credentials": {"endpoint": "https://p", "api_key": "kp"},
                }
            ]
        ),
    )
    records = load_env_integrations()
    grafana = [r for r in records if r.get("service") == "grafana"][0]
    creds = grafana["instances"][0]["credentials"]
    assert creds == {"endpoint": "https://p", "api_key": "kp"}


def test_bad_json_logs_warning_and_falls_through_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("GRAFANA_INSTANCES", "this is not json")
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://legacy")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "legacy-key")

    import logging

    with caplog.at_level(logging.WARNING, logger="app.integrations.catalog"):
        records = load_env_integrations()
    grafana_records = [r for r in records if r.get("service") == "grafana"]
    assert len(grafana_records) == 1
    # Falls through to LEGACY shape, not the new instances shape
    assert "instances" not in grafana_records[0]
    assert grafana_records[0]["credentials"]["endpoint"] == "https://legacy"
    assert any("GRAFANA_INSTANCES is not valid JSON" in r.message for r in caplog.records)


def test_instances_env_var_suppresses_legacy_single_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(
        "GRAFANA_INSTANCES",
        json.dumps(
            [{"name": "prod", "endpoint": "https://p", "api_key": "kp"}]
        ),
    )
    # Also set legacy vars — they must be ignored
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://legacy")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "legacy-key")

    records = load_env_integrations()
    grafana_records = [r for r in records if r.get("service") == "grafana"]
    # Exactly one record — legacy vars did NOT create a second one
    assert len(grafana_records) == 1
    assert "instances" in grafana_records[0]


def test_missing_env_var_uses_legacy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://legacy")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "legacy-key")

    records = load_env_integrations()
    grafana_records = [r for r in records if r.get("service") == "grafana"]
    assert len(grafana_records) == 1
    assert "instances" not in grafana_records[0]
    assert grafana_records[0]["credentials"]["api_key"] == "legacy-key"


def test_empty_json_array_falls_through_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("GRAFANA_INSTANCES", "[]")
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://legacy")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "legacy-key")

    records = load_env_integrations()
    grafana_records = [r for r in records if r.get("service") == "grafana"]
    assert len(grafana_records) == 1
    assert "instances" not in grafana_records[0]


@pytest.mark.parametrize(
    "env_name,service,entry",
    [
        (
            "DD_INSTANCES",
            "datadog",
            {"name": "prod", "api_key": "k", "app_key": "a", "site": "datadoghq.com"},
        ),
        (
            "HONEYCOMB_INSTANCES",
            "honeycomb",
            {"name": "prod", "api_key": "k", "dataset": "__all__"},
        ),
        (
            "CORALOGIX_INSTANCES",
            "coralogix",
            {"name": "prod", "api_key": "k", "base_url": "https://api.coralogix.com"},
        ),
        (
            "AWS_INSTANCES",
            "aws",
            {"name": "prod", "role_arn": "arn:aws:iam::1:role/r", "external_id": "e"},
        ),
    ],
)
def test_instances_env_var_for_all_5_providers(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    service: str,
    entry: dict,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(env_name, json.dumps([entry, {**entry, "name": "staging"}]))
    records = load_env_integrations()
    service_records = [r for r in records if r.get("service") == service]
    assert len(service_records) == 1
    assert [i["name"] for i in service_records[0]["instances"]] == ["prod", "staging"]
