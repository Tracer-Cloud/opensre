"""Skill helpers follow live activation and keep subprocess failures visible."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import core.agent_harness.prompts.skills as skills
from core.agent_harness.ports import TurnBinding
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    InMemorySessionState,
)
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.llm.types import AgentLLMResponse
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
    tool_response,
)
from tests.utils.skill_cards import skill_card
from tools.interactive_shell.actions.skill_view import skill_view_tool
from tools.interactive_shell.actions.update_plan import update_plan_tool
from tools.interactive_shell.skill_scripts.catalog import registered_skill_tools
from tools.interactive_shell.skill_scripts.runner import run_skill_script


@dataclass
class _Session(InMemorySessionState):
    active_skill: str | None = None


@dataclass
class _SchemaLLM(FakeActionLLM):
    requests: list[set[str]] = field(default_factory=list)

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        self.requests.append({tool["name"] for tool in tools or []})
        return super().invoke(messages, system=system, tools=tools)


@pytest.fixture
def helper_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    directory = tmp_path / "testing-helpers"
    scripts = directory / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "inspect_demo.py"
    helper.write_text(
        'import json\nprint(json.dumps({"ok": True, "summary": "helper completed"}))\n'
    )
    (directory / "SKILL.md").write_text(
        skill_card("testing-helpers", script_tools="references/script-tools.md")
    )
    references = directory / "references"
    references.mkdir()
    definitions = skill_card(
        "testing-helpers",
        script_tools=[
            {
                "name": "inspect_demo",
                "script": helper.name,
                "description": "Inspect the fixture.",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            }
        ],
    )
    frontmatter, _ = skills.parse_frontmatter(definitions)
    import yaml

    (references / "script-tools.md").write_text(
        "---\n"
        + yaml.safe_dump({"script_tools": frontmatter["script_tools"]})
        + "---\nHelper tools.\n"
    )
    (tmp_path / "another-skill.md").write_text(skill_card("another-skill"))
    monkeypatch.setattr(
        "core.agent_harness.prompts.skills.content.files.skills_dir", lambda: tmp_path
    )
    skills.clear_skills_caches()
    registered_skill_tools.cache_clear()
    yield helper
    skills.clear_skills_caches()
    registered_skill_tools.cache_clear()


def test_real_loop_refreshes_helpers_on_entry_and_switch(helper_skill: Path) -> None:
    session = _Session()
    output = BufferOutputSink()
    llm = _SchemaLLM(
        [
            tool_response("skill_view", {"name": "testing-helpers"}),
            tool_response("inspect_demo"),
            tool_response("skill_view", {"name": "another-skill"}),
            no_tool_response("Finished."),
        ]
    )
    provider = DefaultToolProvider(session, output, precomputed_action_tools=[skill_view_tool])
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    agent.handle("Run the helper fixture.", TurnBinding(is_tty=False))
    assert len(llm.requests) == 4
    assert "inspect_demo" not in llm.requests[0]
    assert "inspect_demo" in llm.requests[1]
    assert "inspect_demo" not in llm.requests[3]
    assert session.active_skill == "another-skill"
    assert "Could not start" not in output.text
    assert str(helper_skill) not in output.text


def test_helpers_are_session_scoped_and_blocked_without_shell(helper_skill: Path) -> None:
    active = _Session(active_skill="testing-helpers")
    other = _Session()
    output = BufferOutputSink()
    provider = DefaultToolProvider(active, output, precomputed_action_tools=[])
    assert {tool.name for tool in provider.action_tools(confirm_fn=None, is_tty=False)} == {
        "inspect_demo"
    }
    assert (
        DefaultToolProvider(other, output, precomputed_action_tools=[]).action_tools(
            confirm_fn=None, is_tty=False
        )
        == []
    )
    active.available_capabilities = {"shell_commands": ()}
    result = run_skill_script(
        skill_name="testing-helpers",
        tool_name="inspect_demo",
        script_path=str(helper_skill),
        arguments={},
        scope=ActionToolScope(session=active, console=output),
    )
    assert result["ok"] is False
    assert "unavailable" in output.text
    active.available_capabilities = {}
    assert (
        DefaultToolProvider(
            active, output, precomputed_action_tools=[], unattended=True
        ).action_tools(confirm_fn=None, is_tty=False)
        == []
    )


def test_completed_plan_retires_helpers_in_the_running_loop(helper_skill: Path) -> None:
    session = _Session()
    output = BufferOutputSink()

    def plan(first: str, second: str) -> AgentLLMResponse:
        return tool_response(
            "update_plan",
            {
                "plan": [
                    {"step": "Run the helper", "status": first},
                    {"step": "Read its reference", "status": second},
                ]
            },
        )

    llm = _SchemaLLM(
        [
            tool_response("skill_view", {"name": "testing-helpers"}),
            plan("in_progress", "pending"),
            tool_response("inspect_demo"),
            plan("completed", "in_progress"),
            tool_response("skill_view", {"name": "testing-helpers", "reference": "script-tools"}),
            plan("completed", "completed"),
            no_tool_response("Finished."),
        ]
    )
    provider = DefaultToolProvider(
        session, output, precomputed_action_tools=[skill_view_tool, update_plan_tool]
    )
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=provider,
        prompts=EmptyPromptContextProvider(),
        llm_factory=lambda: llm,
    )
    agent.handle("Run and verify the helper.", TurnBinding(is_tty=False))
    assert session.active_skill is None
    assert "inspect_demo" in llm.requests[-2]
    assert "inspect_demo" not in llm.requests[-1]


def test_helper_failure_is_visible_without_dumping_diagnostics(helper_skill: Path) -> None:
    helper_skill.write_text(
        'import json\nprint(json.dumps({"ok": False, "error": "failed", "diagnostics": "verbose git output"}))\nraise SystemExit(1)\n'
    )
    output = BufferOutputSink()
    result = run_skill_script(
        skill_name="testing-helpers",
        tool_name="inspect_demo",
        script_path=str(helper_skill),
        arguments={},
        scope=ActionToolScope(session=_Session(active_skill="testing-helpers"), console=output),
    )
    assert result["ok"] is False
    assert result["diagnostics"] == "verbose git output"
    assert "inspect demo" in output.text and "failed" in output.text
    assert "verbose git output" not in output.text


def test_cancelled_helper_does_not_start(helper_skill: Path) -> None:
    output = BufferOutputSink()
    event = threading.Event()
    event.set()
    output.cancel_event = event  # type: ignore[attr-defined]
    marker = helper_skill.parent / "started"
    helper_skill.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    result = run_skill_script(
        skill_name="testing-helpers",
        tool_name="inspect_demo",
        script_path=str(helper_skill),
        arguments={},
        scope=ActionToolScope(session=_Session(active_skill="testing-helpers"), console=output),
    )
    assert result["cancelled"] is True
    assert not marker.exists()


def test_loader_rejects_script_escape(helper_skill: Path) -> None:
    external = helper_skill.parent.parent.parent / "external.py"
    helper_skill.unlink()
    external.write_text("raise RuntimeError('must not load')\n")
    helper_skill.symlink_to(external)
    catalog = skills.read_skill_catalog()
    assert "testing-helpers" not in {skill.name for skill in catalog.skills}
    assert any("linked script" in diagnostic for diagnostic in catalog.diagnostics)


def test_interrupted_watcher_reaps_the_helper(
    helper_skill: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper_skill.write_text("import time\ntime.sleep(60)\n")
    started: list[subprocess.Popen[Any]] = []
    popen = subprocess.Popen

    def track_process(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = popen(*args, **kwargs)
        started.append(process)
        return process

    def interrupt_watcher(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("watcher interrupted")

    monkeypatch.setattr(subprocess, "Popen", track_process)
    monkeypatch.setattr(
        "tools.interactive_shell.skill_scripts.runner.watch_subprocess_until_exit",
        interrupt_watcher,
    )
    output = BufferOutputSink()
    result = run_skill_script(
        skill_name="testing-helpers",
        tool_name="inspect_demo",
        script_path=str(helper_skill),
        arguments={},
        scope=ActionToolScope(session=_Session(active_skill="testing-helpers"), console=output),
    )
    assert result["ok"] is False
    assert "could not finish" in output.text
    assert len(started) == 1
    assert started[0].poll() is not None
