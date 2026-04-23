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
