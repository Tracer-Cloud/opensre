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

_TEST_API_KEY = "test-trello-api-key"  # noqa: S105
_TEST_TOKEN = "test-trello-token"  # noqa: S105
_TEST_BOARD_ID = "board123"
_TEST_LIST_ID = "list123"


def _make_config(**overrides: object) -> TrelloConfig:
    """Build a TrelloConfig with sensible test defaults."""
    defaults: dict[str, object] = {
        "api_key": _TEST_API_KEY,
        "token": _TEST_TOKEN,
        "board_id": _TEST_BOARD_ID,
        "list_id": _TEST_LIST_ID,
    }
    defaults.update(overrides)
    return TrelloConfig(**defaults)  # type: ignore[arg-type]


def test_build_trello_config_defaults() -> None:
    config = build_trello_config({})

    assert config.base_url == DEFAULT_TRELLO_BASE_URL
    assert config.api_key == ""
    assert config.token == ""
    assert config.board_id == ""
    assert config.list_id == ""
    assert config.timeout_seconds == 15.0


def test_trello_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", _TEST_API_KEY)
    monkeypatch.setenv("TRELLO_TOKEN", _TEST_TOKEN)
    monkeypatch.setenv("TRELLO_BASE_URL", "https://api.trello.com/1")
    monkeypatch.setenv("TRELLO_BOARD_ID", _TEST_BOARD_ID)
    monkeypatch.setenv("TRELLO_LIST_ID", _TEST_LIST_ID)

    config = trello_config_from_env()

    assert config is not None
    assert config.api_key == _TEST_API_KEY
    assert config.token == _TEST_TOKEN
    assert config.board_id == _TEST_BOARD_ID
    assert config.list_id == _TEST_LIST_ID
    assert config.base_url == "https://api.trello.com/1"


def test_trello_config_from_env_returns_none_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)

    config = trello_config_from_env()

    assert config is None


def test_validate_trello_connection_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config()

    def fake_request_json(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"id": "member123", "username": "test_user"}

    monkeypatch.setattr("app.integrations.trello._request_json", fake_request_json)

    result = validate_trello_connection(config=config)

    assert result["username"] == "test_user"


def test_validate_trello_config_missing_api_key() -> None:
    config = _make_config(api_key="", token=_TEST_TOKEN)

    result = validate_trello_config(config)

    assert result.ok is False
    assert "api key" in result.detail.lower()


def test_validate_trello_config_missing_token() -> None:
    config = _make_config(token="")

    result = validate_trello_config(config)

    assert result.ok is False
    assert "token" in result.detail.lower()


def test_validate_trello_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config()

    def fake_validate_connection(*, config: TrelloConfig) -> dict[str, str]:
        return {"id": "member123", "username": "test_user"}

    monkeypatch.setattr(
        "app.integrations.trello.validate_trello_connection",
        fake_validate_connection,
    )

    result = validate_trello_config(config)

    assert result.ok is True
    assert "@test_user" in result.detail


def test_validate_trello_config_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(api_key="bad_key", token="bad_token")  # noqa: S106

    request = httpx.Request("GET", "https://api.trello.com/1/members/me")
    response = httpx.Response(401, request=request, text="unauthorized")

    def fake_validate_connection(*, config: TrelloConfig) -> None:
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
    config = _make_config()

    def fake_request_json(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {
            "id": "card123",
            "name": "Critical incident",
            "desc": "Root cause details",
            "idList": _TEST_LIST_ID,
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
    config = _make_config(list_id="default_list")

    captured_kwargs: dict[str, object] = {}

    def fake_request_json(*_args: object, **kwargs: object) -> dict[str, str]:
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
    params: list[tuple[str, object]] = captured_kwargs["params"]  # type: ignore[assignment]
    assert ("idList", "override_list") in params


def test_create_trello_card_raises_without_list_id() -> None:
    config = _make_config(list_id="")

    with pytest.raises(ValueError, match="list_id"):
        create_trello_card(
            config=config,
            name="Incident",
            desc="Details",
        )
