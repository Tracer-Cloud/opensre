"""Tests for the GitHub Copilot CLI adapter (non-interactive ``copilot -p``)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.llm_cli.copilot import CopilotAdapter
from integrations.llm_cli.runner import CLIBackedLLMClient
from tests.integrations.llm_cli.testing_helpers import write_fake_runnable_cli_bin


@pytest.fixture(autouse=True)
def _copilot_detect_treats_which_hits_as_runnable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests mock ``which`` to ``/usr/bin/copilot`` without creating a real file on disk."""
    import integrations.llm_cli.binary_resolver as br

    real_runnable = br.is_runnable_binary
    shim = "/usr/bin/copilot"

    def _fake(path: str) -> bool:
        if path == shim:
            return True
        p = Path(path)
        if p.is_file():
            return real_runnable(path)
        return real_runnable(path)

    monkeypatch.setattr(br, "is_runnable_binary", _fake)


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "copilot 1.4.2\n"
    m.stderr = ""
    return m


def _gh_logged_in_proc() -> MagicMock:
    """Shape matches current ``gh auth status`` (github.com block + checkmark line)."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = (
        "github.com\n"
        "  ✓ Logged in to github.com account alice (keyring)\n"
        "  - Active account: true\n"
        "  - Git operations protocol: https\n"
        "  - Token: gho_************************************\n"
        "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'\n"
    )
    m.stderr = ""
    return m


def _gh_logged_out_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "You are not logged into any GitHub hosts. Run `gh auth login` to authenticate.\n"
    return m


def _clean_copilot_env(monkeypatch: pytest.MonkeyPatch, *, home: Path | None = None) -> None:
    for key in (
        "COPILOT_BIN",
        "COPILOT_MODEL",
        "COPILOT_HOME",
        "COPILOT_GH_HOST",
        "GH_HOST",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    if home is not None:
        monkeypatch.setenv("COPILOT_HOME", str(home))


# ---------------------------------------------------------------------------
# detect() — token env
#
# When stacking ``@patch`` decorators, pytest binds the *topmost* decorator to the
# *first* function parameter. Here ``copilot.shutil.which`` (``gh`` lookup) is
# first; ``binary_resolver.shutil.which`` (``copilot`` binary resolution) is third.
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_with_token_env_is_logged_in(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Token env is checked first; a non-empty var yields logged_in=True immediately."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = None  # gh not on PATH — should not matter
    mock_run.return_value = _version_proc()

    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghp_test")

    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "COPILOT_GITHUB_TOKEN" in probe.detail


# ---------------------------------------------------------------------------
# detect() — gh auth status
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_gh_logged_in_yields_true(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When gh auth status reports a session, logged_in=True."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = "/usr/bin/gh"

    def side_effect(args: list[str], **_kwargs: object) -> MagicMock:
        if "--version" in args:
            return _version_proc()
        if "auth" in args and "status" in args:
            return _gh_logged_in_proc()
        raise AssertionError(f"unexpected subprocess call: {args}")

    mock_run.side_effect = side_effect
    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")

    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "gh" in probe.detail.lower()


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_gh_auth_status_uses_hostname_when_copilot_gh_host_set(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-github.com COPILOT_GH_HOST adds ``gh auth status --hostname`` per GitHub docs."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = "/usr/bin/gh"

    captured: list[list[str]] = []

    def side_effect(args: list[str], **_kwargs: object) -> MagicMock:
        if "--version" in args:
            return _version_proc()
        if "auth" in args and "status" in args:
            captured.append(list(args))
            return _gh_logged_in_proc()
        raise AssertionError(f"unexpected subprocess call: {args}")

    mock_run.side_effect = side_effect
    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")
    monkeypatch.setenv("COPILOT_GH_HOST", "acme.ghe.com")

    probe = CopilotAdapter().detect()

    assert probe.logged_in is True
    assert captured and captured[0][-2:] == ["--hostname", "acme.ghe.com"]


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_gh_logged_in_via_token_line_fine_grained_pat(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Copilot supports ``github_pat_`` tokens; treat matching ``- Token:`` line as logged in."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = "/usr/bin/gh"

    fg_proc = MagicMock()
    fg_proc.returncode = 0
    fg_proc.stdout = "github.enterprise.com\n  - Token: github_pat_xxxxxxxxxxxx\n"
    fg_proc.stderr = ""

    def side_effect(args: list[str], **_kwargs: object) -> MagicMock:
        if "--version" in args:
            return _version_proc()
        if "auth" in args and "status" in args:
            return fg_proc
        raise AssertionError(f"unexpected subprocess call: {args}")

    mock_run.side_effect = side_effect
    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")

    probe = CopilotAdapter().detect()
    assert probe.logged_in is True


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_gh_logged_out_keeps_copilot_auth_unknown(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A logged-out gh session cannot disprove a separate Copilot CLI login."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = "/usr/bin/gh"

    def side_effect(args: list[str], **_kwargs: object) -> MagicMock:
        if "--version" in args:
            return _version_proc()
        if "auth" in args and "status" in args:
            return _gh_logged_out_proc()
        raise AssertionError(f"unexpected subprocess call: {args}")

    mock_run.side_effect = side_effect
    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")

    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "Could not verify" in probe.detail


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_gh_timeout_maps_to_unknown_auth(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A gh timeout maps to None for gh; overall auth stays unknown without token env."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = "/usr/bin/gh"

    def side_effect(args: list[str], **_kwargs: object) -> MagicMock:
        if "--version" in args:
            return _version_proc()
        if "auth" in args and "status" in args:
            raise subprocess.TimeoutExpired(cmd=args, timeout=5.0)
        raise AssertionError(f"unexpected subprocess call: {args}")

    mock_run.side_effect = side_effect
    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")

    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "Could not verify" in probe.detail


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_gh_not_installed_falls_through(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When gh is not on PATH, auth resolves to unknown if no token env."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = None  # gh not installed

    mock_run.return_value = _version_proc()
    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")

    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_config_json_plaintext_not_used_for_auth(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Copilot may store plaintext tokens in config.json — we do not treat that as probe signal."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = None
    mock_run.return_value = _version_proc()

    home = tmp_path / "copilot_home"
    home.mkdir()
    (home / "config.json").write_text('{"github_token": "ghu_realtoken"}')

    _clean_copilot_env(monkeypatch, home=home)
    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "Could not verify" in probe.detail


# ---------------------------------------------------------------------------
# detect() — binary / version failures
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_with_unrelated_files_is_not_logged_in(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Junk files in COPILOT_HOME must not yield logged_in=True."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = None
    mock_run.return_value = _version_proc()

    home = tmp_path / "copilot_home"
    home.mkdir()
    (home / "telemetry.log").write_text("noise")

    _clean_copilot_env(monkeypatch, home=home)
    probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_detect_when_binary_not_found(
    _mock_which: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_copilot_env(monkeypatch)
    # resolve_cli_binary falls back to known install dirs (e.g. /opt/homebrew/bin)
    # after the PATH lookup. Mocking shutil.which alone still finds a real copilot
    # install on a dev machine, so force nothing to resolve as runnable — this also
    # overrides the autouse fixture that treats real fallback paths as runnable.
    import integrations.llm_cli.binary_resolver as br

    monkeypatch.setattr(br, "is_runnable_binary", lambda _path: False)
    probe = CopilotAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert "not found" in probe.detail.lower()


@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_when_version_fails(
    mock_which: MagicMock,
    mock_run: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_which.return_value = "/usr/bin/copilot"
    failed = MagicMock()
    failed.returncode = 1
    failed.stdout = ""
    failed.stderr = "boom"
    mock_run.return_value = failed
    _clean_copilot_env(monkeypatch)
    probe = CopilotAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert "boom" in probe.detail


# ---------------------------------------------------------------------------
# detect() — no creds at all
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.copilot.shutil.which")
@patch("integrations.llm_cli.copilot.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_no_creds_no_token_is_unclear(
    mock_which: MagicMock,
    mock_run: MagicMock,
    mock_copilot_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without any credential signal, auth state is unclear (None)."""
    mock_copilot_which.return_value = "/usr/bin/copilot"
    mock_which.return_value = None  # gh not installed
    mock_run.return_value = _version_proc()

    _clean_copilot_env(monkeypatch, home=tmp_path / "empty")

    probe = CopilotAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is None
    assert "Could not verify" in probe.detail


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/copilot")
def test_build_argv_uses_non_interactive_flags(
    mock_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_copilot_env(monkeypatch)
    inv = CopilotAdapter().build(prompt="hello world", model=None, workspace="")

    assert inv.argv[0] == "/usr/bin/copilot"
    assert "-p" in inv.argv
    idx = inv.argv.index("-p")
    assert inv.argv[idx + 1] == "hello world"
    assert "--no-color" in inv.argv
    assert "--no-ask-user" in inv.argv
    assert "--silent" in inv.argv
    assert inv.stdin is None
    assert inv.cwd
    assert inv.env is None
    mock_which.assert_called()


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/copilot")
def test_build_uses_workspace_when_provided(
    _mock_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clean_copilot_env(monkeypatch)
    ws = tmp_path / "repo"
    ws.mkdir()
    inv = CopilotAdapter().build(prompt="p", model=None, workspace=str(ws))
    assert inv.cwd == str(ws)


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/copilot")
def test_build_adds_model_flag_when_provided(
    _mock_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_copilot_env(monkeypatch)
    inv = CopilotAdapter().build(prompt="p", model="claude-sonnet-4.6", workspace="")
    assert "--model" in inv.argv
    idx = inv.argv.index("--model")
    assert inv.argv[idx + 1] == "claude-sonnet-4.6"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/copilot")
def test_build_forwards_token_env_keys(
    _mock_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_copilot_env(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghp_a")
    monkeypatch.setenv("GH_TOKEN", "ghp_b")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_c")
    inv = CopilotAdapter().build(prompt="p", model=None, workspace="")
    assert inv.env is not None
    assert inv.env["COPILOT_GITHUB_TOKEN"] == "ghp_a"
    assert inv.env["GH_TOKEN"] == "ghp_b"
    assert inv.env["GITHUB_TOKEN"] == "ghp_c"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/copilot")
def test_build_forwards_copilot_config_envs(
    _mock_which: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copilot HOME/MODEL/host envs flow through the adapter's invocation env."""
    _clean_copilot_env(monkeypatch)
    monkeypatch.setenv("COPILOT_HOME", "/x/copilot")
    monkeypatch.setenv("COPILOT_MODEL", "gpt-5.2")
    monkeypatch.setenv("COPILOT_GH_HOST", "corp.github.example")
    monkeypatch.setenv("GH_HOST", "https://gh.enterprise.test")
    inv = CopilotAdapter().build(prompt="p", model=None, workspace="")
    assert inv.env is not None
    assert inv.env["COPILOT_HOME"] == "/x/copilot"
    assert inv.env["COPILOT_MODEL"] == "gpt-5.2"
    assert inv.env["COPILOT_GH_HOST"] == "corp.github.example"
    assert inv.env["GH_HOST"] == "https://gh.enterprise.test"


def test_build_raises_when_binary_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_copilot_env(monkeypatch)
    with (
        patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None) as mock_which,
        patch(
            "integrations.llm_cli.copilot._fallback_copilot_paths",
            return_value=[],
        ),
        pytest.raises(RuntimeError, match="Copilot CLI not found"),
    ):
        CopilotAdapter().build(prompt="p", model=None, workspace="")
    mock_which.assert_called()


def test_explicit_copilot_bin_used_when_runnable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clean_copilot_env(monkeypatch)
    bin_path = write_fake_runnable_cli_bin(tmp_path, "copilot")
    monkeypatch.setenv("COPILOT_BIN", str(bin_path))
    resolved = CopilotAdapter()._resolve_binary()
    assert resolved == str(bin_path)


# ---------------------------------------------------------------------------
# parse() / explain_failure()
# ---------------------------------------------------------------------------


def test_parse_strips_whitespace() -> None:
    adapter = CopilotAdapter()
    assert adapter.parse(stdout="  hello  \n", stderr="", returncode=0) == "hello"


def test_explain_failure_includes_auth_hint_on_unauthorized() -> None:
    adapter = CopilotAdapter()
    msg = adapter.explain_failure(
        stdout="",
        stderr="error: unauthorized — please /login",
        returncode=1,
    )
    assert "code 1" in msg
    assert "COPILOT_GITHUB_TOKEN" in msg or "/login" in msg
    assert "unauthorized" in msg


def test_explain_failure_does_not_mask_unrelated_error_with_login_in_text() -> None:
    """Regression: the substring 'login' must not trigger the auth hint."""
    adapter = CopilotAdapter()
    err = "Your current login: johndoe@github.com — Error: model 'gpt-5.2' not found in your plan"
    msg = adapter.explain_failure(stdout="", stderr=err, returncode=1)
    assert "model 'gpt-5.2' not found" in msg
    assert "COPILOT_GITHUB_TOKEN" not in msg
    assert "copilot login" not in msg


def test_explain_failure_truncates_long_output() -> None:
    adapter = CopilotAdapter()
    err = "x" * 5000
    msg = adapter.explain_failure(stdout="", stderr=err, returncode=2)
    assert "code 2" in msg
    assert "x" * 2000 in msg


# ---------------------------------------------------------------------------
# runner integration
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invokes_copilot_and_forwards_token_env(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runner merges adapter env (token vars) into the subprocess env."""
    _clean_copilot_env(monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghp_runner")
    monkeypatch.setenv("COPILOT_HOME", "/custom/copilot")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")

    mock_adapter = MagicMock()
    mock_adapter.name = "copilot"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/copilot",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/copilot", "-p", "hi", "--silent"],
        stdin=None,
        cwd="/tmp",
        env={"COPILOT_GITHUB_TOKEN": "ghp_runner", "COPILOT_HOME": "/custom/copilot"},
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"

    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model=None, max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["COPILOT_HOME"] == "/custom/copilot"
    assert env["COPILOT_GITHUB_TOKEN"] == "ghp_runner"
    assert "ANTHROPIC_API_KEY" not in env


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_resolves_copilot_provider() -> None:
    from integrations.llm_cli.registry import (
        CLI_PROVIDER_REGISTRY,
        get_cli_provider_registration,
    )

    reg = get_cli_provider_registration("copilot")
    assert reg is not None
    assert reg.model_env_key == "COPILOT_MODEL"
    assert "copilot" in CLI_PROVIDER_REGISTRY
    adapter = reg.adapter_factory()
    assert isinstance(adapter, CopilotAdapter)


# ---------------------------------------------------------------------------
# subprocess env security
# ---------------------------------------------------------------------------


def test_subprocess_env_does_not_leak_copilot_token_via_prefix_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``COPILOT_*`` must NOT be a prefix entry — COPILOT_GITHUB_TOKEN is a PAT."""
    from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

    monkeypatch.setenv("COPILOT_HOME", "/x/copilot")
    monkeypatch.setenv("COPILOT_MODEL", "gpt-5.2")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghp_super_secret")
    monkeypatch.setenv("COPILOT_BIN", "/usr/local/bin/copilot")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")

    env = build_cli_subprocess_env(None)

    assert "COPILOT_GITHUB_TOKEN" not in env
    assert "COPILOT_HOME" not in env
    assert "COPILOT_MODEL" not in env
    assert "COPILOT_BIN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "PATH" in env or os.environ.get("PATH") is None


def test_parse_returns_stripped_stdout() -> None:
    adapter = CopilotAdapter()
    assert adapter.parse(stdout="  hello world  \n", stderr="", returncode=0) == "hello world"


def test_parse_raises_on_empty_stdout() -> None:
    import pytest

    adapter = CopilotAdapter()
    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="  ", stderr="", returncode=0)


def test_parse_raises_on_empty_stdout_surfaces_stderr() -> None:
    import pytest

    adapter = CopilotAdapter()
    with pytest.raises(RuntimeError, match="some stderr detail"):
        adapter.parse(stdout="", stderr="some stderr detail", returncode=0)
