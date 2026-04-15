from __future__ import annotations

import httpx
import pytest

from app.integrations.trello import TrelloConfig, create_trello_card, validate_trello_config


def test_trello_validation_and_card_creation_e2e(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="trello_token",
        board_id="board123",
        list_id="list123",
    )

    calls: list[tuple[str, str]] = []

    def fake_request_json(config, method, path, *, params=None, json=None):
        calls.append((method, path))

        if method == "GET" and path == "/members/me":
            return {"id": "member123", "username": "test_user"}

        if method == "POST" and path == "/cards":
            return {
                "id": "card123",
                "name": "Critical incident",
                "desc": "Root cause details",
                "idList": "list123",
            }

        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr("app.integrations.trello._request_json", fake_request_json)

    validation = validate_trello_config(config)
    assert validation.ok is True
    assert "@test_user" in validation.detail

    result = create_trello_card(
        config=config,
        name="Critical incident",
        desc="Root cause details",
    )

    assert result["id"] == "card123"
    assert result["name"] == "Critical incident"
    assert result["idList"] == "list123"
    assert calls == [("GET", "/members/me"), ("POST", "/cards")]


def test_trello_validation_failure_e2e(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="bad_key",
        token="bad_token",
        board_id="board123",
        list_id="list123",
    )

    request = httpx.Request("GET", "https://api.trello.com/1/members/me")
    response = httpx.Response(401, request=request, text="unauthorized")

    def fake_request_json(config, method, path, *, params=None, json=None):
        if method == "GET" and path == "/members/me":
            raise httpx.HTTPStatusError(
                "Client error '401 Unauthorized' for url 'https://api.trello.com/1/members/me'",
                request=request,
                response=response,
            )
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr("app.integrations.trello._request_json", fake_request_json)

    validation = validate_trello_config(config)
    assert validation.ok is False
    assert "401" in validation.detail or "unauthorized" in validation.detail.lower()
