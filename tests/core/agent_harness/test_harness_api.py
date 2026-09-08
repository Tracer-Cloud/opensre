"""The exported names of each harness API module are pinned exactly.

* ``core.agent_harness`` — embedder API: entry point, config, session, results, sink.
* ``core.agent_harness.ports`` — what a host implements: the port protocols.
* ``core.agent_harness.spi.<role>`` — what a host calls around a turn, by role;
  the package itself exports nothing. Import-cheap.
* ``core.agent_harness.runtime`` — build and run the agent; loads ``core.agent``.
* ``core.agent_harness.tools`` — what an action tool implements and calls; import-cheap.

Adding a name to any API module is an API change and is made here.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import core.agent_harness as root
import core.agent_harness.ports as ports
import core.agent_harness.runtime as runtime
import core.agent_harness.spi as spi_pkg
import core.agent_harness.tools as tools
from tests.shared.harness_api import API_MODULES, SPI_ROLES

ROOT_API = frozenset(
    {
        "AgentSession",
        "OutputSink",
        "SessionConfig",
        "SessionCore",
        "SessionManager",
        "ToolCallingTurnResult",
        "TurnResult",
        "is_recurring_skill",
        "normalize_skill_name",
        "pin_recurring_skill",
        "resolve_scheduled_skill",
        "validate_skill_inputs",
    }
)

PORTS = frozenset(
    {
        "CancelCapableConsole",
        "ConfirmFn",
        "ConsoleBindable",
        "ErrorReporter",
        "ExecuteActions",
        "GatheredEvidence",
        "LlmFactory",
        "LlmProviderPortsFactory",
        "OutputBindable",
        "OutputSink",
        "PromptContextProvider",
        "SessionBindable",
        "SessionState",
        "SlashPortsFactory",
        "SubprocessPresenterFactory",
        "TaskCancelPortsFactory",
        "ToolEventObserver",
        "ToolProvider",
        "TurnAccounting",
        "TurnBinding",
    }
)

SPI_ROLE_NAMES: dict[str, frozenset[str]] = {
    "session_goal": frozenset(
        {
            "MAX_GOAL_CONDITION_CHARS",
            "SessionGoal",
            "SessionGoalReason",
            "SessionGoalStatus",
            "attach_session_goal",
            "build_session_goal",
            "clear_session_goal",
            "format_session_goal_progress",
            "format_session_goal_status_line",
            "run_until_session_goal",
            "session_goal_is_active",
            "session_goal_is_attached",
            "session_goal_is_paused",
            "strip_session_goal_progress_tags",
        }
    ),
    "session_state": frozenset(
        {
            "PendingScheduleOffer",
            "PendingUserChoice",
            "clear_competing_pending_offers",
            "clear_pending_autosubmit",
            "compact_session_branch",
            "exclusive_stdin_active",
            "format_recovery_note",
            "pop_turn_outcome_hint",
            "session_terminal",
            "set_auto_command",
            "set_turn_outcome_hint",
            "trust_mode_enabled",
            "withhold_capabilities",
        }
    ),
    "cancel": frozenset({"ensure_turn_cancel", "host_cancel_requested"}),
    "accounting": frozenset(
        {
            "DefaultTurnAccounting",
            "LlmRunInfo",
            "SELF_RECORDING_ACTION_TOOL_NAMES",
            "ToolCallingAccountingStatus",
            "format_token_total",
            "record_llm_turn",
            "resolve_model_name",
            "resolve_provider_name",
        }
    ),
    "prompt_chrome": frozenset(
        {
            "COHORT_IDENTITY_UNVERIFIED_MARK",
            "WANT_ME_TO_MARKER",
            "closer_tail_from",
            "normalize_three_tier_spacing",
            "reply_reports_cohort_unverified",
            "strip_shell_prompt_chrome",
        }
    ),
    "integrations": frozenset(
        {
            "has_resolved_integrations",
            "merge_resolved_integrations",
            "resolve_and_cache_integrations",
            "resolve_integrations",
        }
    ),
    "grounding": frozenset(
        {
            "CacheStats",
            "GroundingSource",
            "list_action_skills",
            "load_skill_body",
            "log_grounding_cache_diagnostics",
        }
    ),
    "defaults": frozenset(
        {
            "DefaultErrorReporter",
            "DefaultPromptContextProvider",
            "JsonlSessionStore",
            "default_session_repo",
            "default_session_store",
            "sessions_dir",
        }
    ),
    "handoff": frozenset(
        {
            "AskUserQuestion",
            "format_ask_user_answers",
            "parse_ask_user_answers",
        }
    ),
    "task_plan": frozenset(
        {
            "PLAN_STATUS_GLYPH",
            "PlanStep",
            "PlanStepStatus",
            "TaskPlan",
            "apply_update_plan_host_policy",
            "apply_update_plan_session",
            "ensure_active_step",
            "format_plan_header",
            "format_task_plan_plain",
            "is_plan_diagnosis_prose",
            "parse_task_plan",
            "promote_first_pending_step",
            "record_task_plan_work",
            "take_completed_plan_breakdown",
            "task_plan_to_payload",
        }
    ),
}

RUNTIME = frozenset(
    {
        "ActionTurnRunner",
        "AgentBuildConfig",
        "AgentBusyError",
        "AgentConfig",
        "DefaultHeadlessBuild",
        "DescribeTool",
        "DefaultToolProvider",
        "HeadlessAgent",
        "InMemoryHeadlessBuild",
        "TurnBinding",
        "TurnPlan",
        "agent_llm_is_cli_backed",
        "build_agent",
        "default_llm_factory",
        "default_reasoning_llm_factory",
        "resolve_agent_ports",
    }
)

TOOLS = frozenset(
    {
        "ActionToolScope",
        "action_context_from_agent_context",
        "action_scope_from_agent_context",
        "capability_available_from_sources",
        "coerce_gathered_evidence",
        "execute_with_action_context",
    }
)

#: Names that are both root API and port contracts; exported from both on purpose.
API_PORTS_OVERLAP = frozenset({"OutputSink"})
#: TurnBinding is both a runtime value a host builds and a port contract.
RUNTIME_PORTS_OVERLAP = frozenset({"TurnBinding"})


def test_root_exports_exactly_its_list() -> None:
    assert set(root.__all__) == ROOT_API


def test_ports_exports_exactly_its_list() -> None:
    assert set(ports.__all__) == PORTS


def test_each_spi_role_exports_exactly_its_list() -> None:
    for role, names in SPI_ROLE_NAMES.items():
        module = importlib.import_module(f"core.agent_harness.spi.{role}")
        assert set(module.__all__) == names, role


def test_the_spi_package_itself_exports_nothing() -> None:
    """Hosts import a role, never the grab bag."""
    assert not hasattr(spi_pkg, "__all__")
    assert not any(hasattr(spi_pkg, n) for names in SPI_ROLE_NAMES.values() for n in names)


def test_runtime_exports_exactly_its_list() -> None:
    assert set(runtime.__all__) == RUNTIME


def test_tools_exports_exactly_its_list() -> None:
    assert set(tools.__all__) == TOOLS


def test_api_modules_do_not_overlap_except_where_stated() -> None:
    all_spi = frozenset().union(*SPI_ROLE_NAMES.values())
    assert ROOT_API & PORTS == API_PORTS_OVERLAP
    assert RUNTIME & PORTS == RUNTIME_PORTS_OVERLAP
    assert not (ROOT_API & all_spi)
    assert not (ROOT_API & RUNTIME)
    assert not (all_spi & RUNTIME)
    assert not (all_spi & PORTS)
    assert not (TOOLS & (ROOT_API | PORTS | RUNTIME | all_spi))
    roles = list(SPI_ROLE_NAMES.values())
    for i, a in enumerate(roles):
        for b in roles[i + 1 :]:
            assert not (a & b)


def test_every_pinned_name_resolves() -> None:
    for name in ROOT_API:
        assert getattr(root, name) is not None
    for name in PORTS:
        assert getattr(ports, name) is not None
    for role, names in SPI_ROLE_NAMES.items():
        module = importlib.import_module(f"core.agent_harness.spi.{role}")
        for name in names:
            assert getattr(module, name) is not None
    for name in RUNTIME:
        assert getattr(runtime, name) is not None
    for name in TOOLS:
        assert getattr(tools, name) is not None


def test_border_tests_and_this_pin_share_one_api_module_list() -> None:
    """The border tests allow exactly the API modules pinned here."""
    assert frozenset(SPI_ROLE_NAMES) == SPI_ROLES
    assert {
        "core.agent_harness",
        "core.agent_harness.ports",
        "core.agent_harness.runtime",
        "core.agent_harness.tools",
        *(f"core.agent_harness.spi.{r}" for r in SPI_ROLE_NAMES),
    } == API_MODULES


def test_the_root_has_no_lazy_attribute_machinery() -> None:
    assert not hasattr(root, "_LAZY_EXPORTS")
    assert "__getattr__" not in vars(root)


def test_root_ports_and_spi_do_not_load_the_agent_loop() -> None:
    roles = ", ".join(f"core.agent_harness.spi.{r}" for r in SPI_ROLE_NAMES)
    code = (
        f"import sys, core.agent_harness, core.agent_harness.ports, core.agent_harness.tools, {roles}; "
        "print('core.agent.agent' in sys.modules, "
        "'core.agent_harness.turns.action_driver' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"], out.stdout
