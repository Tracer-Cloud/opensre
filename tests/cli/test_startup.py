"""CLI process startup: one named step, in a fixed order.

``main()`` opened with eight statements of process setup — env bootstrap, error
reporting, observability adapters, terminal prompt behaviour, signal handling —
before invoking the Click group. None of it is CLI-argument logic, and the
ordering constraints survived only as position in a long function.

Order is load-bearing and is what these tests pin:

* env variables load **before** Sentry, so the DSN and the no-telemetry opt-out
  are visible when error reporting initialises;
* observability adapters install **before** the group runs, so core code that
  calls the abstractions during a command routes through the Rich-aware
  implementations rather than the no-op defaults.

Importing this module must also stay cheap — see the fast-version test below.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from surfaces.cli.app import cli

# Env is bound via bootstrap.process; Sentry/adapters are imported
# inside CLI ``run()`` so those patches target the defining module.
_ENV = "bootstrap.process.bootstrap_opensre_env_once"
_SENTRY = "infrastructure.observability.errors.sentry.init_sentry"
_ADAPTERS = "surfaces.shared.terminal.output.boundary.install_product_adapters"
_ESCAPE = "infrastructure.terminal.prompt_support.install_questionary_escape_cancel"
_CTRL_C = "infrastructure.terminal.prompt_support.install_questionary_ctrl_c_double_exit"
_SIGINT = "surfaces.cli.signals.install_sigint_handler"


def _raise_missing_sentry(**_kwargs: Any) -> None:
    raise ModuleNotFoundError(name="sentry_sdk")


def _silence_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize every side effect so a test can assert one thing."""
    for target in (_ENV, _ADAPTERS, _ESCAPE, _CTRL_C, _SIGINT):
        monkeypatch.setattr(target, lambda *_a, **_kw: None)


def test_startup_runs_the_steps_in_the_required_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    order: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: order.append("env"))
    monkeypatch.setattr(_SENTRY, lambda **_kw: order.append("sentry"))
    monkeypatch.setattr(_ADAPTERS, lambda: order.append("adapters"))
    monkeypatch.setattr(_ESCAPE, lambda: None)
    monkeypatch.setattr(_CTRL_C, lambda: None)
    monkeypatch.setattr(_SIGINT, lambda: None)

    # Act
    startup.run(cli, ["doctor"])

    # Assert — shared bootstrap.process boots env; CLI owns Sentry then Rich adapters.
    assert order.index("env") < order.index("sentry"), order
    assert order.index("sentry") < order.index("adapters"), order


def test_startup_installs_the_cli_auth_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor`/`config` must reach registered CLI adapters on the bare CLI path.

    CLI_PROFILE boots env only, so unless startup wires the CLI-auth checker, a
    status check for an installed CLI provider resolves to no adapter and
    misreports it as unavailable. This pins that startup installs the checker.
    """
    # Arrange: a registry that reports claude-code installed + logged in, and no
    # checker installed yet (undo the autouse fixture's install).
    from bootstrap.process import reset_process_runtime_for_tests
    from config.llm_auth import cli_auth
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    monkeypatch.setattr(_SENTRY, lambda **_kw: None)

    ready = MagicMock()
    ready.adapter_factory.return_value.detect.return_value = MagicMock(
        installed=True, logged_in=True, detail="Logged in."
    )
    monkeypatch.setattr(
        "integrations.llm_cli.registry.get_cli_provider_registration",
        lambda provider: ready if provider == "claude-code" else None,
    )
    monkeypatch.setattr(cli_auth, "_installed", None)

    # Act
    startup.run(cli, ["doctor"])

    # Assert: the port now reaches the registered adapter.
    state = cli_auth.resolve_cli_auth_state("claude-code")
    assert state is not None
    assert state.installed is True


def test_missing_sentry_module_is_tolerated_during_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """``opensre update`` must still run when sentry_sdk is being replaced."""
    # Arrange
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    monkeypatch.setattr(_SENTRY, _raise_missing_sentry)

    # Act / Assert — no exception for the update path.
    startup.run(cli, ["update"])


def test_missing_sentry_module_still_raises_for_other_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside `update`, a missing sentry_sdk is a real broken install."""
    # Arrange
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    monkeypatch.setattr(_SENTRY, _raise_missing_sentry)

    # Act / Assert — subcommands init Sentry synchronously so the raise
    # reaches the caller (background init would swallow it on a daemon thread).
    with pytest.raises(ModuleNotFoundError):
        startup.run(cli, ["doctor"])


def test_sentry_sdk_installed_tolerates_modules_without_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test doubles in ``sys.modules`` must not make presence checks raise."""
    from types import SimpleNamespace

    from surfaces.cli import startup

    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace())

    assert startup._sentry_sdk_installed() is True


def test_bare_shell_raises_when_sentry_sdk_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare ``opensre`` checks presence inline before background init."""
    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    monkeypatch.setattr(startup, "_sentry_sdk_installed", lambda: False)

    with pytest.raises(ModuleNotFoundError, match="sentry_sdk"):
        startup.run(cli, [])


def test_bare_shell_hands_back_error_reporting_start_and_does_not_run_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare ``opensre`` defers Sentry to the returned callable; subcommands do not."""
    # Arrange
    import threading

    from bootstrap.process import reset_process_runtime_for_tests
    from surfaces.cli import startup

    reset_process_runtime_for_tests()
    _silence_all(monkeypatch)
    started = threading.Event()
    monkeypatch.setattr(_SENTRY, lambda **_kw: started.set())

    # Act
    start_error_reporting = startup.run(cli, [])

    # Assert: nothing started until the shell says the banner is painted.
    assert start_error_reporting is not None
    assert not started.is_set()
    start_error_reporting()
    assert started.wait(timeout=5)

    # Subcommands initialise in line and hand nothing back.
    started.clear()
    assert startup.run(cli, ["doctor"]) is None
    assert started.is_set()


def test_importing_the_entry_point_does_not_load_the_terminal_stack() -> None:
    """``opensre --version`` must answer before questionary/prompt_toolkit load.

    The entry point imports this module at module scope, so anything imported
    here lands in front of the fast-version path — a broken or missing terminal
    dependency would then break ``--version``, which exists precisely to work
    when the full CLI does not. Run in a clean interpreter because the test
    session has already imported half the product.
    """
    # Arrange
    probe = (
        "import sys; import surfaces.cli.__main__; "
        "print(len([n for n in sys.modules "
        "if n.split('.')[0] in {'questionary', 'prompt_toolkit'}]))"
    )

    # Act
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    # Assert
    assert result.stdout.strip() == "0", result.stdout


def test_fast_version_answers_without_the_terminal_stack() -> None:
    """The fast path prints a version and still loads no terminal stack."""
    # Arrange
    probe = (
        "import sys; from surfaces.cli.__main__ import main; main(['--version']); "
        "print('TERMINAL_MODULES', len([n for n in sys.modules "
        "if n.split('.')[0] in {'questionary', 'prompt_toolkit'}]))"
    )

    # Act
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    # Assert
    assert "TERMINAL_MODULES 0" in result.stdout, result.stdout


def test_fast_help_invocation_detects_help_flags() -> None:
    from surfaces.cli.invocation import is_fast_help_invocation

    assert is_fast_help_invocation(cli, ["--help"])
    assert is_fast_help_invocation(cli, ["-h"])
    assert is_fast_help_invocation(cli, ["doctor", "--help"])
    assert is_fast_help_invocation(cli, ["ask", "--allowed-tool", "grafana_query", "--help"])
    assert not is_fast_help_invocation(cli, [])
    assert not is_fast_help_invocation(cli, ["doctor"])
    assert not is_fast_help_invocation(cli, ["--version"])


def test_fast_help_invocation_ignores_help_tokens_used_as_values() -> None:
    """``--help`` / ``-h`` bound as option values or ``--`` operands are not help."""
    from surfaces.cli.invocation import is_fast_help_invocation, resolve_command_parts

    assert not is_fast_help_invocation(cli, ["ask", "--allowed-tool", "--help", "prompt"])
    assert not is_fast_help_invocation(cli, ["ask", "--allowed-tool", "-h", "prompt"])
    assert not is_fast_help_invocation(cli, ["ask", "--", "--help"])
    assert not is_fast_help_invocation(cli, ["--theme", "--help"])
    assert resolve_command_parts(cli, ["ask", "--allowed-tool", "--help", "prompt"]) == ["ask"]
    assert resolve_command_parts(cli, ["ask", "--", "--help"]) == ["ask"]


def test_help_flag_skips_full_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root ``--help`` must not install adapters or Sentry (kubernetes/boto3)."""

    def fail_bootstrap(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--help should not bootstrap the full CLI")

    monkeypatch.setattr("infrastructure.observability.errors.sentry.init_sentry", fail_bootstrap)
    monkeypatch.setattr(
        "surfaces.shared.terminal.output.boundary.install_product_adapters",
        fail_bootstrap,
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)

    from surfaces.cli.app import main

    assert main(["--help"]) == 0


def test_help_as_option_value_still_runs_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Click binds ``--help`` to ``--allowed-tool``; that is not a help invocation."""
    started: list[bool] = []

    monkeypatch.setattr(
        "surfaces.cli.app.startup.run",
        lambda *_a, **_k: started.append(True),
    )
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        "surfaces.cli.ask.approval.unknown_allowed_tools",
        lambda _v: ("--help",),
    )

    from surfaces.cli.app import main

    rc = main(["ask", "--allowed-tool", "--help", "prompt"])
    assert started
    assert rc != 0


def test_fast_help_does_not_import_vendor_sdks() -> None:
    """``opensre --help`` must not pay kubernetes/boto3/Sentry import tax."""
    probe = (
        "import sys; from surfaces.cli.__main__ import main; main(['--help']); "
        "heavy = [n for n in sys.modules if n.split('.')[0] in "
        "{'kubernetes', 'boto3', 'botocore', 'sentry_sdk', 'litellm'}]; "
        "cmds = [n for n in sys.modules if n.startswith('surfaces.cli.commands.') "
        "and n not in {'surfaces.cli.commands', 'surfaces.cli.commands.command_specs'}]; "
        "print('HEAVY', ','.join(sorted(heavy)[:12]) or 'none'); "
        "print('CMDS', ','.join(sorted(cmds)) or 'none')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "HEAVY none" in result.stdout, result.stdout + result.stderr
    assert "CMDS none" in result.stdout, result.stdout + result.stderr


def test_subcommand_help_still_loads_that_command(capsys: pytest.CaptureFixture[str]) -> None:
    """``opensre ask --help`` must print real options from the ask command module."""
    from surfaces.cli.app import main

    rc = main(["ask", "--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--allowed-tool" in out
    assert "--context-file" in out
    assert "dangerously-bypass-approvals" in out


def test_ask_help_does_not_import_the_agent_harness() -> None:
    """Ask help is the Click adapter; the harness loads when ask actually runs."""
    probe = (
        "import sys; from surfaces.cli.__main__ import main; main(['ask', '--help']); "
        "heavy = [n for n in sys.modules if n.startswith('core.agent_harness')]; "
        "print('HARNESS', ','.join(sorted(heavy)[:8]) or 'none')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "HARNESS none" in result.stdout, result.stdout + result.stderr


def test_help_does_not_resolve_the_git_version() -> None:
    """Root help must not read git / package metadata just to exist."""
    probe = (
        "import sys; from surfaces.cli.__main__ import main; main(['--help']); "
        "print('BUILD_INFO', 'config.runtime_metadata.build_info' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "BUILD_INFO False" in result.stdout, result.stdout + result.stderr
