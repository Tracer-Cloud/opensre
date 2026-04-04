"""Tests for TracerClientBase request behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import app.integrations.clients.tracer_client.tracer_client_base as tracer_client_base


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.last_url = ""
        self.last_params: Mapping[str, Any] = {}

    def get(self, url: str, params: Mapping[str, Any]) -> _FakeResponse:
        self.last_url = url
        self.last_params = params
        return _FakeResponse({"success": True, "data": []})


def test_get_uses_base_url_and_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeHttpClient()

    monkeypatch.setattr(
        tracer_client_base,
        "extract_org_slug_from_jwt",
        lambda _token: "org-slug",
    )
    monkeypatch.setattr(
        tracer_client_base.httpx,
        "Client",
        lambda **_kwargs: fake_client,
    )

    client = tracer_client_base.TracerClientBase(
        base_url="https://tracer.example.com/",
        org_id="org_123",
        jwt_token="token",
    )

    result = client._get("/api/pipelines", {"orgId": "org_123", "size": 50})

    assert result == {"success": True, "data": []}
    assert fake_client.last_url == "https://tracer.example.com/api/pipelines"
    assert fake_client.last_params == {"orgId": "org_123", "size": 50}


def test_get_defaults_to_empty_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeHttpClient()

    monkeypatch.setattr(
        tracer_client_base,
        "extract_org_slug_from_jwt",
        lambda _token: None,
    )
    monkeypatch.setattr(
        tracer_client_base.httpx,
        "Client",
        lambda **_kwargs: fake_client,
    )

    client = tracer_client_base.TracerClientBase(
        base_url="https://tracer.example.com",
        org_id="org_456",
        jwt_token="token",
    )

    result = client._get("/api/runs")

    assert result == {"success": True, "data": []}
    assert fake_client.last_url == "https://tracer.example.com/api/runs"
    assert fake_client.last_params == {}
