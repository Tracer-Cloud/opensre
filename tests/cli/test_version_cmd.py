from __future__ import annotations

import platform

from config.version import get_opensre_version
from surfaces.cli.app import main


def test_version_subcommand(monkeypatch, capsys) -> None:
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)

    rc = main(["version"])
    assert rc == 0

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert lines[0] == f"opensre {get_opensre_version()}"
    assert lines[1] == f"Python  {platform.python_version()}"
    assert lines[2] == f"OS      {platform.system().lower()} ({platform.machine()})"


def test_version_flag_uses_fast_path(monkeypatch, capsys) -> None:
    def fail_bootstrap(*_args, **_kwargs) -> None:
        raise AssertionError("--version should not bootstrap the full CLI")

    monkeypatch.setattr("infrastructure.observability.errors.sentry.init_sentry", fail_bootstrap)
    monkeypatch.setattr("surfaces.cli.startup.sentry_entrypoint_for", fail_bootstrap)

    rc = main(["--version"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == f"opensre, version {get_opensre_version()}"


def test_help_flag_skips_full_startup(monkeypatch, capsys) -> None:
    def fail_bootstrap(*_args, **_kwargs) -> None:
        raise AssertionError("--help should not bootstrap the full CLI")

    monkeypatch.setattr("infrastructure.observability.errors.sentry.init_sentry", fail_bootstrap)
    monkeypatch.setattr(
        "surfaces.shared.terminal.output.boundary.install_product_adapters",
        fail_bootstrap,
    )

    rc = main(["--help"])

    assert rc == 0
    assert "Usage:" in capsys.readouterr().out
