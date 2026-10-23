"""Exercise device OAuth and real LiteLLM HTTP/SSE with synthetic xAI accounts."""

from __future__ import annotations

import json
import stat
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from click.testing import CliRunner

from config.constants.grok import GROK_RESOURCE, GROK_SCOPES
from config.llm_auth.credentials import status
from config.llm_auth.subscription_storage import read_store, subscription_lock, write_store
from core.llm.subscriptions import grok, grok_clients
from core.llm.subscriptions.types import SubscriptionLoginRequired
from core.llm.types import LLMRequestCancelled
from surfaces.cli.app import cli


def _response(output: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": "grok-4.3",
        "output": output
        if output is not None
        else [
            {
                "type": "message",
                "id": "msg-test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "hello", "annotations": []}],
            }
        ],
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    }


class _EventStream(httpx.SyncByteStream):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events, self.read, self.closed = events, 0, False

    def __iter__(self) -> Iterator[bytes]:
        for event in self.events:
            self.read += 1
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_SUBSCRIPTION_AUTH_DIR", str(tmp_path / "subscriptions"))
    monkeypatch.setenv("OPENSRE_GROK_OAUTH_CLIENT_ID", "approved-test-client")
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("XAI_API_KEY", "must-not-use-usage-key")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use-openai-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://wrong.example")
    monkeypatch.setenv("XAI_API_BASE", "https://wrong.example")
    monkeypatch.setattr(grok.time, "sleep", lambda _delay: None)
    state: dict[str, Any] = {"subject": "user-one", "requests": [], "refreshes": [], "polls": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        path = request.url.path
        if path.endswith("openid-configuration"):
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "issuer": "https://auth.x.ai",
                    "device_authorization_endpoint": state.get(
                        "device_endpoint", "https://auth.x.ai/oauth2/device/code"
                    ),
                    "token_endpoint": "https://auth.x.ai/oauth2/token",
                    "userinfo_endpoint": "https://auth.x.ai/userinfo",
                    "revocation_endpoint": "https://auth.x.ai/revoke",
                },
            )
        if path == "/oauth2/device/code":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [state.get("client_id", "approved-test-client")]
            assert form["referrer"] == ["opensre"]
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://accounts.x.ai/device",
                    "interval": 1,
                    "expires_in": state.get("device_lifetime", 600),
                },
            )
        if path == "/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [state.get("client_id", "approved-test-client")]
            if form["grant_type"] == ["refresh_token"]:
                state["refreshes"].append(form)
                if "refresh_error" in state:
                    return httpx.Response(
                        state.get("refresh_status", HTTPStatus.BAD_REQUEST),
                        json={"error": state["refresh_error"]},
                    )
                return httpx.Response(
                    HTTPStatus.OK,
                    json={
                        "access_token": "renewed-token",
                        "refresh_token": "rotated-refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            state["polls"].append(form)
            errors = state.get("poll_errors", [])
            if errors:
                return httpx.Response(HTTPStatus.BAD_REQUEST, json={"error": errors.pop(0)})
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "access_token": "subscription-token",
                    "refresh_token": "original-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": state.get("scope", GROK_SCOPES),
                },
            )
        if path == "/userinfo":
            return httpx.Response(HTTPStatus.OK, json={"sub": state["subject"]})
        if path == "/v1/models":
            assert request.headers["authorization"].startswith("Bearer ")
            if "model_status" in state:
                return httpx.Response(
                    state["model_status"],
                    json={
                        "code": "personal-team-blocked:spending-limit",
                        "error": "provider detail must not be displayed",
                    },
                )
            return httpx.Response(HTTPStatus.OK, json={"data": [{"id": "grok-4.3"}]})
        if path == "/revoke":
            return httpx.Response(state.get("revoke_status", HTTPStatus.OK))
        if path == "/v1/responses":
            assert str(request.url) == f"{GROK_RESOURCE}/responses"
            assert (
                request.headers["authorization"]
                == f"Bearer {state.get('expected_token', 'subscription-token')}"
            )
            body = json.loads(request.content)
            state["completion"] = body
            assert body["model"] == "grok-4.3" and body["stream"] is True
            if "inference_status" in state:
                return httpx.Response(
                    state["inference_status"],
                    json={
                        "error": {"message": "subscription unavailable", "type": "quota_exceeded"}
                    },
                )
            assert body["store"] is False
            assert body["include"] == ["reasoning.encrypted_content"]
            assert request.extensions["timeout"]["read"] >= 900
            events = state.get(
                "events",
                [
                    {
                        "type": "response.output_text.delta",
                        "delta": "hello",
                        "sequence_number": 0,
                        "item_id": "msg-test",
                        "output_index": 0,
                        "content_index": 0,
                    },
                    {"type": "response.completed", "sequence_number": 1, "response": _response()},
                ],
            )
            stream = _EventStream(events)
            state["opened_stream"] = stream
            return httpx.Response(
                HTTPStatus.OK, headers={"content-type": "text/event-stream"}, stream=stream
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda _self, _url: transport)
    return state


def _login(**kwargs: Any) -> str:
    return grok.login(lambda _url: None, select_model=lambda models: models[0][0], **kwargs)


def _store() -> dict[str, Any]:
    with subscription_lock("grok"):
        return read_store("grok")


def _expire() -> None:
    with subscription_lock("grok"):
        data = read_store("grok")
        data["profiles"][data["active"]]["expires_at"] = time.time() - 1
        write_store("grok", data)


def test_cli_login_runtime_streaming_verify_and_logout(wire, tmp_path):
    from core.llm.factory import LLMRole, get_llm, reset_llm_clients
    from core.llm.internal.client_cache_key import current_llm_client_cache_key
    from core.llm.readiness import llm_ready
    from surfaces.shared.llm_setup.auth_service import logout_provider, verify_provider

    result = CliRunner().invoke(cli, ["auth", "login", "grok", "--no-open-browser"])
    assert result.exit_code == 0, result.output
    assert "ABCD-EFGH" in result.output and "secret-device-code" not in result.output
    env = (tmp_path / ".env").read_text()
    assert "LLM_PROVIDER=grok" in env and "GROK_MODEL=grok-4.3" in env
    assert "OPENSRE_LLM_CONNECTION=local" in env
    assert "subscription-token" not in env and "original-refresh" not in env
    assert llm_ready().ready
    assert current_llm_client_cache_key() == ("litellm", "grok:grok-4.3")
    reset_llm_clients()
    try:
        deltas: list[str] = []
        response = get_llm(LLMRole.AGENT).invoke(
            [{"role": "user", "content": "hello"}], on_text_delta=deltas.append
        )
        assert response.content == "hello" and deltas == ["hello"]
        assert (response.input_tokens, response.output_tokens) == (11, 7)
        assert list(grok_clients.llm_client("grok-4.3").invoke_stream("hello")) == ["hello"]
    finally:
        reset_llm_clients()
    assert verify_provider("grok").verified
    assert "session removed" in logout_provider("grok")
    assert not status("grok").configured and not llm_ready().ready
    with pytest.raises(SubscriptionLoginRequired):
        grok.resolve()
    assert stat.S_IMODE((tmp_path / "subscriptions/grok/auth.json").stat().st_mode) == 0o600


def test_device_poll_pending_slowdown_and_account_isolation(wire):
    wire["poll_errors"] = ["authorization_pending", "slow_down"]
    _login()
    first = _store()
    wire["subject"] = "user-two"
    with pytest.raises(SubscriptionLoginRequired, match="identity changed"):
        _login()
    assert _store() == first
    _login(new_account=True)
    second = _store()
    assert len(second["profiles"]) == 2 and second["active"] != first["active"]
    grok.logout()
    final = _store()
    assert final["profiles"][first["active"]] == first["profiles"][first["active"]]
    assert "access_token" not in final["profiles"][second["active"]]


@pytest.mark.parametrize("mutation", ["scope", "denied", "endpoint", "cancel"])
def test_failed_login_preserves_existing_session(wire, mutation):
    _login()
    original = _store()
    if mutation == "scope":
        wire["scope"] = "openid api:access"
    elif mutation == "denied":
        wire["poll_errors"] = ["access_denied"]
    elif mutation == "endpoint":
        wire["device_endpoint"] = "https://attacker.example/steal"

    def select(_models):
        raise KeyboardInterrupt()

    with pytest.raises((SubscriptionLoginRequired, KeyboardInterrupt)):
        if mutation == "cancel":
            grok.login(lambda _url: None, select_model=select)
        else:
            _login()
    assert _store() == original
    assert all(request.url.host != "attacker.example" for request in wire["requests"])


def test_default_client_login_refresh_and_reconnect_without_configuration(wire, monkeypatch):
    monkeypatch.delenv("OPENSRE_GROK_OAUTH_CLIENT_ID")
    wire["client_id"] = "b1a00492-073a-47ea-816f-4c329264a828"
    presented: list[str] = []
    assert grok.login(presented.append, select_model=lambda models: models[0][0]) == "grok-4.3"
    assert presented == ["https://accounts.x.ai/device?user_code=ABCD-EFGH"]
    store = _store()
    account = store["active"]
    assert store["profiles"][account]["client_id"] == wire["client_id"]
    monkeypatch.setenv("OPENSRE_GROK_OAUTH_CLIENT_ID", "other-client")
    _expire()
    assert grok.resolve().token == "renewed-token"
    grok.logout()
    _login(profile_id=account)
    assert _store()["profiles"][account]["client_id"] == wire["client_id"]


def test_model_access_denied_explains_plan_gate_without_replacing_session(wire):
    _login()
    original = _store()
    wire["model_status"] = HTTPStatus.FORBIDDEN
    result = CliRunner().invoke(cli, ["auth", "login", "grok", "--no-open-browser"])
    assert result.exit_code != 0
    assert "active plan and remaining allowance" in result.output
    assert "No API-key fallback" in result.output
    assert "provider detail" not in result.output
    assert "subscription-token" not in result.output
    assert _store() == original
    assert "completion" not in wire


def test_concurrent_refresh_rotates_once_and_keeps_registration(wire, monkeypatch):
    _login()
    monkeypatch.setenv("OPENSRE_GROK_OAUTH_CLIENT_ID", "other-client")
    _expire()
    with ThreadPoolExecutor(max_workers=2) as pool:
        auth = list(pool.map(lambda _: grok.resolve(), range(2)))
    assert [item.token for item in auth] == ["renewed-token", "renewed-token"]
    assert len(wire["refreshes"]) == 1
    assert "renewed-token" not in repr(auth[0])
    assert _store()["profiles"][_store()["active"]]["refresh_token"] == "rotated-refresh"


@pytest.mark.parametrize("terminal", [True, False])
def test_refresh_failure_cleanup_and_no_fallback(wire, terminal):
    _login()
    _expire()
    original = _store()
    wire["refresh_error"] = "invalid_grant" if terminal else "temporarily_unavailable"
    wire["refresh_status"] = HTTPStatus.BAD_REQUEST if terminal else HTTPStatus.SERVICE_UNAVAILABLE
    with pytest.raises(SubscriptionLoginRequired):
        grok_clients.agent_client("grok-4.3").invoke([{"role": "user", "content": "hello"}])
    assert "completion" not in wire
    current = _store()
    if terminal:
        profile = current["profiles"][current["active"]]
        assert profile["client_id"] == "approved-test-client"
        assert "access_token" not in profile and "refresh_token" not in profile
        assert not status("grok").configured
    else:
        assert current == original and status("grok").configured


def test_failed_revocation_still_clears_selected_tokens(wire):
    _login()
    wire["revoke_status"] = HTTPStatus.SERVICE_UNAVAILABLE
    assert "Remote revocation was not confirmed" in grok.logout()
    assert "access_token" not in _store()["profiles"][_store()["active"]]


def test_revocation_cleanup_failure_is_reported(wire, monkeypatch):
    _login()
    original_exit = httpx.Client.__exit__

    def exit_with_error(client: httpx.Client, *args: Any) -> None:
        original_exit(client, *args)
        raise httpx.HTTPError("Client cleanup failed")

    monkeypatch.setattr(httpx.Client, "__exit__", exit_with_error)

    assert "Remote revocation was not confirmed" in grok.logout()
    store = _store()
    profile = store["profiles"][store["active"]]
    assert not {"access_token", "refresh_token", "expires_at"}.intersection(profile)
    assert profile["client_id"] == "approved-test-client"


def test_streamed_tool_arguments_and_tool_result_replay(wire):
    _login()
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "replay-reasoning"},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"service":"api"}',
            "status": "completed",
        },
        {
            "type": "function_call",
            "id": "fc_2",
            "call_id": "call_2",
            "name": "lookup",
            "arguments": '{"service":"worker"}',
            "status": "completed",
        },
    ]
    wire["events"] = [
        {
            "type": "response.output_item.done",
            "output_index": index,
            "sequence_number": index,
            "item": item,
        }
        for index, item in enumerate(output)
    ] + [{"type": "response.completed", "sequence_number": 3, "response": _response([])}]
    client = grok_clients.agent_client("grok-4.3")
    result = client.invoke(
        [{"role": "user", "content": "check service"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {"type": "object", "properties": {"service": {"type": "string"}}},
                },
            }
        ],
    )
    assert result.tool_calls[0].input == {"service": "api"}
    assert wire["completion"]["tools"][0]["name"] == "lookup"
    assert not any(item.get("type") == "additional_tools" for item in wire["completion"]["input"])
    from core.messages import MessageMapper

    mapper = MessageMapper(client)
    results = mapper.to_tool_result_runtime_message(result.tool_calls, ["healthy", "degraded"])
    assert len(results.provider_payloads) == 2
    client.invoke([result.raw_content, *mapper.to_provider_messages([results])])
    replay = wire["completion"]["input"]
    assert replay[0]["encrypted_content"] == "replay-reasoning"
    assert replay[-2:] == [
        {"type": "function_call_output", "call_id": "call_1", "output": json.dumps("healthy")},
        {"type": "function_call_output", "call_id": "call_2", "output": json.dumps("degraded")},
    ]


@pytest.mark.parametrize(
    "status_code", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS]
)
def test_quota_and_auth_errors_never_use_api_key_or_retry(wire, status_code):
    _login()
    wire["inference_status"] = status_code
    before = len(wire["requests"])
    with pytest.raises(RuntimeError):
        grok_clients.agent_client("grok-4.3").invoke([{"role": "user", "content": "hello"}])
    assert len(wire["requests"]) == before + 1


def test_stream_cancellation_and_incomplete_output(wire):
    _login()
    client = grok_clients.agent_client("grok-4.3")
    deltas: list[str] = []
    with pytest.raises(LLMRequestCancelled):
        client.invoke(
            [{"role": "user", "content": "hello"}],
            on_text_delta=deltas.append,
            should_stop=lambda: bool(deltas),
        )
    assert deltas == ["hello"]
    assert wire["opened_stream"].closed and wire["opened_stream"].read == 1
    wire["events"] = [
        {
            "type": "response.incomplete",
            "sequence_number": 0,
            "response": {**_response(), "status": "incomplete"},
        }
    ]
    with pytest.raises(RuntimeError, match="incomplete"):
        client.invoke([{"role": "user", "content": "hello"}])
    wire["events"] = [
        {
            "type": "response.output_text.delta",
            "delta": "partial",
            "sequence_number": 0,
            "item_id": "msg-test",
            "output_index": 0,
            "content_index": 0,
        }
    ]
    with pytest.raises(RuntimeError, match="incomplete"):
        client.invoke([{"role": "user", "content": "hello"}])


def test_unknown_model_cannot_fall_back_to_buffered_inference(wire):
    _login()
    before = len(wire["requests"])
    with pytest.raises(RuntimeError, match="native streaming"):
        grok_clients.agent_client("unrecognized-future-model").invoke(
            [{"role": "user", "content": "hello"}]
        )
    assert len(wire["requests"]) == before


def test_device_expiry_stops_polling_without_replacing_account(wire, monkeypatch):
    _login()
    original = _store()
    wire["device_lifetime"] = 3
    wire["poll_errors"] = ["authorization_pending", "slow_down"]

    class Clock:
        elapsed = 0.0
        delays: list[float] = []
        time = staticmethod(time.time)

        def monotonic(self):
            return self.elapsed

        def sleep(self, seconds):
            self.delays.append(seconds)
            self.elapsed += seconds

    clock = Clock()
    monkeypatch.setattr(grok, "time", clock)
    with pytest.raises(SubscriptionLoginRequired, match="timed out"):
        _login()
    assert clock.delays == [1.0, 1.0, 1.0]
    assert _store() == original


def test_oauth_scope_can_be_omitted_only_when_requested_scopes_are_unchanged(wire, monkeypatch):
    """RFC 6749 permits omission when the granted scopes match the request."""
    transport_for_url = httpx.Client._transport_for_url

    def handle(request):
        response = transport_for_url(None, request.url).handle_request(request)
        if request.url.path == "/oauth2/token":
            data = response.json()
            data.pop("scope", None)
            return httpx.Response(HTTPStatus.OK, json=data)
        return response

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda _self, _url: transport)
    _login()
    assert grok.resolve().token == "subscription-token"
