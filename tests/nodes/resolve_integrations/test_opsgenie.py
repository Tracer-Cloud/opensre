"""Tests for OpsGenie integration resolution and env var loading."""

from __future__ import annotations

from typing import Any

import pytest

from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations


def _active_opsgenie(api_key: str = "og-key", region: str = "us") -> list[dict[str, Any]]:
    return [
        {
            "id": "store-opsgenie",
            "service": "opsgenie",
            "status": "active",
            "credentials": {"api_key": api_key, "region": region},
        }
    ]


def test_classify_opsgenie_from_store() -> None:
    resolved = _classify_integrations(_active_opsgenie())
    assert "opsgenie" in resolved
    assert resolved["opsgenie"]["api_key"] == "og-key"
    assert resolved["opsgenie"]["region"] == "us"


def test_classify_opsgenie_eu_region() -> None:
    resolved = _classify_integrations(_active_opsgenie(region="eu"))
    assert resolved["opsgenie"]["region"] == "eu"


def test_classify_opsgenie_skipped_without_api_key() -> None:
    resolved = _classify_integrations(_active_opsgenie(api_key=""))
    assert "opsgenie" not in resolved


def test_classify_opsgenie_skipped_when_inactive() -> None:
    integrations = [
        {
            "id": "x",
            "service": "opsgenie",
            "status": "inactive",
            "credentials": {"api_key": "og-key"},
        }
    ]
    resolved = _classify_integrations(integrations)
    assert "opsgenie" not in resolved


def test_load_env_opsgenie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSGENIE_API_KEY", "env-key")
    monkeypatch.setenv("OPSGENIE_REGION", "eu")
    integrations = _load_env_integrations()
    og = [i for i in integrations if i["service"] == "opsgenie"]
    assert len(og) == 1
    assert og[0]["credentials"]["api_key"] == "env-key"
    assert og[0]["credentials"]["region"] == "eu"


def test_load_env_opsgenie_absent_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSGENIE_API_KEY", raising=False)
    integrations = _load_env_integrations()
    og = [i for i in integrations if i["service"] == "opsgenie"]
    assert len(og) == 0


def test_load_env_opsgenie_defaults_region_to_us(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSGENIE_API_KEY", "env-key")
    monkeypatch.delenv("OPSGENIE_REGION", raising=False)
    integrations = _load_env_integrations()
    og = [i for i in integrations if i["service"] == "opsgenie"]
    assert og[0]["credentials"]["region"] == "us"
