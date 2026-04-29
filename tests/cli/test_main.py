from __future__ import annotations

from unittest.mock import patch

from app.cli.__main__ import main
from app.cli.repl.config import ReplConfig


def test_main_runs_health_command(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda: None)

    with (
        patch("app.integrations.verify.verify_integrations") as mock_verify,
        patch("app.integrations.verify.format_verification_results") as mock_format,
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


def test_no_interactive_falls_through_to_landing_page(monkeypatch) -> None:
    """Regression for Greptile P1 (PR #591): --no-interactive previously ran
    `raise SystemExit(run_repl(...))` unconditionally on a TTY, returning 0 but
    never reaching render_landing().  The fix guards the SystemExit on
    `config.enabled`, so disabled mode falls through to render_landing().
    """
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda: None)

    # Force the TTY branch so the regression path is actually exercised.
    monkeypatch.setattr("app.cli.__main__.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.cli.__main__.sys.stdout.isatty", lambda: True)

    # Force disabled interactive config via the loader.  Return a disabled config
    # regardless of how the CLI resolved the flag.
    monkeypatch.setattr(
        "app.cli.repl.config.ReplConfig.load",
        classmethod(lambda _cls, **_kw: ReplConfig(enabled=False, layout="classic")),
    )

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "app.cli.__main__.render_landing",
        lambda: landing_calls.append(1),
    )

    # run_repl must NOT be invoked when config.enabled is False.
    def _fail_if_called(**_kw: object) -> int:
        raise AssertionError("run_repl must not run when config.enabled=False")

    with patch("app.cli.repl.run_repl", side_effect=_fail_if_called):
        exit_code = main(["--no-interactive"])

    assert exit_code == 0
    assert landing_calls == [1], "render_landing should be called exactly once"


def test_default_no_args_enters_repl(monkeypatch) -> None:
    """Regression: the default invocation `opensre` (no args, TTY) must enter
    the REPL.  A previous Click misconfiguration (is_flag + flag_value=False)
    made the `interactive` kwarg resolve to False even with no flag, so every
    local run silently rendered the landing page.  Assert the CLI passes
    cli_enabled=True into ReplConfig.load and actually calls run_repl.
    """
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda: None)
    monkeypatch.setattr("app.cli.__main__.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.cli.__main__.sys.stdout.isatty", lambda: True)

    load_calls: list[dict] = []
    orig_load = ReplConfig.load

    @classmethod  # type: ignore[misc]
    def spy_load(cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return orig_load(**kw)

    monkeypatch.setattr("app.cli.repl.config.ReplConfig.load", spy_load)

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "app.cli.__main__.render_landing",
        lambda: landing_calls.append(1),
    )

    with (
        patch("app.cli.repl.run_repl", return_value=0),
        patch("app.cli.repl.loop.run_repl", return_value=0),
    ):
        exit_code = main([])

    assert exit_code == 0
    assert len(load_calls) == 1
    assert load_calls[0].get("cli_enabled") is True, (
        f"default no-args run must pass cli_enabled=True, got {load_calls[0]}"
    )
    assert landing_calls == [], "REPL should run, not landing page"


def test_agent_subcommand_launches_repl(monkeypatch) -> None:
    """`opensre agent` must always enter the REPL, even if config disables it.

    The explicit subcommand expresses clear user intent, so env/file disables
    are overridden by passing cli_enabled=True into ReplConfig.load.
    """
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.commands.agent.capture_cli_invoked", lambda: None)
    monkeypatch.setattr("app.cli.commands.agent.sys.stdin.isatty", lambda: True)
    monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")

    load_calls: list[dict] = []
    orig_load = ReplConfig.load

    @classmethod  # type: ignore[misc]
    def spy_load(cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return orig_load(**kw)

    monkeypatch.setattr("app.cli.repl.config.ReplConfig.load", spy_load)

    run_repl_calls: list[ReplConfig] = []

    def fake_run_repl(**kw: object) -> int:
        cfg = kw.get("config")
        assert isinstance(cfg, ReplConfig)
        run_repl_calls.append(cfg)
        return 0

    with (
        patch("app.cli.repl.run_repl", side_effect=fake_run_repl),
        patch("app.cli.repl.loop.run_repl", side_effect=fake_run_repl),
    ):
        exit_code = main(["agent"])

    assert exit_code == 0
    assert len(load_calls) == 1
    assert load_calls[0].get("cli_enabled") is True
    assert len(run_repl_calls) == 1
    assert run_repl_calls[0].enabled is True, "agent subcommand must override OPENSRE_INTERACTIVE=0"


def test_agent_subcommand_accepts_layout(monkeypatch) -> None:
    """`opensre agent --layout pinned` must forward layout into ReplConfig."""
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.commands.agent.capture_cli_invoked", lambda: None)
    monkeypatch.setattr("app.cli.commands.agent.sys.stdin.isatty", lambda: True)

    run_repl_calls: list[ReplConfig] = []

    def fake_run_repl(**kw: object) -> int:
        cfg = kw.get("config")
        assert isinstance(cfg, ReplConfig)
        run_repl_calls.append(cfg)
        return 0

    with (
        patch("app.cli.repl.run_repl", side_effect=fake_run_repl),
        patch("app.cli.repl.loop.run_repl", side_effect=fake_run_repl),
    ):
        exit_code = main(["agent", "--layout", "pinned"])

    assert exit_code == 0
    assert len(run_repl_calls) == 1
    assert run_repl_calls[0].layout == "pinned"


def test_agent_subcommand_errors_on_non_tty(monkeypatch, capsys) -> None:
    """`opensre agent` must surface a clear error on non-TTY stdin.

    The module docstring promises the explicit subcommand starts the REPL on
    user intent. On piped/CI stdin we cannot do that, so we raise a loud
    OpenSREError with a suggestion instead of silently returning 0.
    """
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.commands.agent.capture_cli_invoked", lambda: None)
    monkeypatch.setattr("app.cli.commands.agent.sys.stdin.isatty", lambda: False)

    def _fail_if_called(**_kw: object) -> int:
        raise AssertionError("run_repl must not run when stdin is not a TTY")

    with (
        patch("app.cli.repl.run_repl", side_effect=_fail_if_called),
        patch("app.cli.repl.loop.run_repl", side_effect=_fail_if_called),
    ):
        exit_code = main(["agent"])

    assert exit_code != 0, "non-TTY agent invocation must not silently succeed"
    captured = capsys.readouterr()
    assert "TTY" in captured.err or "TTY" in captured.out
