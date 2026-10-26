"""Tests for the unified LLM factory (``core.llm.factory``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_harness.llm_resolution import agent_llm_is_cli_backed
from core.llm.factory import (
    _MODEL_TYPE_BY_ROLE,
    LLMRole,
    LLMRoute,
    get_llm,
    reset_llm_clients,
    resolve_llm_route,
)
from core.llm.internal.client_cache_key import current_llm_client_cache_key
from core.llm.types import ModelType


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.account.account_llm_route", lambda: None)
    reset_llm_clients()
    yield
    reset_llm_clients()


def test_resolve_llm_route_reports_provider_and_sdk_transport(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "config.llm_settings.resolve_llm_settings", lambda: SimpleNamespace(provider="anthropic")
    )
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    route = resolve_llm_route()

    assert route.provider == "anthropic"
    assert route.use_litellm is False
    assert route.cli_provider_registration is None


def test_resolve_llm_route_azure_forces_litellm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "config.llm_settings.resolve_llm_settings", lambda: SimpleNamespace(provider="azure-openai")
    )
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    route = resolve_llm_route()

    # Azure always routes through LiteLLM even without the transport flag.
    assert route.use_litellm is True


def test_account_login_forces_hosted_openai_sdk_route(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1",
            model="gpt-5.4-mini",
        ),
    )
    monkeypatch.setenv("LLM_PROVIDER", "custom-openai")
    monkeypatch.delenv("CUSTOM_OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")

    route = resolve_llm_route()

    assert route.provider == "openai"
    assert route.use_litellm is False
    assert route.cli_provider_registration is None


def test_account_login_uses_a_distinct_client_cache_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    local_key = current_llm_client_cache_key()
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1",
            model="gpt-5.4-mini",
        ),
    )

    assert current_llm_client_cache_key() != local_key
    assert current_llm_client_cache_key() == (
        "sdk",
        "account:openai:https://app.opensre.com/api/llm/v1:gpt-5.4-mini",
    )


def test_account_model_change_invalidates_the_client_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = SimpleNamespace(
        base_url="https://app.opensre.com/api/llm/v1",
        model="gpt-5.4-mini",
    )
    monkeypatch.setattr("config.account.account_llm_route", lambda: route)
    first = current_llm_client_cache_key()

    route.model = "gpt-5.5-mini"

    assert current_llm_client_cache_key() != first


def test_get_llm_routes_agent_and_non_agent_roles(monkeypatch: pytest.MonkeyPatch):
    route = LLMRoute(
        settings=SimpleNamespace(),
        provider="anthropic",
        cli_provider_registration=None,
        use_litellm=False,
    )
    monkeypatch.setattr("core.llm.factory.resolve_llm_route", lambda: route)
    monkeypatch.setattr(
        "core.llm.client_builders.build_agent_client", lambda _route: "AGENT_CLIENT"
    )
    monkeypatch.setattr(
        "core.llm.client_builders.build_reasoning_client",
        lambda _route, model_type: f"LLM:{model_type}",
    )

    assert get_llm(LLMRole.AGENT) == "AGENT_CLIENT"
    assert get_llm(LLMRole.REASONING) == "LLM:reasoning"
    assert get_llm(LLMRole.CLASSIFICATION) == "LLM:classification"
    assert get_llm(LLMRole.TOOLCALL) == "LLM:toolcall"


def test_llm_roles_and_model_types_are_string_enums() -> None:
    """The role-to-tier map keeps typed, round-trippable public values."""
    assert LLMRole("reasoning") is LLMRole.REASONING
    assert ModelType("reasoning") is ModelType.REASONING
    assert _MODEL_TYPE_BY_ROLE[LLMRole.REASONING] is ModelType.REASONING
    assert _MODEL_TYPE_BY_ROLE[LLMRole.CLASSIFICATION] is ModelType.CLASSIFICATION
    assert _MODEL_TYPE_BY_ROLE[LLMRole.TOOLCALL] is ModelType.TOOLCALL


def test_get_llm_caches_per_role_and_invalidates_on_config_change(monkeypatch: pytest.MonkeyPatch):
    cache_key = {"value": ("sdk", "anthropic")}
    monkeypatch.setattr("core.llm.factory.current_llm_client_cache_key", lambda: cache_key["value"])
    monkeypatch.setattr(
        "core.llm.factory.resolve_llm_route",
        lambda: LLMRoute(SimpleNamespace(), "anthropic", None, False),
    )
    monkeypatch.setattr("core.llm.client_builders.build_agent_client", lambda _route: object())
    monkeypatch.setattr(
        "core.llm.client_builders.build_reasoning_client", lambda _route, _mt: object()
    )

    first_agent = get_llm(LLMRole.AGENT)
    assert get_llm(LLMRole.AGENT) is first_agent  # cached per role
    assert get_llm(LLMRole.REASONING) is not first_agent  # distinct role, distinct client

    cache_key["value"] = ("sdk", "openai")  # provider changed -> whole cache invalidates
    assert get_llm(LLMRole.AGENT) is not first_agent


@pytest.mark.parametrize(("registration", "cli_backed"), [(None, False), ("codex-reg", True)])
def test_agent_llm_is_cli_backed_reads_the_route_and_builds_no_client(
    registration: str | None,
    cli_backed: bool,
    monkeypatch: pytest.MonkeyPatch,
):
    """Transport-based policy selection must not pay for a client it may not use."""

    def _fail_get_llm(*_a: object, **_k: object) -> None:
        raise AssertionError("selecting on transport must not construct an LLM client")

    monkeypatch.setattr("core.llm.factory.get_llm", _fail_get_llm)
    monkeypatch.setattr(
        "core.llm.factory.resolve_llm_route",
        lambda: LLMRoute(SimpleNamespace(), "anthropic", registration, False),
    )

    assert agent_llm_is_cli_backed() is cli_backed


def test_reset_llm_clients_forces_rebuild(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "core.llm.factory.current_llm_client_cache_key", lambda: ("sdk", "anthropic")
    )
    monkeypatch.setattr(
        "core.llm.factory.resolve_llm_route",
        lambda: LLMRoute(SimpleNamespace(), "anthropic", None, False),
    )
    monkeypatch.setattr("core.llm.client_builders.build_agent_client", lambda _route: object())
    monkeypatch.setattr(
        "core.llm.client_builders.build_reasoning_client", lambda _route, _mt: object()
    )

    first = get_llm(LLMRole.AGENT)
    reset_llm_clients()

    assert get_llm(LLMRole.AGENT) is not first
