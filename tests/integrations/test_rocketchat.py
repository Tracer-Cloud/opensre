from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from integrations.config_models import RocketChatConfig
from integrations.rocketchat import classify
from integrations.rocketchat.verifier import verify_rocketchat

# ---------------------------------------------------------------------------
# RocketChatConfig
# ---------------------------------------------------------------------------


def test_config_strips_trailing_slash_from_server_url() -> None:
    cfg = RocketChatConfig.model_validate(
        {"server_url": "https://chat.example.com/", "auth_token": "tok", "user_id": "u1"}
    )
    assert cfg.server_url == "https://chat.example.com"


def test_config_rejects_server_url_without_scheme() -> None:
    with pytest.raises(ValidationError):
        RocketChatConfig.model_validate(
            {"server_url": "chat.example.com", "auth_token": "tok", "user_id": "u1"}
        )


@pytest.mark.parametrize("field", ["server_url", "auth_token", "user_id"])
def test_config_rejects_blank_pat_fields_without_webhook(field: str) -> None:
    payload = {
        "server_url": "https://chat.example.com",
        "auth_token": "tok",
        "user_id": "u1",
    }
    payload[field] = "   "
    with pytest.raises(ValidationError):
        RocketChatConfig.model_validate(payload)


def test_config_accepts_webhook_only() -> None:
    cfg = RocketChatConfig.model_validate({"webhook_url": "https://chat.example.com/hooks/abc/def"})
    assert cfg.webhook_url == "https://chat.example.com/hooks/abc/def"
    assert cfg.auth_token == ""


def test_config_accepts_incomplete_pat_when_webhook_present() -> None:
    cfg = RocketChatConfig.model_validate(
        {"webhook_url": "https://chat.example.com/hooks/abc/def", "auth_token": "tok"}
    )
    assert cfg.webhook_url


def test_config_rejects_invalid_webhook_url() -> None:
    with pytest.raises(ValidationError):
        RocketChatConfig.model_validate({"webhook_url": "chat.example.com/hooks/abc"})


def test_config_accepts_both_modes() -> None:
    cfg = RocketChatConfig.model_validate(
        {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "user_id": "u1",
            "webhook_url": "https://chat.example.com/hooks/abc/def",
        }
    )
    assert cfg.server_url and cfg.webhook_url


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_returns_config_for_valid_credentials() -> None:
    cfg, key = classify(
        {
            "server_url": "https://chat.example.com",
            "auth_token": "tok",
            "user_id": "u1",
            "default_channel": "#incidents",
        },
        record_id="rec-1",
    )
    assert key == "rocketchat"
    assert cfg is not None
    assert cfg.server_url == "https://chat.example.com"
    assert cfg.default_channel == "#incidents"


def test_classify_skips_when_auth_token_missing() -> None:
    assert classify({"server_url": "https://chat.example.com"}, record_id="rec-1") == (None, None)


def test_classify_accepts_webhook_only_credentials() -> None:
    cfg, key = classify(
        {"webhook_url": "https://chat.example.com/hooks/abc/def"},
        record_id="rec-1",
    )
    assert key == "rocketchat"
    assert cfg is not None
    assert cfg.webhook_url == "https://chat.example.com/hooks/abc/def"


def test_classify_validation_error_returns_none_and_reports() -> None:
    """A ValidationError in classify() returns (None, None) and reports a
    sanitized wrapper (no secret field values), mirroring the Discord rule."""
    secret_value = "leaked-secret-token"

    with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_report:
        result = classify(
            {"auth_token": secret_value, "server_url": "not-a-url", "user_id": "u1"},
            record_id="rec-rocketchat",
        )

    assert result == (None, None)
    assert mock_report.call_count == 1
    exc_arg = mock_report.call_args.args[0]
    assert not isinstance(exc_arg, ValidationError)
    assert str(exc_arg) == "rocketchat config validation failed"
    assert secret_value not in str(exc_arg)


# ---------------------------------------------------------------------------
# verify_rocketchat
# ---------------------------------------------------------------------------

_VALID_CONFIG = {
    "server_url": "https://chat.example.com",
    "auth_token": "tok",
    "user_id": "u1",
}


def _mock_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def test_verify_missing_server_url() -> None:
    result = verify_rocketchat("local env", {"auth_token": "tok", "user_id": "u1"})
    assert result["status"] == "missing"
    assert "server_url" in result["detail"]


def test_verify_missing_auth_token() -> None:
    result = verify_rocketchat("local env", {"server_url": "https://chat.example.com"})
    assert result["status"] == "missing"
    assert "auth_token" in result["detail"]


def test_verify_passes_and_reports_username(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int) -> MagicMock:
        captured["url"] = url
        captured["headers"] = headers
        return _mock_response(200, {"success": True, "username": "opensre.bot"})

    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", _fake_get)
    result = verify_rocketchat("local env", _VALID_CONFIG)

    assert result["status"] == "passed"
    assert "@opensre.bot" in result["detail"]
    assert captured["url"] == "https://chat.example.com/api/v1/me"
    assert captured["headers"] == {"X-Auth-Token": "tok", "X-User-Id": "u1"}


def test_verify_reports_invalid_credentials_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.rocketchat.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(401, {"status": "error", "message": "Unauthorized"}),
    )
    result = verify_rocketchat("local env", _VALID_CONFIG)
    assert result["status"] == "failed"
    assert "invalid or expired" in result["detail"]


def test_verify_reports_unexpected_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.rocketchat.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(503, {}),
    )
    result = verify_rocketchat("local env", _VALID_CONFIG)
    assert result["status"] == "failed"
    assert "HTTP 503" in result["detail"]


def test_verify_reports_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", _raise)
    result = verify_rocketchat("local env", _VALID_CONFIG)
    assert result["status"] == "failed"
    assert "connection refused" in result["detail"]


def test_verify_passes_with_non_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", lambda *_a, **_kw: resp)
    result = verify_rocketchat("local env", _VALID_CONFIG)
    assert result["status"] == "passed"
    assert "@unknown" in result["detail"]


# ---------------------------------------------------------------------------
# verify_rocketchat — webhook mode
# ---------------------------------------------------------------------------

_WEBHOOK_CONFIG = {"webhook_url": "https://chat.example.com/hooks/abc/def"}


@pytest.mark.parametrize("status_code", [200, 400, 403, 405])
def test_verify_webhook_only_passes_on_reachable_probe(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(status_code, {})

    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", _fake_get)
    result = verify_rocketchat("local env", _WEBHOOK_CONFIG)

    assert result["status"] == "passed"
    assert "non-posting probe" in result["detail"]
    assert captured["url"] == _WEBHOOK_CONFIG["webhook_url"]


def test_verify_webhook_fails_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.rocketchat.verifier.httpx.get",
        lambda *_a, **_kw: _mock_response(404, {}),
    )
    result = verify_rocketchat("local env", _WEBHOOK_CONFIG)
    assert result["status"] == "failed"
    assert "404" in result["detail"]


def test_verify_webhook_fails_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", _raise)
    result = verify_rocketchat("local env", _WEBHOOK_CONFIG)
    assert result["status"] == "failed"
    assert "unreachable" in result["detail"]


def test_verify_webhook_connection_error_redacts_webhook_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhook_url = _WEBHOOK_CONFIG["webhook_url"]

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise ConnectionError(f"failed to connect to {webhook_url}")

    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", _raise)
    result = verify_rocketchat("local env", _WEBHOOK_CONFIG)

    assert result["status"] == "failed"
    assert webhook_url not in result["detail"]
    assert "<redacted>" in result["detail"]


def test_verify_prefers_pat_probe_when_both_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_get(url: str, **kw: Any) -> MagicMock:
        captured["url"] = url
        return _mock_response(200, {"success": True, "username": "opensre.bot"})

    monkeypatch.setattr("integrations.rocketchat.verifier.httpx.get", _fake_get)
    result = verify_rocketchat("local env", {**_VALID_CONFIG, **_WEBHOOK_CONFIG})

    assert result["status"] == "passed"
    assert captured["url"] == "https://chat.example.com/api/v1/me"
