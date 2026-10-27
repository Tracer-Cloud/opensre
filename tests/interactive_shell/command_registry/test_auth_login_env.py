"""Login delegation must preserve the shell's unrelated exported settings."""

from __future__ import annotations

import os
import subprocess
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import cli_parity


@pytest.mark.parametrize("set_provider,exit_code", [(True, 0), (False, 0), (True, 1)])
def test_login_reloads_only_selected_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, set_provider: bool, exit_code: int
) -> None:
    env_path = tmp_path / ".env"
    # Already-saved values still need to override exports when explicitly selected.
    env_path.write_text(
        "LLM_PROVIDER=chatgpt\nCHATGPT_MODEL=selected-model\n"
        "OPENSRE_LLM_CONNECTION=local\nUNRELATED_SETTING=saved\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(env_path))
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("CHATGPT_MODEL", "exported-model")
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "account")
    monkeypatch.setenv("UNRELATED_SETTING", "exported")
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, exit_code, stdout="", stderr="")

    monkeypatch.setattr(cli_parity.subprocess, "run", run)
    args = ["auth", "login", "chatgpt"]
    args.append("--set-provider" if set_provider else "--no-set-provider")
    cli_parity.run_cli_command(Console(file=StringIO()), args)

    switched = set_provider and exit_code == 0
    assert os.environ["UNRELATED_SETTING"] == "exported"
    assert os.environ["LLM_PROVIDER"] == ("chatgpt" if switched else "anthropic")
    assert os.environ["CHATGPT_MODEL"] == ("selected-model" if switched else "exported-model")
    assert os.environ["OPENSRE_LLM_CONNECTION"] == ("local" if switched else "account")
    assert os.environ.get("OPENSRE_LLM_TRANSPORT") == (None if switched else "litellm")


@pytest.mark.parametrize("selected", [False, True])
def test_login_result_refreshes_connection_without_reactivating_old_selection(
    monkeypatch, tmp_path, selected
):
    from config.constants.llm import OPENSRE_MODEL_SETUP_RESULT_ENV
    from surfaces.shared.llm_setup.handoff import notify_setup

    path = tmp_path / ".env"
    path.write_text(
        "LLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=new-key\nDEEPSEEK_REASONING_MODEL=deepseek-chat\nOPENSRE_LLM_CONNECTION=local\n"
    )
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(path))
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "account")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-key")
    monkeypatch.setenv("UNRELATED_SETTING", "exported")

    def run(cmd, **kwargs):
        with monkeypatch.context() as child:
            child.setenv(
                OPENSRE_MODEL_SETUP_RESULT_ENV, kwargs["env"][OPENSRE_MODEL_SETUP_RESULT_ENV]
            )
            notify_setup(connected="deepseek", selected=selected)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_parity.subprocess, "run", run)
    cli_parity.run_cli_command(Console(file=StringIO()), ["auth", "login", "deepseek"])
    assert os.environ["DEEPSEEK_API_KEY"] == "new-key"
    assert os.environ["LLM_PROVIDER"] == ("deepseek" if selected else "anthropic")
    assert os.environ["OPENSRE_LLM_CONNECTION"] == ("local" if selected else "account")
    assert os.environ["UNRELATED_SETTING"] == "exported"


def test_choose_later_on_active_provider_preserves_exported_model(monkeypatch, tmp_path):
    from config.constants.llm import OPENSRE_MODEL_SETUP_RESULT_ENV
    from surfaces.shared.llm_setup.handoff import notify_setup

    path = tmp_path / ".env"
    path.write_text(
        "LLM_PROVIDER=openai\nOPENAI_REASONING_MODEL=old-saved-model\nOPENAI_API_KEY=new-key\nOPENSRE_LLM_CONNECTION=local\n"
    )
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(path))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "active-exported-model")
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")

    def run(cmd, **kwargs):
        with monkeypatch.context() as child:
            child.setenv(
                OPENSRE_MODEL_SETUP_RESULT_ENV, kwargs["env"][OPENSRE_MODEL_SETUP_RESULT_ENV]
            )
            notify_setup(connected="openai")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_parity.subprocess, "run", run)
    cli_parity.run_cli_command(Console(file=StringIO()), ["auth", "login", "openai"])
    assert os.environ["OPENAI_API_KEY"] == "new-key"
    assert os.environ["OPENAI_REASONING_MODEL"] == "active-exported-model"


@pytest.mark.parametrize("selected", [False, True])
def test_login_cli_default_clears_parent_override_only_after_selection(
    monkeypatch, tmp_path, selected
):
    from config.constants.llm import OPENSRE_MODEL_SETUP_RESULT_ENV
    from surfaces.shared.llm_setup.handoff import notify_setup

    path = tmp_path / ".env"
    path.write_text("LLM_PROVIDER=claude-code\nCLAUDE_CODE_MODEL=\nOPENSRE_LLM_CONNECTION=local\n")
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(path))
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.setenv("CLAUDE_CODE_MODEL", "opus")
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "local")

    def run(cmd, **kwargs):
        with monkeypatch.context() as child:
            child.setenv(
                OPENSRE_MODEL_SETUP_RESULT_ENV, kwargs["env"][OPENSRE_MODEL_SETUP_RESULT_ENV]
            )
            notify_setup(connected="claude-code", selected=selected)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_parity.subprocess, "run", run)
    cli_parity.run_cli_command(Console(file=StringIO()), ["auth", "login", "claude-code"])
    assert os.environ["CLAUDE_CODE_MODEL"] == ("" if selected else "opus")
