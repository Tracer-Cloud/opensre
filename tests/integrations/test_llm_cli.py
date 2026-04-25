"""Unit tests for the CLI-backed LLM runner."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe
from app.integrations.llm_cli.runner import CLIBackedLLMClient, _build_subprocess_env, _strip_ansi
from app.services.llm_client import LLMResponse


def test_strip_ansi():
    """Test stripping ANSI escape codes from text."""
    text = "\x1b[31mHello\x1b[0m \x1b[1mWorld\x1b[0m"
    assert _strip_ansi(text) == "Hello World"


def test_build_subprocess_env():
    """Test building safe environment for subprocesses."""
    with patch.dict(
        "os.environ", {"HOME": "/home/user", "SECRET": "shhh", "LC_ALL": "en_US.UTF-8"}
    ):
        env = _build_subprocess_env({"EXTRA": "value"})
        assert env["HOME"] == "/home/user"
        assert env["LC_ALL"] == "en_US.UTF-8"
        assert env["EXTRA"] == "value"
        assert "SECRET" not in env


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "TestLLM"
    adapter.binary_env_key = "TEST_BIN"
    adapter.install_hint = "Install test-cli"
    adapter.auth_hint = "Run test auth"
    return adapter


def test_cli_client_invoke_success(mock_adapter):
    """Test successful CLI LLM invocation."""
    client = CLIBackedLLMClient(mock_adapter)

    # Mock probe
    mock_adapter.detect.return_value = CLIProbe(
        installed=True,
        version="1.0.0",
        logged_in=True,
        bin_path="/usr/bin/test-cli",
        detail="Ready",
    )

    # Mock build
    mock_adapter.build.return_value = CLIInvocation(
        argv=["test-cli", "generate"], stdin="Prompt", timeout_sec=30
    )

    # Mock subprocess
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Raw output", stderr="")

        # Mock parse
        mock_adapter.parse.return_value = "Cleaned output"

        response = client.invoke("Test prompt")

        assert isinstance(response, LLMResponse)
        assert response.content == "Cleaned output"
        mock_run.assert_called_once()


def test_cli_client_not_installed(mock_adapter):
    """Test error when CLI is not installed."""
    client = CLIBackedLLMClient(mock_adapter)
    mock_adapter.detect.return_value = CLIProbe(
        installed=False, version=None, logged_in=None, bin_path=None, detail="Not found"
    )

    with pytest.raises(RuntimeError, match="CLI not found"):
        client.invoke("prompt")


def test_cli_client_not_authenticated(mock_adapter):
    """Test error when CLI is not authenticated."""
    client = CLIBackedLLMClient(mock_adapter)
    mock_adapter.detect.return_value = CLIProbe(
        installed=True, version="1.0.0", logged_in=False, bin_path="/bin/test", detail="Expired"
    )

    with pytest.raises(RuntimeError, match="not authenticated"):
        client.invoke("prompt")


def test_cli_client_timeout(mock_adapter):
    """Test handling of subprocess timeout."""
    client = CLIBackedLLMClient(mock_adapter)
    mock_adapter.detect.return_value = CLIProbe(
        installed=True,
        version="1.0.0",
        logged_in=True,
        bin_path="/bin/test",
        detail="Ready",
    )
    mock_adapter.build.return_value = CLIInvocation(argv=["test"], timeout_sec=1)

    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="test", timeout=1)),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        client.invoke("prompt")


def test_cli_client_failure_exit_code(mock_adapter):
    """Test handling of non-zero exit codes."""
    client = CLIBackedLLMClient(mock_adapter)
    mock_adapter.detect.return_value = CLIProbe(
        installed=True,
        version="1.0.0",
        logged_in=True,
        bin_path="/bin/test",
        detail="Ready",
    )
    mock_adapter.build.return_value = CLIInvocation(argv=["test"])

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error msg")
        mock_adapter.explain_failure.return_value = "CLI failed: Error msg"

        with pytest.raises(RuntimeError, match="CLI failed: Error msg"):
            client.invoke("prompt")
