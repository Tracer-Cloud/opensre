"""Factory setup establishes the webapp account that owns shell access."""

from __future__ import annotations

import io

from rich.console import Console

from config.account import AccountRecord
from surfaces.cli.account_auth import AccountAuthError, AccountLoginResult
from surfaces.cli.wizard import factory_setup, summaries
from surfaces.shared.account_session import AccountSessionState, AccountStatus
from surfaces.shared.terminal.banner import banner as banner_module
from surfaces.shared.terminal.banner.banner_state import LaunchStatus


def _signed_out() -> AccountStatus:
    return AccountStatus(AccountSessionState.SIGNED_OUT, None, "signed out")


def _record() -> AccountRecord:
    return AccountRecord(
        user_id="user_123",
        organization_id="org_123",
        github_username="octocat",
        email="octocat@example.com",
        app_url="https://app.opensre.com",
        signed_in_at="2026-09-01T10:00:00+00:00",
        token_expires_at="2026-12-01T10:00:00+00:00",
        llm_model="gpt-5.4-mini",
    )


def test_factory_setup_uses_the_shell_banner_with_concise_steps(monkeypatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr(
        banner_module,
        "load_launch_status",
        lambda: LaunchStatus(skill_count=4, integration_count=1),
    )
    monkeypatch.setattr(
        summaries,
        "console",
        Console(file=output, force_terminal=False, highlight=False, width=100),
    )

    summaries.render_factory_setup_header()

    rendered = output.getvalue()
    assert "Welcome to OpenSRE CLI" in rendered
    assert "Skills (4)" in rendered
    assert "Setup · 2 steps" in rendered
    assert "Sign in with GitHub in the OpenSRE webapp" in rendered
    assert "Start the shell with your hosted model" in rendered


def test_run_factory_setup_requires_account_then_returns_success(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(factory_setup, "render_factory_setup_header", lambda: None)

    def _account(*, step: int, total_steps: int) -> bool:
        calls.append((step, total_steps))
        return True

    monkeypatch.setattr(factory_setup, "_run_account_signup_step", _account)

    assert factory_setup.run_factory_setup() == 0
    assert calls == [(1, factory_setup.FACTORY_SETUP_TOTAL_STEPS)]


def test_run_factory_setup_stops_when_user_stays_signed_out(monkeypatch) -> None:
    monkeypatch.setattr(factory_setup, "render_factory_setup_header", lambda: None)
    monkeypatch.setattr(factory_setup, "_run_account_signup_step", lambda **_kwargs: False)

    assert factory_setup.run_factory_setup() == 1


def test_account_step_keeps_an_active_hosted_session(monkeypatch) -> None:
    monkeypatch.setattr(
        factory_setup,
        "account_status",
        lambda: AccountStatus(AccountSessionState.ACTIVE, _record(), "ok"),
    )
    calls: list[bool] = []
    monkeypatch.setattr(
        factory_setup,
        "login_account",
        lambda **_kwargs: calls.append(True),
    )

    assert factory_setup._run_account_signup_step(step=1, total_steps=2) is True
    assert calls == []


def test_account_step_retries_webapp_login_then_succeeds(monkeypatch) -> None:
    result = AccountLoginResult(_record())
    attempts = 0
    presented: list[object] = []
    monkeypatch.setattr(factory_setup, "account_status", _signed_out)
    monkeypatch.setattr(factory_setup, "choose", lambda *_args, **_kwargs: "retry")
    monkeypatch.setattr(
        factory_setup.AccountLoginPresenter,
        "success",
        lambda _self, value: presented.append(value),
    )

    def _login(**_kwargs: object) -> AccountLoginResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AccountAuthError("not approved")
        return result

    monkeypatch.setattr(factory_setup, "login_account", _login)

    assert factory_setup._run_account_signup_step(step=1, total_steps=2) is True
    assert attempts == 2
    assert presented == [result]


def test_account_step_can_stay_signed_out_after_failure(monkeypatch) -> None:
    monkeypatch.setattr(factory_setup, "account_status", _signed_out)
    monkeypatch.setattr(factory_setup, "choose", lambda *_args, **_kwargs: "cancel")

    def _denied(**_kwargs: object) -> AccountLoginResult:
        raise AccountAuthError("denied")

    monkeypatch.setattr(factory_setup, "login_account", _denied)

    assert factory_setup._run_account_signup_step(step=1, total_steps=2) is False
