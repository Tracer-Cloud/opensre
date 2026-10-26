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
    if not set_provider:
        args.append("--no-set-provider")
    cli_parity.run_cli_command(Console(file=StringIO()), args)

    switched = set_provider and exit_code == 0
    assert os.environ["UNRELATED_SETTING"] == "exported"
    assert os.environ["LLM_PROVIDER"] == ("chatgpt" if switched else "anthropic")
    assert os.environ["CHATGPT_MODEL"] == ("selected-model" if switched else "exported-model")
    assert os.environ["OPENSRE_LLM_CONNECTION"] == ("local" if switched else "account")
    assert os.environ.get("OPENSRE_LLM_TRANSPORT") == (None if switched else "litellm")
