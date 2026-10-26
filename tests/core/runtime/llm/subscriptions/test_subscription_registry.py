"""Subscription orchestration preserves explicit connection selection."""

from pathlib import Path

import pytest

from core.llm.subscriptions import chatgpt
from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile
from surfaces.shared.llm_setup.auth_service import logout_provider, verify_provider
from surfaces.shared.llm_setup.subscription_service import configure_subscription


def test_chatgpt_setup_verify_logout_characterization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pin selected account/model forwarding and metadata before the refactor."""
    seen: list[tuple[str | None, bool]] = []

    def login(present, *, select_model, profile_id=None, new_account=False):
        seen.append((profile_id, new_account))
        return select_model([("account-model", "Account model")])

    monkeypatch.setattr(chatgpt, "login", login)
    monkeypatch.setattr(chatgpt, "verify", lambda: [("account-model", "Account model")])
    monkeypatch.setattr(chatgpt, "logout", lambda: "selected session removed")
    result = configure_subscription(
        profile=resolve_auth_profile("chatgpt"),
        model="account-model",
        set_provider=False,
        present=lambda _url: None,
        choose_model=lambda models: models[0][0],
        profile_id="selected-account",
        new_account=True,
    )
    assert (result.provider, result.model, result.source, result.env_path) == (
        "chatgpt",
        "account-model",
        "subscription",
        None,
    )
    assert seen == [("selected-account", True)]
    status = verify_provider("chatgpt")
    assert status.provider == "chatgpt" and status.verified
    assert logout_provider("chatgpt") == "selected session removed"


@pytest.mark.parametrize("method", ["oauth", "api_key"])
def test_second_adapter_dispatches_setup_runtime_and_logout(monkeypatch, tmp_path, method):
    """A second test adapter needs no new branch in shared orchestration."""
    from dataclasses import replace

    from click.testing import CliRunner

    from config.llm_auth.records import resolve_provider_auth_record
    from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
    from core.llm.factory import LLMRole, get_llm, reset_llm_clients
    from core.llm.subscriptions import registry
    from core.llm.subscriptions.types import SubscriptionAuth
    from surfaces.cli.app import cli

    seen = []
    registration = registry.SUBSCRIPTION_ADAPTERS["grok"]

    class TestAdapter:
        def login(
            self, _present, *, select_model, profile_id=None, new_account=False, credential=None
        ):
            seen.append((profile_id, new_account, credential))
            assert (credential == "subscription-key") == (method == "api_key")
            return select_model(self.models())

        def resolve(self):
            return SubscriptionAuth("subscription-key", "https://example.invalid")

        def models(self):
            return [("test-model", "Test model")]

        def verify(self):
            return self.models()

        def logout(self):
            return "test subscription removed"

    class TestClients:
        def agent_client(self, model):
            assert model == "test-model"
            return registration.clients.agent_client(model)

        def llm_client(self, model, *, usage_callback=None):
            assert model == "test-model"
            return registration.clients.llm_client(model, usage_callback=usage_callback)

    monkeypatch.setitem(
        registry.SUBSCRIPTION_ADAPTERS,
        "grok",
        registry.SubscriptionRegistration(TestAdapter(), TestClients()),
    )
    monkeypatch.setitem(
        SUBSCRIPTION_BY_VALUE, "grok", replace(SUBSCRIPTION_BY_VALUE["grok"], auth_method=method)
    )
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(tmp_path / ".env"))
    args = ["auth", "login", "grok", "--no-open-browser", "--profile", "test-account"]
    if method == "api_key":
        args += ["--api-key", "subscription-key"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert seen == [("test-account", False, "subscription-key" if method == "api_key" else None)]
    env = (tmp_path / ".env").read_text()
    assert "OPENSRE_LLM_CONNECTION=local" in env and "GROK_MODEL=test-model" in env
    assert "subscription-key" not in env
    assert verify_provider("grok").provider == "grok"
    assert resolve_provider_auth_record("grok")["verified"] == "true"
    assert logout_provider("grok") == "test subscription removed"
    reset_llm_clients()
    try:
        assert get_llm(LLMRole.AGENT).model_id == "test-model"
        assert all(get_llm(role) is not None for role in LLMRole)
    finally:
        reset_llm_clients()


def test_catalog_only_subscription_cannot_build_an_inference_client(monkeypatch):
    from core.llm.subscriptions import registry
    from core.llm.transports.litellm.routing import build_litellm_agent_client

    monkeypatch.delitem(registry.SUBSCRIPTION_ADAPTERS, "grok")
    with pytest.raises(ValueError, match="No subscription adapter"):
        build_litellm_agent_client(object(), "grok")
