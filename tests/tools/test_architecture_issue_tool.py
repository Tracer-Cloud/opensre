"""Tests for architecture action tools (clone, cleanup, save observations)."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from integrations.github.tools.architecture_issue_tool.repo_workspace import WorkspaceError
from integrations.github.tools.architecture_issue_tool.report_persistence import (
    ReportPersistenceError,
    sanitize_repo_name,
    save_architecture_observations,
)
from integrations.github.tools.architecture_issue_tool.tool import (
    architecture_cleanup_repo,
    architecture_clone_repo,
    architecture_save_observations,
)
from tests.tools.conftest import BaseToolContract
from tools import registry as registry_module


class TestArchitectureCloneRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_clone_repo.__opensre_registered_tool__


class TestArchitectureCleanupRepoContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_cleanup_repo.__opensre_registered_tool__


class TestArchitectureSaveObservationsContract(BaseToolContract):
    def get_tool_under_test(self):
        return architecture_save_observations.__opensre_registered_tool__


def test_architecture_clone_and_cleanup_are_mutating() -> None:
    assert architecture_clone_repo.__opensre_registered_tool__.side_effect_level == "mutating"
    assert architecture_cleanup_repo.__opensre_registered_tool__.side_effect_level == "mutating"


def test_architecture_tools_are_action_surface_only() -> None:
    registry_module.clear_tool_registry_cache()
    action = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("action")
    }
    chat = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("chat")}

    for name in (
        "architecture_clone_repo",
        "architecture_cleanup_repo",
        "architecture_save_observations",
    ):
        assert name in action
        assert name not in chat

    assert "scan_architecture_imports" not in action
    assert "scan_module_placement" not in action
    assert "find_architecture_violations" not in action
    assert "find_architecture_violations" not in chat


def test_architecture_clone_repo_local_path(tmp_path: Path) -> None:
    result = architecture_clone_repo(
        github_connection_origin="webapp",
        github_token="app-token",
        owner="org",
        repo="repo",
        local_path=str(tmp_path),
    )
    assert result["ok"] is True
    assert result["workspace_root"] == str(tmp_path.resolve())


def test_architecture_clone_repo_prefers_injected_token_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: env token set alongside an injected (configured-source) token
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    clone_mock = MagicMock(side_effect=WorkspaceError("stop before network"))
    monkeypatch.setattr(
        "integrations.github.tools.architecture_issue_tool.tool.clone_github_repo", clone_mock
    )

    # Act
    architecture_clone_repo(
        github_connection_origin="webapp", owner="org", repo="repo", github_token="store-token"
    )

    # Assert: the configured-source token reaches the clone, not the env token
    assert clone_mock.call_args.kwargs["token"] == "store-token"


def test_architecture_clone_repo_env_token_does_not_allow_a_clone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    clone_mock = MagicMock(side_effect=AssertionError("unauthorized clone"))
    monkeypatch.setattr(
        "integrations.github.tools.architecture_issue_tool.tool.clone_github_repo", clone_mock
    )
    result = architecture_clone_repo(owner="org", repo="repo")
    assert result["work_outcome"]["status"] == "blocked"
    clone_mock.assert_not_called()


def test_architecture_cleanup_refuses_outside_path(tmp_path: Path) -> None:
    result = architecture_cleanup_repo(
        github_connection_origin="webapp", github_token="app-token", workspace_root=str(tmp_path)
    )
    assert result["ok"] is False
    assert "not an audit directory" in result["error"]


def test_sanitize_repo_name() -> None:
    assert sanitize_repo_name("Tracer-Cloud/opensre") == "Tracer-Cloud-opensre"
    assert sanitize_repo_name("  ") == "repo"


def test_save_architecture_observations_writes_markdown(tmp_path: Path) -> None:
    path = save_architecture_observations(
        session_id="sess-123",
        repo_name="opensre",
        observations="- import: core -> surfaces\n- size: big.py (900)",
        audit_id="abcd1234",
        home_dir=tmp_path,
    )
    assert path == tmp_path / "sess-123" / "opensre-architecture-audit-abcd1234.md"
    text = path.read_text(encoding="utf-8")
    assert "# Architecture audit observations" in text
    assert "sess-123" in text
    assert "- import: core -> surfaces" in text
    assert "- size: big.py (900)" in text


def test_save_architecture_observations_rejects_empty(tmp_path: Path) -> None:
    try:
        save_architecture_observations(
            session_id="sess-123",
            repo_name="opensre",
            observations="   ",
            home_dir=tmp_path,
        )
    except ReportPersistenceError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("expected ReportPersistenceError")


def test_architecture_save_observations_tool_uses_explicit_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "integrations.github.tools.architecture_issue_tool.tool.save_architecture_observations",
        partial(save_architecture_observations, home_dir=tmp_path),
    )
    result = architecture_save_observations(
        github_connection_origin="webapp",
        github_token="app-token",
        repo_name="opensre",
        observations="- placement: tools under wrong tree",
        session_id="ae4c7934-747e-4ffc-9f62-3143cd1ad5af",
    )
    assert result["ok"] is True
    saved = Path(result["path"])
    assert saved.exists()
    assert saved.parent.name == "ae4c7934-747e-4ffc-9f62-3143cd1ad5af"
    assert saved.name.startswith("opensre-architecture-audit-")
    assert saved.suffix == ".md"


def test_architecture_save_observations_tool_reads_session_from_context(
    tmp_path: Path, monkeypatch
) -> None:
    from core.agent_harness.tools.tool_context import (
        ACTION_TOOL_CONTEXT_RESOURCE_KEY,
        ActionToolScope,
    )
    from core.tool.contracts import AgentToolContext

    monkeypatch.setattr(
        "integrations.github.tools.architecture_issue_tool.tool.save_architecture_observations",
        partial(save_architecture_observations, home_dir=tmp_path),
    )
    session = SimpleNamespace(session_id="ctx-session-id")
    context = AgentToolContext(
        resolved_integrations={},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(
                session=session,
                console=SimpleNamespace(),
            )
        },
    )
    result = architecture_save_observations(
        github_connection_origin="webapp",
        github_token="app-token",
        repo_name="envoy",
        observations="- size: abi.h (14255)",
        context=context,
    )
    assert result["ok"] is True
    assert result["session_id"] == "ctx-session-id"
    assert Path(result["path"]).exists()


class _CancelledTurnConsole:
    """A console whose turn was cancelled; counts how often the tool asked."""

    def __init__(self) -> None:
        self.reads = 0

    @property
    def cancel_requested(self) -> bool:
        self.reads += 1
        return True


def test_architecture_clone_waiting_on_a_full_heavy_work_gate_ends_with_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled turn must not sit out the slot wait; the tool still answers with an error."""
    from config.constants.turn_concurrency import OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV
    from core.agent_harness.tools.tool_context import (
        ACTION_TOOL_CONTEXT_RESOURCE_KEY,
        ActionToolScope,
    )
    from core.tool.contracts import AgentToolContext
    from infrastructure.process.turn_capacity import (
        HEAVY_WORK_BUSY_MESSAGE,
        process_heavy_work_gate,
        reset_process_heavy_work_gate_for_tests,
    )
    from infrastructure.process.turn_capacity import heavy_work as heavy_work_module
    from integrations.github.tools.architecture_issue_tool import repo_workspace

    # Arrange: both slots held, a long wait, and a turn that has been cancelled.
    monkeypatch.setenv(OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV, "2")
    monkeypatch.setattr(heavy_work_module, "HEAVY_WORK_WAIT_SECONDS", 30.0)
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(repo_workspace, "architecture_workspace_dir", lambda: workspace)

    def _no_clone(**_kwargs: object) -> None:
        pytest.fail("cloned without a heavy-work slot")

    monkeypatch.setattr(repo_workspace, "_shallow_clone", _no_clone)
    console = _CancelledTurnConsole()
    context = AgentToolContext(
        resolved_integrations={},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(
                session=SimpleNamespace(), console=console
            )
        },
    )
    reset_process_heavy_work_gate_for_tests()
    gate = process_heavy_work_gate()
    assert gate.try_acquire() and gate.try_acquire()

    try:
        # Act
        result = architecture_clone_repo(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="org",
            repo="repo",
            ref="main",
            context=context,
        )
    finally:
        reset_process_heavy_work_gate_for_tests()

    # Assert: the tool's error result, the cancel flag was consulted, and no audit dir is left.
    assert result["ok"] is False
    assert result["error"] == HEAVY_WORK_BUSY_MESSAGE
    assert console.reads > 0
    assert list(workspace.iterdir()) == []
