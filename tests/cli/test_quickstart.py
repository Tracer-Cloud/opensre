"""Docs characterization for https://www.opensre.com/docs/quickstart.

Pins every command and path the quickstart tells users to type, so a doc drift
or CLI rename fails here before a new user hits it.

Live install paths (real brew/curl CDN) live in
``tests/e2e/install/test_live_installers.py`` (``OPENSRE_LIVE_INSTALL=1``).
"""

from __future__ import annotations

import errno
import json
import re
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from config.constants.paths import REPO_ROOT
from surfaces.cli.lifecycle.update import _INSTALL_SCRIPT, _INSTALL_SCRIPT_PS1
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from tests.cli.test_smoke import CliSandbox, _cli_env, _run_cli

QUICKSTART_MDX = REPO_ROOT / "docs" / "quickstart.mdx"


@pytest.fixture()
def cli_sandbox(tmp_path: Path) -> CliSandbox:
    home = tmp_path / "home"
    home.mkdir()
    project_env_path = tmp_path / "project.env"
    return CliSandbox(
        home=home,
        project_env_path=project_env_path,
        env=_cli_env(home, project_env_path),
    )


class _ReleaseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = json.dumps(
            {
                "tag_name": "main-build",
                "body": "- Version: `9999.0.0`\n- Commit: `deadbeef`\n",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture()
def release_api_url() -> Iterator[str]:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ReleaseHandler)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES}:
            pytest.skip("localhost HTTP server binding is not permitted in this environment")
        raise

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/releases/tags/main-build"
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


# ── Docs contract ────────────────────────────────────────────────────────────


def test_quickstart_doc_lists_every_user_command() -> None:
    text = QUICKSTART_MDX.read_text(encoding="utf-8")
    for needle in (
        "brew tap tracer-cloud/tap",
        "brew install tracer-cloud/tap/opensre",
        "curl -fsSL https://install.opensre.com | bash",
        "irm https://install.opensre.com | iex",
        "opensre setup",
        "opensre\n",
        'opensre ask "why did checkout latency increase today?"',
        "opensre update",
        "opensre uninstall",
        "opensre uninstall --yes",
        "/help",
        "/status",
        "/effort",
        "/exit",
    ):
        assert needle in text, f"quickstart.mdx is missing documented step {needle!r}"


def test_quickstart_install_urls_match_update_lifecycle() -> None:
    """The update command must re-fetch the same installer the docs advertise."""
    assert _INSTALL_SCRIPT == "https://install.opensre.com"
    assert _INSTALL_SCRIPT_PS1 == "https://install.opensre.com"
    install_sh = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    # Anchored: a bare substring test would also accept a lookalike host such
    # as ``https://install.opensre.com.evil.test``.
    assert re.search(r"https://install\.opensre\.com(?![\w.-])", install_sh), (
        "install.sh must reference the documented installer URL"
    )
    # install.ps1 is what ``irm https://install.opensre.com | iex`` downloads;
    # the script body talks to GitHub releases, but must still hand off to setup.
    install_ps1 = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "$exe setup" in install_ps1 or "& $BinaryPath setup" in install_ps1


# ── Step: landing / interactive shell entry ──────────────────────────────────


def test_quickstart_bare_opensre_shows_landing_page(cli_sandbox: CliSandbox) -> None:
    """Non-TTY ``opensre`` prints the quick-start landing (not a hung REPL)."""
    result = _run_cli(cli_sandbox)

    assert result.exit_code == 0
    assert "Quick start:" in result.stdout
    assert "opensre setup" in result.stdout or "setup" in result.stdout
    assert "ask" in result.stdout


def test_quickstart_help_lists_documented_commands(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "--help")

    assert result.exit_code == 0
    for command in ("setup", "onboard", "ask", "update", "uninstall"):
        assert command in result.stdout, f"missing `{command}` in opensre --help"
    assert "No COMMAND: start the interactive shell" in result.stdout


def test_quickstart_slash_commands_are_registered() -> None:
    """Interactive shell commands named in the quickstart must exist."""
    for name in ("/help", "/status", "/effort", "/exit"):
        assert name in SLASH_COMMANDS, f"quickstart mentions {name} but it is not registered"


# ── Step: setup / onboard ────────────────────────────────────────────────────


def test_quickstart_setup_help(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "setup", "--help")

    assert result.exit_code == 0
    assert "--dev" in result.stdout
    assert "localhost:3000" in result.stdout


def test_quickstart_onboard_help(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "onboard", "--help")

    assert result.exit_code == 0
    assert "onboarding" in result.stdout.lower() or "wizard" in result.stdout.lower()


# ── Step: update ─────────────────────────────────────────────────────────────


def test_quickstart_update_help(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "update", "--help")

    assert result.exit_code == 0
    assert "update" in result.stdout.lower()
    assert "--check" in result.stdout


def test_quickstart_update_check_runs(cli_sandbox: CliSandbox, release_api_url: str) -> None:
    result = _run_cli(
        cli_sandbox,
        "update",
        "--check",
        extra_env={"OPENSRE_RELEASES_API_URL": release_api_url},
    )

    assert result.exit_code == 1
    assert "current:" in result.stdout
    assert "latest:" in result.stdout


# ── Uninstall ────────────────────────────────────────────────────────────────


def test_quickstart_uninstall_help_documents_yes(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "uninstall", "--help")

    assert result.exit_code == 0
    assert "--yes" in result.stdout
    assert "Remove opensre" in result.stdout or "local data" in result.stdout.lower()


def test_quickstart_uninstall_yes_skips_confirm_and_clears_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented ``opensre uninstall --yes`` skips the prompt and removes data."""
    from surfaces.cli.lifecycle.uninstall import run_uninstall

    data_dir = tmp_path / ".opensre"
    data_dir.mkdir()
    (data_dir / "marker").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall._data_dirs",
        lambda: [data_dir],
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall._is_binary_install",
        lambda: False,
    )
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 0)

    assert run_uninstall(yes=True) == 0
    assert not data_dir.exists()
