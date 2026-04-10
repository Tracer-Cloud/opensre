import httpx
import pytest

from app.integrations.trello import (
    DEFAULT_TRELLO_BASE_URL,
    TrelloConfig,
    build_trello_config,
    create_trello_card,
    trello_config_from_env,
    validate_trello_config,
    validate_trello_connection,
)


def test_build_trello_config_defaults() -> None:
    config = build_trello_config({})

    assert config.base_url == DEFAULT_TRELLO_BASE_URL
    assert config.api_key == ""
    assert config.token == ""
    assert config.board_id == ""
    assert config.list_id == ""
    assert config.timeout_seconds == 15.0


def test_trello_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "trello_key")
    monkeypatch.setenv("TRELLO_TOKEN", "trello_token")
    monkeypatch.setenv("TRELLO_BASE_URL", "https://api.trello.com/1")
    monkeypatch.setenv("TRELLO_BOARD_ID", "board123")
    monkeypatch.setenv("TRELLO_LIST_ID", "list123")

    config = trello_config_from_env()

    assert config is not None
    assert config.api_key == "trello_key"
    assert config.token == "trello_token"
    assert config.board_id == "board123"
    assert config.list_id == "list123"
    assert config.base_url == "https://api.trello.com/1"


def test_trello_config_from_env_returns_none_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)

    config = trello_config_from_env()

    assert config is None


def test_validate_trello_connection_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="trello_token",
        board_id="board123",
        list_id="list123",
    )

    def fake_request_json(*args, **kwargs):
        return {"id": "member123", "username": "test_user"}

    monkeypatch.setattr("app.integrations.trello._request_json", fake_request_json)

    result = validate_trello_connection(config=config)

    assert result["username"] == "test_user"


def test_validate_trello_config_missing_api_key() -> None:
    config = TrelloConfig(
        api_key="",
        token="trello_token",
        board_id="board123",
        list_id="list123",
    )

    result = validate_trello_config(config)

    assert result.ok is False
    assert "api key" in result.detail.lower()


def test_validate_trello_config_missing_token() -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="",
        board_id="board123",
        list_id="list123",
    )

    result = validate_trello_config(config)

    assert result.ok is False
    assert "token" in result.detail.lower()


def test_validate_trello_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="trello_token",
        board_id="board123",
        list_id="list123",
    )

    def fake_validate_connection(*, config: TrelloConfig):
        return {"id": "member123", "username": "test_user"}

    monkeypatch.setattr(
        "app.integrations.trello.validate_trello_connection",
        fake_validate_connection,
    )

    result = validate_trello_config(config)

    assert result.ok is True
    assert "@test_user" in result.detail


def test_validate_trello_config_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="bad_key",
        token="bad_token",
        board_id="board123",
        list_id="list123",
    )

    request = httpx.Request("GET", "https://api.trello.com/1/members/me")
    response = httpx.Response(401, request=request, text="unauthorized")

    def fake_validate_connection(*, config: TrelloConfig):
        raise httpx.HTTPStatusError(
            "Client error '401 Unauthorized' for url 'https://api.trello.com/1/members/me'",
            request=request,
            response=response,
        )

    monkeypatch.setattr(
        "app.integrations.trello.validate_trello_connection",
        fake_validate_connection,
    )

    result = validate_trello_config(config)

    assert result.ok is False
    assert "401" in result.detail or "unauthorized" in result.detail.lower()


def test_create_trello_card_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="trello_token",
        board_id="board123",
        list_id="list123",
    )

    def fake_request_json(*args, **kwargs):
        return {
            "id": "card123",
            "name": "Critical incident",
            "desc": "Root cause details",
            "idList": "list123",
        }

    monkeypatch.setattr("app.integrations.trello._request_json", fake_request_json)

    result = create_trello_card(
        config=config,
        name="Critical incident",
        desc="Root cause details",
    )

    assert result["id"] == "card123"
    assert result["name"] == "Critical incident"


def test_create_trello_card_uses_override_list_id(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="trello_token",
        board_id="board123",
        list_id="default_list",
    )

    captured_kwargs: dict[str, object] = {}

    def fake_request_json(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {"id": "card123", "idList": "override_list"}

    monkeypatch.setattr("app.integrations.trello._request_json", fake_request_json)

    result = create_trello_card(
        config=config,
        name="Incident",
        desc="Details",
        list_id="override_list",
    )

    assert result["id"] == "card123"
    params = captured_kwargs["params"]
    assert ("idList", "override_list") in params


def test_create_trello_card_raises_without_list_id() -> None:
    config = TrelloConfig(
        api_key="trello_key",
        token="trello_token",
        board_id="board123",
        list_id="",
    )

    with pytest.raises(ValueError, match="list_id"):
        create_trello_card(
            config=config,
            name="Incident",
            desc="Details",
        )
