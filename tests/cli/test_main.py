from __future__ import annotations

import importlib
import importlib.machinery
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from config.constants.product import RELEASE_STAGE
from config.repl_config import ReplConfig
from infrastructure.analytics import provider
from infrastructure.analytics.events import Event
from surfaces.cli.app import cli
from surfaces.cli.startup import sentry_entrypoint_for
from surfaces.entrypoint import main


def _fake_sentry_sdk(*, flush: object) -> types.ModuleType:
    """A ``sys.modules`` double that ``find_spec`` can tolerate."""
    mod = types.ModuleType("sentry_sdk")
    mod.__spec__ = importlib.machinery.ModuleSpec("sentry_sdk", loader=None)
    mod.flush = flush  # type: ignore[attr-defined]
    return mod


def _stub_analytics_httpx(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    posted_payloads: list[dict[str, object]] = []

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict[str, str],
        ) -> _StubResponse:
            posted_payloads.append({"url": url, "json": json.loads(content), "headers": headers})
            return _StubResponse()

    monkeypatch.setattr(provider.httpx, "Client", _StubClient)
    return posted_payloads


def test_main_runs_health_command(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)

    with (
        patch("integrations.verify.verify_integrations") as mock_verify,
        patch("integrations.verify.format_verification_results") as mock_format,
    ):
        mock_verify.return_value = [
            {
                "service": "aws",
                "source": "local store",
                "status": "passed",
                "detail": "ok",
            }
        ]
        mock_format.return_value = (
            "\n"
            "  SERVICE    SOURCE       STATUS      DETAIL\n"
            "  aws        local store  passed      ok\n"
        )

        exit_code = main(["health"])

    assert exit_code == 0


def test_main_does_not_capture_expected_usage_errors_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured.append(exc),
    )

    exit_code = main(["integrations", "show", "nonexistent"])

    assert exit_code != 0
    assert captured == []


def test_main_treats_onboard_abort_as_clean_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr(
        "surfaces.cli.wizard.flow.run_wizard",
        lambda: (_ for _ in ()).throw(click.Abort()),
    )

    exit_code = main(["onboard"])

    assert exit_code == 0


def test_main_allows_update_when_sentry_sdk_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)

    def _raise_missing_sentry(**_kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'sentry_sdk'", name="sentry_sdk")

    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", _raise_missing_sentry
    )
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "9999.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.is_update_available", lambda _c, _l: False)

    exit_code = main(["update", "--check"])

    assert exit_code == 0
    assert "already up to date" in capsys.readouterr().out


def test_main_non_update_still_raises_when_sentry_sdk_missing(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)

    def _raise_missing_sentry(**_kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'sentry_sdk'", name="sentry_sdk")

    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", _raise_missing_sentry
    )

    with pytest.raises(ModuleNotFoundError):
        main(["health"])


def test_main_does_not_capture_analytics_for_help(monkeypatch, capsys) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "surfaces.cli.app.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "surfaces.cli.app.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)

    exit_code = main(["--help"])

    assert exit_code == 0
    assert "Usage:" in capsys.readouterr().out
    assert captured == []


def test_main_does_not_capture_unknown_command_to_sentry(monkeypatch, capsys) -> None:
    captured: list[str] = []
    captured_errors: list[BaseException] = []
    monkeypatch.setattr(
        "surfaces.cli.app.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "surfaces.cli.app.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    exit_code = main(["not-a-command"])

    assert exit_code != 0
    assert "No such command" in capsys.readouterr().err
    assert captured == []
    assert captured_errors == []


def test_main_does_not_capture_invalid_option_parse_error(monkeypatch, capsys) -> None:
    captured: list[str] = []
    captured_errors: list[BaseException] = []
    monkeypatch.setattr(
        "surfaces.cli.app.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "surfaces.cli.app.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    exit_code = main(["--definitely-wrong-option"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "--definitely-wrong-option" in stderr and "No such option" in stderr
    assert captured == []
    assert captured_errors == []


def test_main_captures_analytics_once_for_accepted_command(monkeypatch, capsys) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "surfaces.cli.app.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "surfaces.cli.app.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("integrations.verify.verify_integrations", lambda: [])

    exit_code = main(["health"])

    assert exit_code == 0
    assert "OpenSRE Health" in capsys.readouterr().out
    assert captured == ["install", "cli"]


def test_main_fast_version_command_skips_first_run_setup(monkeypatch, capsys) -> None:
    captured: list[dict[str, object] | None] = []
    monkeypatch.setattr(
        "surfaces.cli.app.capture_first_run_if_needed",
        lambda: captured.append({"unexpected": "install"}),
    )
    monkeypatch.setattr(
        "surfaces.cli.app.capture_cli_invoked",
        lambda properties=None: captured.append(properties),
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)

    exit_code = main(["version"])

    assert exit_code == 0
    assert "opensre" in capsys.readouterr().out
    assert captured == []


def test_main_debug_sentry_sends_synthetic_event(monkeypatch, capsys) -> None:
    debug_module = importlib.import_module("surfaces.cli.commands.debug")
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []
    root_init_entrypoints: list[str | None] = []
    flush_calls: list[int] = []

    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda entrypoint=None: root_init_entrypoints.append(entrypoint),
    )
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(debug_module, "sentry_transport_enabled", lambda: True)
    monkeypatch.setattr(debug_module, "resolved_sentry_dsn_host", lambda: "sentry.example.test")

    def capture_stub(*args: object, **kwargs: object) -> str:
        captured.append((args, kwargs))
        return "event-123"

    monkeypatch.setattr(debug_module, "capture_exception", capture_stub)
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        _fake_sentry_sdk(flush=lambda timeout: flush_calls.append(timeout)),
    )

    exit_code = main(["debug", "sentry"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Sentry DSN host: sentry.example.test" in output
    assert "Sentry event ID: event-123" in output
    assert "Sentry flush sent: yes" in output
    assert root_init_entrypoints == ["debug"]
    assert flush_calls == [5]
    assert captured[0][1]["context"] == "debug.sentry"
    assert captured[0][1]["tags"] == {"debug": "true", "surface": "debug"}


def test_sentry_entrypoint_uses_debug_for_debug_group_invocations() -> None:
    assert sentry_entrypoint_for(cli, ["debug", "future-check"]) == "debug"


def test_main_debug_sentry_exits_nonzero_when_disabled(monkeypatch, capsys) -> None:
    debug_module = importlib.import_module("surfaces.cli.commands.debug")
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(debug_module, "sentry_transport_enabled", lambda: False)
    monkeypatch.setattr(debug_module, "resolved_sentry_dsn_host", lambda: "")

    exit_code = main(["debug", "sentry"])

    assert exit_code == 1
    assert "Sentry is disabled or no DSN is configured." in capsys.readouterr().err


def test_main_debug_sentry_exits_nonzero_when_flush_fails(monkeypatch, capsys) -> None:
    debug_module = importlib.import_module("surfaces.cli.commands.debug")
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(debug_module, "sentry_transport_enabled", lambda: True)
    monkeypatch.setattr(debug_module, "resolved_sentry_dsn_host", lambda: "sentry.example.test")
    monkeypatch.setattr(debug_module, "capture_exception", lambda *_args, **_kw: "event-123")

    def flush_stub(*, timeout: int) -> bool:
        assert timeout == 5
        return False

    monkeypatch.setitem(sys.modules, "sentry_sdk", _fake_sentry_sdk(flush=flush_stub))

    exit_code = main(["debug", "sentry"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Sentry event ID: event-123" in output
    assert "Sentry flush sent: no" in output


def test_main_emits_first_run_install_before_cli_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    # This test validates analytics event ordering only; avoid real Sentry init
    # side effects (e.g. sdk integration hooks) that are unrelated to the
    # install/cli-invoked event contract.
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    provider.shutdown_analytics(flush=False)
    provider._instance = None
    provider._cached_anonymous_id = None
    provider._cached_identity_persistence = "unknown"
    provider._first_run_marker_created_this_process = False
    provider._pending_user_id_load_failures.clear()
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    posted_payloads = _stub_analytics_httpx(monkeypatch)

    monkeypatch.setattr("surfaces.cli.app.render_landing", lambda _group: None)

    exit_code = main(["--no-interactive"])

    assert exit_code == 0
    assert RELEASE_STAGE in capsys.readouterr().err
    # CLI exit is non-blocking; wait for the daemon worker to finish posts.
    analytics = provider._instance
    if analytics is not None and analytics._worker is not None:
        analytics._worker.join(timeout=2.0)
    assert [payload["json"]["event"] for payload in posted_payloads] == [
        Event.INSTALL_DETECTED.value,
        Event.CLI_INVOKED.value,
    ]
    provider.shutdown_analytics(flush=False)
    provider._instance = None


@pytest.mark.parametrize(
    ("argv", "subcommand_event", "setup"),
    [
        (
            ["onboard"],
            "onboard_started",
            "surfaces.cli.wizard.flow.run_wizard",
        ),
        (
            ["integrations", "list"],
            "integrations_listed",
            "integrations.cli.cmd_list",
        ),
    ],
)
def test_main_captures_cli_invoked_before_reported_subcommand_families(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    subcommand_event: str,
    setup: str,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "surfaces.cli.app.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "surfaces.cli.app.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)

    if setup == "surfaces.cli.wizard.flow.run_wizard":
        onboard_module = importlib.import_module("surfaces.cli.commands.onboard")
        monkeypatch.setattr(setup, lambda: 0)
        monkeypatch.setattr(
            onboard_module,
            "capture_onboard_started",
            lambda: captured.append(subcommand_event),
        )
        monkeypatch.setattr(onboard_module, "capture_onboard_completed", lambda _cfg: None)
    else:
        integrations_module = importlib.import_module("surfaces.cli.commands.integrations")
        monkeypatch.setattr(setup, lambda: None)
        monkeypatch.setattr(
            integrations_module,
            "capture_integrations_listed",
            lambda: captured.append(subcommand_event),
        )

    exit_code = main(argv)

    assert exit_code == 0
    assert captured[:3] == ["install", "cli", subcommand_event]


def test_no_interactive_falls_through_to_landing_page(monkeypatch) -> None:
    """Regression for Greptile P1 (PR #591): --no-interactive previously ran
    `raise SystemExit(run_repl(...))` unconditionally on a TTY, returning 0 but
    never reaching render_landing().  The fix guards the SystemExit on
    `config.enabled`, so disabled mode falls through to render_landing().
    """
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)

    # Force the TTY branch so the regression path is actually exercised.
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)

    # Force disabled interactive config via the loader.  Return a disabled config
    # regardless of how the CLI resolved the flag.
    monkeypatch.setattr(
        "config.repl_config.ReplConfig.load",
        classmethod(lambda _cls, **_kw: ReplConfig(enabled=False, layout="classic")),
    )

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "surfaces.cli.app.render_landing",
        lambda _group: landing_calls.append(1),
    )

    # run_repl must NOT be invoked when config.enabled is False.
    def _fail_if_called(**_kw: object) -> int:
        raise AssertionError("run_repl must not run when config.enabled=False")

    with patch("surfaces.interactive_shell.run_repl", side_effect=_fail_if_called):
        exit_code = main(["--no-interactive"])

    assert exit_code == 0
    assert landing_calls == [1], "render_landing should be called exactly once"


def test_landing_page_runs_the_launch_work_the_shell_would_have_run(monkeypatch) -> None:
    """With no shell to paint a banner, the deferred error-reporting start still runs."""
    # Arrange: a bare launch whose startup hands back deferred work, on a TTY
    # with the shell disabled so the landing page is served instead.
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(
        "config.repl_config.ReplConfig.load",
        classmethod(lambda _cls, **_kw: ReplConfig(enabled=False, layout="classic")),
    )
    order: list[str] = []

    def _start_error_reporting() -> None:
        order.append("start")

    def _hand_back_error_reporting_start(_group: object, _argv: object) -> object:
        return _start_error_reporting

    monkeypatch.setattr("surfaces.cli.app.startup.run", _hand_back_error_reporting_start)
    monkeypatch.setattr("surfaces.cli.app.render_landing", lambda _group: order.append("landing"))

    # Act
    exit_code = main(["--no-interactive"])

    # Assert: the deferred start ran once, before the page printed.
    assert exit_code == 0
    assert order == ["start", "landing"]


def test_default_no_args_enters_repl(monkeypatch) -> None:
    """Regression: the default invocation `opensre` (no args, TTY) must enter
    the REPL.  A previous Click misconfiguration (is_flag + flag_value=False)
    made the `interactive` kwarg resolve to False even with no flag, so every
    local run silently rendered the landing page.  With no flag the CLI now
    passes cli_enabled=None (deferring to env/config, which default to enabled),
    so run_repl must still be called.
    """
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)

    load_calls: list[dict] = []
    orig_load = ReplConfig.load

    @classmethod  # type: ignore[misc]
    def spy_load(cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return orig_load(**kw)

    monkeypatch.setattr("config.repl_config.ReplConfig.load", spy_load)

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "surfaces.cli.app.render_landing",
        lambda _group: landing_calls.append(1),
    )

    with (
        patch("surfaces.interactive_shell.run_repl", return_value=0),
        patch("surfaces.interactive_shell.main.run_repl", return_value=0),
    ):
        exit_code = main([])

    assert exit_code == 0
    assert len(load_calls) >= 1
    repl_load = load_calls[-1]
    assert repl_load.get("cli_enabled") is None, (
        f"default no-args run must defer to env/config (cli_enabled=None), got {repl_load}"
    )
    assert repl_load.get("cli_layout") is None
    assert repl_load.get("cli_theme") is None, (
        f"default no-args run must leave theme env/config overridable, got {repl_load}"
    )
    assert landing_calls == [], "REPL should run, not landing page"


def test_env_disables_interactive_without_flag(monkeypatch) -> None:
    """Regression for #4376: with no --interactive/--no-interactive flag,
    OPENSRE_INTERACTIVE (and config.yml) must be honored. The Click default
    previously forced cli_enabled=True, so ``OPENSRE_INTERACTIVE=0 opensre``
    still entered the REPL. The CLI now passes cli_enabled=None when no flag is
    given, so the env var disables the shell and the landing page renders.
    """
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)

    # Hermetic: ignore any real ~/.opensre/config.yml and drive purely via the env var.
    monkeypatch.setattr("config.repl_config._read_config_file", lambda: {})
    monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")

    load_calls: list[dict] = []
    orig_load = ReplConfig.load

    @classmethod  # type: ignore[misc]
    def spy_load(cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return orig_load(**kw)

    monkeypatch.setattr("config.repl_config.ReplConfig.load", spy_load)

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "surfaces.cli.app.render_landing",
        lambda _group: landing_calls.append(1),
    )

    def _fail_if_called(**_kw: object) -> int:
        raise AssertionError("run_repl must not run when OPENSRE_INTERACTIVE=0 and no flag")

    with patch("surfaces.interactive_shell.run_repl", side_effect=_fail_if_called):
        exit_code = main([])

    assert exit_code == 0
    assert load_calls[-1].get("cli_enabled") is None, (
        f"no flag must defer to env/config (cli_enabled=None), got {load_calls[-1]}"
    )
    assert landing_calls == [1], "landing page should render when the env var disables interactive"


def test_resume_flag_enters_repl_with_session_id(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)

    load_calls: list[dict] = []
    orig_load = ReplConfig.load

    @classmethod  # type: ignore[misc]
    def spy_load(cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return orig_load(**kw)

    monkeypatch.setattr("config.repl_config.ReplConfig.load", spy_load)

    run_repl_calls: list[dict[str, object]] = []

    def _capture_run_repl(**kwargs: object) -> int:
        run_repl_calls.append(kwargs)
        return 0

    with patch("surfaces.interactive_shell.run_repl", side_effect=_capture_run_repl):
        exit_code = main(["--resume", "8988e743"])

    assert exit_code == 0
    assert load_calls[-1]["cli_enabled"] is True
    assert len(run_repl_calls) == 1
    assert run_repl_calls[0]["resume_session_id"] == "8988e743"
    assert run_repl_calls[0]["config"].enabled is True


def test_invalid_theme_flag_returns_usage_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)

    exit_code = main(["--theme", "chartreuse"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "Invalid value for '--theme'" in err
    assert "chartreuse" in err


def test_valid_theme_flag_passes_normalized_value(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)

    load_calls: list[dict] = []

    @classmethod  # type: ignore[misc]
    def spy_load(_cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return ReplConfig(enabled=True, layout="classic", theme="blue")

    monkeypatch.setattr("config.repl_config.ReplConfig.load", spy_load)

    with (
        patch("surfaces.interactive_shell.run_repl", return_value=0),
        patch("surfaces.interactive_shell.main.run_repl", return_value=0),
    ):
        exit_code = main(["--theme", "BLUE"])

    assert exit_code == 0
    assert len(load_calls) >= 1
    assert all(call.get("cli_theme") == "blue" for call in load_calls)


def test_main_flushes_analytics_when_events_are_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-shot CLI exit must drain queued events (e.g. investigation_completed)."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr("surfaces.cli.app.analytics_needs_flush", lambda: True)
    monkeypatch.setattr(
        "surfaces.cli.app.shutdown_analytics",
        lambda **kwargs: calls.append(kwargs),
    )

    exit_code = main(["integrations", "show", "nonexistent"])

    assert exit_code != 0
    assert calls and calls[0]["flush"] is True
    assert calls[0]["timeout"] > 0


def test_main_does_not_block_when_no_events_are_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr("surfaces.cli.app.analytics_needs_flush", lambda: False)
    monkeypatch.setattr(
        "surfaces.cli.app.shutdown_analytics",
        lambda **kwargs: calls.append(kwargs),
    )

    exit_code = main(["integrations", "show", "nonexistent"])

    assert exit_code != 0
    assert calls == [{"flush": False}]


def test_root_main_propagates_the_cli_exit_code(monkeypatch) -> None:
    """``python main.py`` must exit with the CLI's status, not always 0.

    main.py is the documented entry point, so a swallowed exit code silently
    breaks CI steps, shell `&&` chains, and anything that checks $?.
    """
    # Arrange: repo-root main.py is not an installed module, so import it from
    # the checkout explicitly rather than relying on sys.path containing cwd.
    from config.constants.paths import REPO_ROOT

    monkeypatch.syspath_prepend(str(REPO_ROOT))
    import main as root_main

    monkeypatch.setattr("surfaces.cli.app.main", lambda *_a, **_k: 2)

    # Act
    status = root_main.main()

    # Assert
    assert status == 2
