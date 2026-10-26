"""Tests for Kimi Code CLI adapter."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from integrations.llm_cli.kimi import KimiAdapter


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "kimi-cli version: 1.40.0\n"
    m.stderr = ""
    return m


def _login_status_logged_in_proc() -> MagicMock:
    """Mock response for 'kimi login status' when logged in."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = "You are logged in.\n"
    m.stderr = ""
    return m


@patch("integrations.llm_cli.kimi.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_path_binary_logged_in_env(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/kimi"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        elif len(args) >= 3 and args[1] == "login" and args[2] == "status":
            return _login_status_logged_in_proc()
        raise AssertionError(f"Unexpected call: {args}")

    mock_run.side_effect = side_effect
    with patch.dict(os.environ, {"KIMI_API_KEY": "sk-test"}, clear=False):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/kimi"
    assert probe.version == "1.40.0"


@patch("integrations.llm_cli.kimi.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_min_version_enforcement(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/kimi"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            m = MagicMock()
            m.returncode = 0
            m.stdout = "kimi-cli version: 1.39.0\n"
            m.stderr = ""
            return m
        elif len(args) >= 3 and args[1] == "login" and args[2] == "status":
            # When version is below minimum, login status might still succeed
            m = MagicMock()
            m.returncode = 0
            m.stdout = "You are logged in.\n"
            m.stderr = ""
            return m
        raise AssertionError(f"Unexpected call: {args}")

    mock_run.side_effect = side_effect

    with patch.dict(os.environ, {}, clear=True):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.version == "1.39.0"
    assert "upgrade: uv tool upgrade kimi-cli" in probe.detail


@patch("integrations.llm_cli.kimi.pathlib.Path.exists", return_value=False)
@patch("integrations.llm_cli.kimi.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_missing_config_not_logged_in(
    mock_which: MagicMock, mock_run: MagicMock, _mock_exists: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/kimi"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        elif len(args) >= 3 and args[1] == "login" and args[2] == "status":
            # Not logged in case
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "Not logged in."
            return m
        raise AssertionError(f"Unexpected call: {args}")

    mock_run.side_effect = side_effect

    with patch.dict(os.environ, {}, clear=True):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch("integrations.llm_cli.kimi.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_login_status_not_logged_in_uses_api_key_fallback(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    """When kimi login status reports not logged in, check KIMI_API_KEY fallback."""
    mock_which.return_value = "/usr/bin/kimi"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login" and args[2] == "status":
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "Not logged in."
            return m
        raise AssertionError(f"Unexpected call: {args}")

    mock_run.side_effect = side_effect

    with patch.dict(os.environ, {"KIMI_API_KEY": "  sk-test  "}, clear=True):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "KIMI_API_KEY" in probe.detail
    assert mock_run.call_args_list[0].args[0] == ["/usr/bin/kimi", "--version"]
    assert mock_run.call_args_list[1].args[0] == ["/usr/bin/kimi", "login", "status"]


@patch("integrations.llm_cli.kimi.pathlib.Path.read_text")
@patch("integrations.llm_cli.kimi.pathlib.Path.exists", return_value=True)
@patch("integrations.llm_cli.kimi.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_login_status_unavailable_uses_config_fallback(
    mock_which: MagicMock,
    mock_run: MagicMock,
    _mock_exists: MagicMock,
    mock_read_text: MagicMock,
) -> None:
    mock_which.return_value = "/usr/bin/kimi"
    mock_read_text.return_value = '[providers.moonshot]\napi_key = "sk-config"\n'

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login" and args[2] == "status":
            m = MagicMock()
            m.returncode = 2
            m.stdout = ""
            m.stderr = "unknown command: login"
            return m
        raise AssertionError(f"Unexpected call: {args}")

    mock_run.side_effect = side_effect

    with patch.dict(os.environ, {}, clear=True):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "config.toml" in probe.detail


@patch(
    "integrations.llm_cli.binary_resolver.shutil.which",
    return_value="/usr/bin/kimi",
)
def test_build_adds_model_flag_and_yolo(mock_which: MagicMock) -> None:
    inv = KimiAdapter().build(prompt="p", model="kimi-k2.5", workspace="")
    assert inv.stdin == "p"
    assert "--yolo" in inv.argv
    assert "--print" in inv.argv
    assert "-m" in inv.argv
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "kimi-k2.5"
    mock_which.assert_called()


def test_kimi_cli_registry_entry() -> None:
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("kimi")
    assert reg is not None
    assert reg.model_env_key == "KIMI_MODEL"
    assert reg.adapter_factory().name == "kimi"


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invoke_forwards_kimi_env(mock_run: MagicMock) -> None:
    from integrations.llm_cli.kimi import KimiAdapter
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock(spec=KimiAdapter)
    mock_adapter.name = "kimi"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/kimi",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/kimi", "--print", "--yolo"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"

    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr,
        patch.dict(
            os.environ,
            {
                "KIMI_API_KEY": "sk-kimi",
                "KIMI_BASE_URL": "https://custom.kimi.com",
                "OPENAI_API_KEY": "sk-openai",
            },
            clear=False,
        ),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="kimi-k2.5", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["KIMI_API_KEY"] == "sk-kimi"
    assert env["KIMI_BASE_URL"] == "https://custom.kimi.com"
    assert "OPENAI_API_KEY" not in env


@patch("integrations.llm_cli.runner.time.sleep")
@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_retries_on_ex_tempfail(
    mock_run: MagicMock, mock_sleep: MagicMock
) -> None:
    """EX_TEMPFAIL (75) should be retried; on final success returns the answer."""
    from integrations.llm_cli.kimi import KimiAdapter
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock(spec=KimiAdapter)
    mock_adapter.name = "kimi"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/kimi",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/kimi", "--print", "--yolo"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = (
        "kimi exited with code 75. To resume this session: kimi -r abc"
    )

    tempfail = MagicMock(returncode=75, stdout="To resume this session: kimi -r abc", stderr="")
    success = MagicMock(returncode=0, stdout="answer\n", stderr="")
    mock_run.side_effect = [tempfail, success]

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="kimi-k2.5", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    assert mock_run.call_count == 2
    mock_sleep.assert_called_once()


@patch("integrations.llm_cli.runner.time.sleep")
@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_raises_after_all_tempfail_retries(
    mock_run: MagicMock, mock_sleep: MagicMock
) -> None:
    """EX_TEMPFAIL (75) exhausting all retries raises RuntimeError."""
    import pytest

    from integrations.llm_cli.kimi import KimiAdapter
    from integrations.llm_cli.runner import _TEMPFAIL_MAX_RETRIES, CLIBackedLLMClient

    mock_adapter = MagicMock(spec=KimiAdapter)
    mock_adapter.name = "kimi"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/kimi",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/kimi", "--print", "--yolo"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.return_value = (
        "kimi exited with code 75. To resume this session: kimi -r abc"
    )

    tempfail = MagicMock(returncode=75, stdout="To resume this session: kimi -r abc", stderr="")
    mock_run.side_effect = [tempfail] * (_TEMPFAIL_MAX_RETRIES + 1)

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="kimi-k2.5", max_tokens=256)
        with pytest.raises(RuntimeError):
            client.invoke("hello")

    assert mock_run.call_count == _TEMPFAIL_MAX_RETRIES + 1
    assert mock_sleep.call_count == _TEMPFAIL_MAX_RETRIES


def test_parse_and_explain_failure() -> None:
    adapter = KimiAdapter()

    # Test parse
    assert adapter.parse(stdout="  hello world  \n", stderr="", returncode=0) == "hello world"

    import pytest

    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="  ", stderr="", returncode=0)

    # Test explain_failure: Auth error
    fail_auth = adapter.explain_failure(stdout="LLM not set", stderr="", returncode=1)
    assert "Not logged in" in fail_auth
    assert "kimi login" in fail_auth

    fail_401 = adapter.explain_failure(stdout="", stderr="Error code: 401", returncode=1)
    assert "API key invalid" in fail_401

    # Test explain_failure: generic error
    fail_generic = adapter.explain_failure(stdout="", stderr="some error", returncode=1)
    assert "kimi exited with code 1" in fail_generic
    assert "some error" in fail_generic

    # Test explain_failure: empty output with code 0
    fail_empty = adapter.explain_failure(stdout="", stderr="", returncode=0)
    assert fail_empty == "kimi returned no output"


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_exit_75_raises_cli_timeout_error(mock_run: MagicMock) -> None:
    """Exit code 75 (EX_TEMPFAIL) must raise CLITimeoutError, not RuntimeError.

    Sentry ignores CLITimeoutError so transient kimi failures do not create
    spurious bug reports.
    """
    import pytest

    from integrations.llm_cli.kimi import KimiAdapter
    from integrations.llm_cli.runner import CLIBackedLLMClient, CLITimeoutError

    mock_adapter = MagicMock(spec=KimiAdapter)
    mock_adapter.name = "kimi"
    mock_adapter.detect.return_value = MagicMock(
        installed=True, bin_path="/usr/bin/kimi", logged_in=True, detail="ok"
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/kimi", "--print", "--yolo"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_run.return_value = MagicMock(returncode=75, stdout="", stderr="rate limit")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="kimi-k2.5", max_tokens=256)
        with pytest.raises(CLITimeoutError, match="exit 75"):
            client.invoke("hello")
