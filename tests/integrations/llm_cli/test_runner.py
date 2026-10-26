from __future__ import annotations

import logging
import os
import sys
import time
from unittest.mock import MagicMock, patch

from core.llm.types import ModelType
from integrations.llm_cli.base import CLIInvocation, CLIProbe
from integrations.llm_cli.runner import CLIBackedLLMClient


def _mock_probe() -> MagicMock:
    return MagicMock(installed=True, bin_path="/usr/bin/mock-cli", logged_in=True, detail="ok")


class _PlainStreamingAdapter:
    name = "plain-cli"
    streams_plain_stdout = True
    binary_env_key = "PLAIN_CLI_BIN"
    install_hint = "install plain-cli"
    auth_hint = "plain-cli login"
    min_version = None
    default_exec_timeout_sec = 5.0

    def __init__(self, script: str) -> None:
        self._script = script

    def detect(self) -> CLIProbe:
        return CLIProbe(
            installed=True,
            version="1.0.0",
            logged_in=True,
            bin_path=sys.executable,
            detail="ok",
        )

    def build(
        self,
        *,
        prompt: str,
        model: str | None,
        workspace: str,
        reasoning_effort: str | None = None,
    ) -> CLIInvocation:
        _ = prompt, model, workspace, reasoning_effort
        return CLIInvocation(
            argv=(sys.executable, "-c", self._script),
            stdin=None,
            cwd=os.getcwd(),
            env=None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        _ = stderr, returncode
        return stdout.strip()

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        _ = stdout, stderr
        return f"plain-cli exited with code {returncode}"


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_llm_spawn_log_redacts_prompt_in_argv(mock_run: MagicMock, caplog) -> None:
    prompt = "customer secret investigation context"
    mock_adapter = MagicMock()
    mock_adapter.name = "copilot"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock-cli", "-p", prompt, "--model", "gpt-5.1"),
        stdin=None,
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "copilot"
    assert spawn.argv == ["/usr/bin/mock-cli", "-p", "<redacted-prompt>", "--model", "gpt-5.1"]
    assert prompt not in spawn.argv


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_llm_spawn_log_keeps_non_prompt_argv_args(mock_run: MagicMock, caplog) -> None:
    prompt = "customer secret investigation context"
    mock_adapter = MagicMock()
    mock_adapter.name = "claude-code"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock-cli", "-p", "--output-format", "text"),
        stdin=prompt,
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "claude-code"
    assert spawn.argv == ["/usr/bin/mock-cli", "-p", "--output-format", "text"]


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_llm_spawn_log_redacts_prompt_equals_form(mock_run: MagicMock, caplog) -> None:
    prompt = "customer secret investigation context"
    mock_adapter = MagicMock()
    mock_adapter.name = "mock-cli"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock-cli", f"--prompt={prompt}", "--model", "gpt-5.1"),
        stdin=None,
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        with caplog.at_level(logging.DEBUG, logger="integrations.llm_cli.runner"):
            client = CLIBackedLLMClient(mock_adapter)
            client.invoke(prompt)

    spawn = next(record for record in caplog.records if record.msg == "cli_llm_spawn")
    assert spawn.provider == "mock-cli"
    assert spawn.argv == ["/usr/bin/mock-cli", "--prompt=<redacted-prompt>", "--model", "gpt-5.1"]
    assert all(prompt not in arg for arg in spawn.argv)


@patch("integrations.llm_cli.runner.subprocess.run")
def test_runner_uses_adapter_explain_failure_for_quota(mock_run: MagicMock) -> None:
    """Quota stderr is enriched via adapter explain_failure, not a runner-side classifier."""
    import pytest

    from integrations.llm_cli.failure_explain import explain_cli_failure

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.auth_hint = "codex login"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/codex", "exec", "-"),
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.side_effect = lambda **kwargs: explain_cli_failure(
        exit_label="codex exec", **kwargs
    )
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="429 Too Many Requests: quota exceeded"
    )

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter)
        with pytest.raises(RuntimeError, match="quota or rate limit exceeded"):
            client.invoke("hello")


@patch("integrations.llm_cli.runner.subprocess.run")
def test_toolcall_cli_invocation_uses_low_reasoning_effort(
    mock_run: MagicMock,
    monkeypatch,
) -> None:
    captured: dict[str, str | None] = {}

    def build_invocation(
        *,
        prompt: str,
        model: str | None,
        workspace: str,
        reasoning_effort: str | None = None,
    ) -> MagicMock:
        _ = prompt, model, workspace
        captured["reasoning_effort"] = reasoning_effort
        return MagicMock(
            argv=("/usr/bin/mock-cli",),
            stdin="hello",
            cwd="/tmp",
            env=None,
            timeout_sec=30.0,
        )

    monkeypatch.setenv("OPENSRE_REASONING_EFFORT", "high")
    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.detect.return_value = _mock_probe()
    mock_adapter.build.side_effect = build_invocation
    mock_adapter.parse.return_value = "answer"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model_type=ModelType.TOOLCALL)
        client.invoke("hello")

    assert captured["reasoning_effort"] == "low"


def test_invoke_stream_yields_plain_stdout_before_process_exit() -> None:
    """Plain-text CLI providers should not buffer a long answer until exit."""
    script = (
        "import sys, time; "
        "sys.stdout.write('a' * 200); sys.stdout.flush(); "
        "time.sleep(1.0); "
        "sys.stdout.write('done'); sys.stdout.flush()"
    )

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(_PlainStreamingAdapter(script))
        stream = client.invoke_stream("hello")
        started = time.monotonic()
        first = next(stream)
        first_elapsed = time.monotonic() - started
        rest = "".join(stream)

    assert first == "a" * 160
    assert first_elapsed < 0.8
    assert first + rest == ("a" * 200) + "done"
