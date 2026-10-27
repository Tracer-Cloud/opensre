"""Connection, selection, billing identity and persistence regression contracts."""

from __future__ import annotations

import io
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from rich.console import Console

from config.llm_auth.model_catalog import cached_model_catalog, save_model_catalog
from config.llm_auth.records import (
    delete_provider_auth_record,
    save_provider_auth_record,
    save_provider_auth_record_values,
)
from surfaces.cli.app import cli
from surfaces.shared.llm_setup import login_flow, model_inventory, model_picker
from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
from surfaces.shared.llm_setup.connection import connect_provider
from surfaces.shared.llm_setup.model_results import ModelChoice
from surfaces.shared.llm_setup.selection import select_provider
from surfaces.shared.llm_setup.subscription_service import configure_subscription

_HOSTED_CHOICE = model_inventory.hosted_choice


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "local")
    monkeypatch.setattr(
        model_inventory,
        "SUPPORTED_PROVIDERS",
        (PROVIDER_BY_VALUE["openai"], PROVIDER_BY_VALUE["anthropic"]),
    )
    monkeypatch.setattr(model_inventory, "hosted_choice", lambda: None)
    return tmp_path / ".env"


@pytest.mark.parametrize(
    "activation,model,switched",
    [
        (None, None, False),
        (False, None, False),
        (None, "gpt-test", False),
        (False, "gpt-test", False),
        (True, None, True),
        (True, "gpt-test", True),
    ],
)
def test_non_tty_auth_flags_connect_without_hidden_input(isolated, activation, model, switched):
    args = [
        "auth",
        "login",
        "openai",
        "--api-key",
        "synthetic-new",
        "--no-validate",
        "--no-open-browser",
    ]
    if activation is not None:
        args.append("--set-provider" if activation else "--no-set-provider")
    if model:
        args.extend(["--model", model])
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert os.environ["LLM_PROVIDER"] == ("openai" if switched else "anthropic")
    assert os.environ["OPENAI_API_KEY"] == "synthetic-new"
    assert "synthetic-new" not in result.output
    assert isolated.exists() is switched


def test_connect_failure_and_choose_later_preserve_active_routing(monkeypatch, isolated):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-old")
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")
    before = {
        k: os.getenv(k)
        for k in (
            "LLM_PROVIDER",
            "ANTHROPIC_REASONING_MODEL",
            "OPENSRE_LLM_CONNECTION",
            "OPENSRE_LLM_TRANSPORT",
        )
    }
    result = connect_provider(
        profile=resolve_auth_profile("openai"),
        api_key="synthetic-new",
        model="gpt-test",
        validate=False,
    )
    picker_calls = []

    def later(_console, **kwargs):
        picker_calls.append(kwargs)
        return None

    assert (
        login_flow.finish_connection(
            Console(file=io.StringIO()),
            connect=lambda: result,
            provider="openai",
            activation=None,
            model="gpt-test",
            interactive=True,
            picker=later,
        )
        is None
    )
    assert picker_calls == [{"provider_id": "openai", "preferred_model": "gpt-test"}]
    assert {k: os.getenv(k) for k in before} == before
    assert not isolated.exists()
    from config.llm_auth.credentials import require_for_request

    assert require_for_request("openai").api_key == "synthetic-new"


def test_subscription_validation_precedes_commit_and_picker_follows(monkeypatch, isolated):
    events = []

    class Auth:
        def login(self, _present, *, select_model, **_kwargs):
            events.append("auth")
            selected = select_model([("account-only", "Account model")])
            events.append("commit")
            save_provider_auth_record(
                provider="chatgpt",
                auth_name="chatgpt",
                kind="subscription",
                source="subscription",
                detail="Synthetic account",
            )
            return selected

    monkeypatch.setattr(
        "surfaces.shared.llm_setup.subscription_service.subscription_adapter",
        lambda _: SimpleNamespace(auth=Auth()),
    )

    def connect():
        return configure_subscription(
            profile=resolve_auth_profile("chatgpt"),
            model=None,
            set_provider=False,
            present=lambda _: None,
        )

    def later(_console, **kwargs):
        events.append("picker")
        assert cached_model_catalog("chatgpt") == (("account-only", "Account model"),)
        return None

    login_flow.finish_connection(
        Console(file=io.StringIO()),
        connect=connect,
        provider="chatgpt",
        activation=None,
        model=None,
        interactive=True,
        picker=later,
    )
    assert events == ["auth", "commit", "picker"]
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert not isolated.exists()
    with pytest.raises(RuntimeError, match="available"):
        configure_subscription(
            profile=resolve_auth_profile("chatgpt"),
            model="not-entitled",
            set_provider=False,
            present=lambda _: None,
            new_account=True,
        )
    assert events[-1] == "auth"
    assert cached_model_catalog("chatgpt") == (("account-only", "Account model"),)


def test_catalog_snapshot_survives_metadata_refresh_and_reconnect_replaces_it(isolated):
    save_model_catalog("chatgpt", [("one", "First model")])
    save_provider_auth_record(
        provider="chatgpt",
        auth_name="chatgpt",
        kind="subscription",
        source="subscription",
        detail="Refreshed",
    )
    save_provider_auth_record_values("chatgpt", {"source": "subscription", "verified": "true"})
    assert cached_model_catalog("chatgpt") == (("one", "First model"),)
    save_model_catalog("chatgpt", [("two", "Second model")])
    assert cached_model_catalog("chatgpt") == (("two", "Second model"),)
    # An ordinary refresh holding an older record cannot overwrite a reconnect.
    save_provider_auth_record_values(
        "chatgpt", {"model_catalog": '[["stale", "Old"]]', "source": "subscription"}
    )
    assert cached_model_catalog("chatgpt") == (("two", "Second model"),)
    delete_provider_auth_record("chatgpt")
    assert cached_model_catalog("chatgpt") == ()


def test_subscription_reuses_the_existing_session_without_browser(monkeypatch, isolated):
    save_provider_auth_record(
        provider="chatgpt",
        auth_name="chatgpt",
        kind="subscription",
        source="subscription",
        detail="Synthetic existing session",
    )
    auth = SimpleNamespace(
        verify=lambda: [("account-model", "Model")],
        login=lambda *_a, **_k: pytest.fail("existing session must be reused"),
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.subscription_service.subscription_adapter",
        lambda _: SimpleNamespace(auth=auth),
    )
    result = configure_subscription(
        profile=resolve_auth_profile("chatgpt"),
        model=None,
        set_provider=False,
        present=lambda _: pytest.fail("no browser"),
        allow_authorization=False,
    )
    assert result.model == "account-model"
    assert os.environ["LLM_PROVIDER"] == "anthropic"


def test_picker_uses_exact_pairs_current_row_and_single_submit(monkeypatch, isolated):
    rows = (
        ModelChoice("openai", "same", "Same [literal] 李", "OpenAI · API billing"),
        ModelChoice("chatgpt", "same", "Same [literal] 李", "ChatGPT · Subscription", True),
    )
    monkeypatch.setattr(model_picker, "model_choices", lambda **_: rows)
    build = model_picker.build_input_form
    seen = []

    def inspect(values, **kwargs):
        assert values["model"] == rows[1].key
        assert kwargs["submit_on_choice"] is True
        fields = kwargs["fields"](values)
        assert fields[0].searchable
        assert [c.value for c in fields[0].choices[:2]] == [rows[0].key, rows[1].key]
        # Form validation is pure: it returns an ID and cannot authenticate/commit.
        assert kwargs["validate"]({"model": rows[0].key}) == rows[0].key
        return build(values, **kwargs)

    monkeypatch.setattr(model_picker, "build_input_form", inspect)
    monkeypatch.setattr(model_picker, "run_command_input", lambda _: rows[0].key)
    monkeypatch.setattr(
        model_picker,
        "select_provider",
        lambda *pair: seen.append(pair) or SimpleNamespace(model="same"),
    )
    output = io.StringIO()
    model_picker.model_picker(Console(file=output))
    assert seen == [("openai", "same")]
    assert "Same [literal] 李" in output.getvalue()


def test_connect_inside_picker_returns_to_updated_choices_when_user_chooses_later(
    monkeypatch, isolated
):
    row = ModelChoice("openai", "gpt-test", "Test model", "OpenAI · API billing")
    calls = []
    monkeypatch.setattr(
        model_picker, "model_choices", lambda **_: calls.append("inventory") or (row,)
    )
    answers = iter(["__connect__", row.key])
    monkeypatch.setattr(model_picker, "run_command_input", lambda _: next(answers))
    monkeypatch.setattr(
        model_picker, "select_provider", lambda *_: SimpleNamespace(model="gpt-test")
    )
    model_picker.model_picker(
        Console(file=io.StringIO()), connect=lambda _provider: calls.append("login") or None
    )
    assert calls == ["inventory", "login", "inventory"]


def test_cli_unknown_auth_and_definite_signout_stay_distinct(monkeypatch, isolated):
    provider = PROVIDER_BY_VALUE["codex"]
    monkeypatch.setattr(model_inventory, "SUPPORTED_PROVIDERS", (provider,))
    monkeypatch.setattr(
        model_inventory,
        "resolve_cli_auth_state",
        lambda _: SimpleNamespace(installed=True, logged_in=None),
    )
    rows = model_inventory.model_choices()
    assert rows and all(r.availability == "auth-on-use" for r in rows)
    assert any(r.model_id == "" for r in rows)
    monkeypatch.setattr(
        model_inventory,
        "resolve_cli_auth_state",
        lambda _: SimpleNamespace(installed=True, logged_in=False),
    )
    assert model_inventory.model_choices() == ()


def test_inactive_endpoint_and_custom_model_survive_switches(monkeypatch, isolated):
    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "synthetic-custom")
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai")
    select_provider("custom-openai", "private-model")
    select_provider("openai", "gpt-test")
    select_provider("custom-openai")
    content = isolated.read_text()
    assert "CUSTOM_OPENAI_BASE_URL=https://example.invalid/v1" in content
    assert "OPENAI_REASONING_MODEL=gpt-test" in content
    assert os.environ["CUSTOM_OPENAI_REASONING_MODEL"] == "private-model"


def test_json_list_show_are_read_only_and_contain_exact_identity(monkeypatch, isolated):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai")
    runner = CliRunner()
    for args in [["--json", "model", "list"], ["--json", "model", "show"]]:
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        rows = payload if isinstance(payload, list) else [payload]
        assert all(
            {"provider_id", "model_id", "source", "active", "availability"} <= row.keys()
            for row in rows
        )
        assert "synthetic-openai" not in result.output
    assert not isolated.exists()


def test_non_tty_missing_input_fails_without_prompt_or_browser(monkeypatch, isolated):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("click.prompt", lambda *_a, **_k: pytest.fail("must not read stdin"))
    result = CliRunner().invoke(cli, ["auth", "login", "openai"])
    assert result.exit_code != 0 and "--api-key" in result.output
    assert not isolated.exists()


def test_hosted_choice_survives_local_selection_and_can_be_selected_again(monkeypatch, isolated):
    from config.account import (
        AccountRecord,
        account_llm_route,
        load_account_record,
        save_account_record,
    )
    from config.constants.llm import HOSTED_MODEL_SOURCE_ID as HOSTED_SOURCE
    from surfaces.shared.llm_setup.selection import select_hosted_model

    monkeypatch.setattr(model_inventory, "hosted_choice", _HOSTED_CHOICE)
    hosted_choice = _HOSTED_CHOICE
    monkeypatch.setenv("OPENSRE_ACCOUNT_METADATA_PATH", str(isolated.parent / "account.json"))
    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "synthetic-account-token")
    record = AccountRecord(
        "synthetic-user",
        None,
        None,
        "https://example.invalid",
        "2026-10-27T00:00:00Z",
        "2026-11-27T00:00:00Z",
        llm_model="hosted-model",
    )
    save_account_record(record)
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "hosted")
    before = hosted_choice()
    assert before is not None and before.active
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key")
    select_provider("openai", "api-model")
    assert account_llm_route() is None
    after = hosted_choice()
    assert after is not None and after.provider_id == HOSTED_SOURCE and not after.active
    assert load_account_record() == record
    select_hosted_model(after.model_id)
    assert account_llm_route().model == "hosted-model"
    assert load_account_record() == record
    assert os.environ["OPENAI_REASONING_MODEL"] == "api-model"


def test_child_connect_only_refreshes_replaced_key_without_importing_saved_selection(
    monkeypatch, isolated
):
    from config.llm_auth.credentials import require_for_request, save_api_key
    from surfaces.shared.llm_setup.handoff import refresh_setup_result

    isolated.write_text(
        "LLM_PROVIDER=openai\nOPENAI_REASONING_MODEL=old-saved-selection\nOPENSRE_LLM_CONNECTION=local\nUNRELATED_SETTING=saved\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-shadowing-export")
    monkeypatch.setenv("UNRELATED_SETTING", "exported")
    save_api_key("openai", "synthetic-replacement")
    receipt = isolated.parent / "receipt.json"
    receipt.write_text(
        json.dumps({"provider": "openai", "activated": False, "credential_replaced": True})
    )
    refresh_setup_result(receipt)
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert os.environ["UNRELATED_SETTING"] == "exported"
    assert require_for_request("openai").api_key == "synthetic-replacement"
    receipt.write_text(
        json.dumps({"provider": "openai", "activated": True, "credential_replaced": True})
    )
    refresh_setup_result(receipt)
    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["OPENAI_REASONING_MODEL"] == "old-saved-selection"


@pytest.mark.timeout(5)
@pytest.mark.parametrize("key,commits", [("\r", 1), ("\x1b", 0), ("\x03", 0)])
def test_real_form_one_choice_or_escape_has_no_review_step(monkeypatch, isolated, key, commits):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    row = ModelChoice(
        "openai", "exact-model", "Long model [literal] 李", "OpenAI · API billing", True
    )
    monkeypatch.setattr(model_picker, "model_choices", lambda **_: (row,))
    selected = []
    monkeypatch.setattr(
        model_picker,
        "select_provider",
        lambda *pair: selected.append(pair) or SimpleNamespace(model="exact-model"),
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        monkeypatch.setattr(
            model_picker,
            "run_command_input",
            lambda app: app.run(pre_run=lambda: pipe.send_text(key)),
        )
        model_picker.model_picker(Console(file=io.StringIO()))
    assert len(selected) == commits


@pytest.mark.parametrize(
    "activation,model,picker_count,switched",
    [
        (None, None, 1, False),
        (False, None, 0, False),
        (False, "gpt-explicit", 0, False),
        (True, None, 1, False),
        (True, "gpt-explicit", 0, True),
    ],
)
def test_interactive_activation_flags_offer_picker_without_implicit_selection(
    monkeypatch, isolated, activation, model, picker_count, switched
):
    calls = []
    monkeypatch.setattr(
        login_flow, "model_picker", lambda *_a, **_k: calls.append("picker") or None
    )
    login_flow.login_flow(
        Console(file=io.StringIO()),
        profile=resolve_auth_profile("openai"),
        api_key="synthetic-new",
        model=model,
        activation=activation,
        validate=False,
        open_browser=False,
        interactive=True,
    )
    assert len(calls) == picker_count
    assert os.environ["LLM_PROVIDER"] == ("openai" if switched else "anthropic")


def test_existing_env_key_is_replaced_without_importing_saved_model(isolated, monkeypatch):
    isolated.write_text(
        "LLM_PROVIDER=anthropic\nOPENAI_API_KEY=synthetic-old\nOPENAI_REASONING_MODEL=gpt-saved\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-export")
    connect_provider(
        profile=resolve_auth_profile("openai"), api_key="synthetic-new", validate=False
    )
    assert "OPENAI_API_KEY=synthetic-new" in isolated.read_text()
    assert "OPENAI_REASONING_MODEL=gpt-saved" in isolated.read_text()
    assert os.environ["LLM_PROVIDER"] == "anthropic"


def test_inventory_isolates_broken_provider_and_keeps_catalog_order(monkeypatch, isolated):
    from surfaces.shared.llm_setup.catalog import ModelOption

    providers = (
        replace(
            PROVIDER_BY_VALUE["openai"],
            models=(ModelOption("same", "Shared model"), ModelOption("second", "Second model")),
        ),
        replace(PROVIDER_BY_VALUE["anthropic"], models=(ModelOption("same", "Shared model"),)),
    )
    monkeypatch.setattr(model_inventory, "SUPPORTED_PROVIDERS", providers)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-anthropic")
    monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "same")
    rows = model_inventory.model_choices()
    assert [(r.provider_id, r.model_id) for r in rows] == [
        ("anthropic", "same"),
        ("openai", "same"),
        ("openai", "second"),
    ]
    original = model_inventory.credential_status

    def status(provider):
        if provider == "anthropic":
            raise OSError("Synthetic unavailable metadata")
        return original(provider)

    monkeypatch.setattr(model_inventory, "credential_status", status)
    rows = model_inventory.model_choices()
    assert rows[0].availability == "reconnect"
    assert all(r.availability == "available" for r in rows if r.provider_id == "openai")
