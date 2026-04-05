"""Tests for Vercel integration handling in resolve_integrations node."""

from __future__ import annotations

import pytest

from app.nodes.resolve_integrations.node import _classify_integrations, _load_env_integrations


def _vercel_store_entry(api_token: str = "tok_test", team_id: str = "") -> dict:
    return {
        "id": "vercel-store-1",
        "service": "vercel",
        "status": "active",
        "credentials": {"api_token": api_token, "team_id": team_id},
    }


def test_classify_integrations_resolves_vercel() -> None:
    resolved = _classify_integrations([_vercel_store_entry(team_id="team_xyz")])
    vercel = resolved.get("vercel")
    assert vercel is not None
    assert vercel["api_token"] == "tok_test"
    assert vercel["team_id"] == "team_xyz"


def test_classify_integrations_skips_vercel_without_token() -> None:
    entry = _vercel_store_entry(api_token="")
    resolved = _classify_integrations([entry])
    assert "vercel" not in resolved


def test_classify_integrations_skips_inactive_vercel() -> None:
    entry = {**_vercel_store_entry(), "status": "inactive"}
    resolved = _classify_integrations([entry])
    assert "vercel" not in resolved


def test_classify_integrations_vercel_team_id_optional() -> None:
    resolved = _classify_integrations([_vercel_store_entry(team_id="")])
    assert resolved["vercel"]["team_id"] == ""


def test_load_env_integrations_reads_vercel_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL_API_TOKEN", "tok_from_env")
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)

    integrations = _load_env_integrations()
    vercel_entries = [i for i in integrations if i["service"] == "vercel"]

    assert len(vercel_entries) == 1
    creds = vercel_entries[0]["credentials"]
    assert creds["api_token"] == "tok_from_env"
    assert creds["team_id"] == ""
    assert vercel_entries[0]["status"] == "active"


def test_load_env_integrations_reads_vercel_team_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL_API_TOKEN", "tok_from_env")
    monkeypatch.setenv("VERCEL_TEAM_ID", "team_from_env")

    integrations = _load_env_integrations()
    vercel_entries = [i for i in integrations if i["service"] == "vercel"]

    assert vercel_entries[0]["credentials"]["team_id"] == "team_from_env"


def test_load_env_integrations_skips_vercel_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL_API_TOKEN", raising=False)

    integrations = _load_env_integrations()
    vercel_entries = [i for i in integrations if i["service"] == "vercel"]
    assert len(vercel_entries) == 0
