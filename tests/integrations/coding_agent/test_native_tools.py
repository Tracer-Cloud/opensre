"""Isolation contract of the built-in coding agent's tools: the checkout is the boundary."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from integrations.coding_agent.native_tools import WorkspaceTools


def test_tools_stay_inside_the_checkout_and_leave_git_history_to_the_caller(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "app.py").write_text("value = 1\n")
    (tmp_path / "secret.txt").write_text("token")
    (repo / "link").symlink_to(tmp_path / "secret.txt")
    workspace = WorkspaceTools(str(repo), deadline=time.monotonic() + 60)
    tools = {tool.name: tool.run for tool in workspace.registered_tools()}

    for path in ("../secret.txt", str(tmp_path / "secret.txt"), "link"):
        assert tools["read_file"](path=path)["ok"] is False
    assert tools["write_file"](path="../escape.txt", content="x")["ok"] is False
    assert tools["write_file"](path=".git/hooks/pre-commit", content="x")["ok"] is False
    assert not (tmp_path / "escape.txt").exists()

    edit = {"path": "app.py", "old_string": "value = 1", "new_string": "value = 2"}
    assert tools["edit_file"](**edit)["ok"] is False  # not read yet
    assert tools["read_file"](path="app.py")["ok"] is True
    assert tools["edit_file"](**edit)["ok"] is True
    assert (repo / "app.py").read_text() == "value = 2\n"

    for command in ("git commit -am fix", "make lint && git push", "sh -c 'git reset --hard'"):
        assert tools["bash"](command=command)["ok"] is False
    assert tools["bash"](command="git status --short")["ok"] is True
