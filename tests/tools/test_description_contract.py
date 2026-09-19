"""Description contract for every registered tool.

A tool's description is the model's primary signal for choosing it among the
~290 tools offered together. This suite pins a minimum quality bar so a tool
cannot enter the registry with an unusable description.

Existing violators are quarantined in a shrink-only allowlist (issue #5498).
The allowlist is compared exactly against the live violator set: a tool that
gets fixed MUST be removed (the ratchet test fails on a stale entry), and a
new violator fails immediately. Rewriting descriptions is explicitly out of
scope — the allowlist is the measured backlog.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from core.tool import RegisteredTool
from tools import registry as registry_module

# Mirrors tools/registry_skill_guidance.py:_MAX_TOOL_SKILL_GUIDANCE_CHARS.
_MAX_TOOL_SKILL_GUIDANCE_CHARS = 2400

# A description shorter than this gives the model too little signal to pick the
# tool among ~290 siblings. The shortest live description is 31 chars, so this
# floor catches only genuinely stub descriptions.
_MIN_DESCRIPTION_CHARS = 20

# Placeholders a developer leaves when the real description is not yet written.
# Anchored at the start so legitimate mid-sentence uses (e.g. "a todo list
# tool", "placeholder quoted text") are not false-flagged.
_PLACEHOLDER_RE = re.compile(
    r"^(TODO|TBD|FIXME|description here|placeholder|lorem ipsum)\b",
    re.IGNORECASE,
)

# Credential shapes that must never ship in a tool description (visible to the
# model and to transcript readers). Only high-confidence token shapes are
# matched so a legitimate description is not blocked by a short lookalike.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|gh[pousr]_[A-Za-z0-9]{36,})",
    re.IGNORECASE,
)

# Today's production tool count. Discovery swallows import errors, so a silently
# short registry would make every per-tool check pass by testing nothing. This
# floor fails loud when the registry shrinks; raise it when the registry grows.
_MIN_REGISTRY_SIZE = 265

#: Tools that currently violate at least one description-contract rule.
#: Compared exactly against the live violator set — a fixed tool MUST be removed
#: (the ratchet test fails on a stale entry), and a new violator MUST be fixed,
#: never appended. Each entry is per-integration backlog work tracked under
#: issue #5498; rewriting descriptions is out of scope for that issue.
_DESCRIPTION_CONTRACT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "cli_exec",
        "code_implement",
        "fix_sentry_issue_start",
        "get_dagster_run_logs",
        "get_mariadb_global_status",
        "get_mariadb_innodb_status",
        "get_mariadb_process_list",
        "get_mariadb_replication_status",
        "get_mariadb_slow_queries",
        "get_mongodb_atlas_alerts",
        "get_mongodb_atlas_cluster_events",
        "get_mongodb_atlas_cluster_metrics",
        "get_mongodb_atlas_clusters",
        "get_mongodb_atlas_performance_advisor",
        "get_mongodb_collection_stats",
        "get_mongodb_current_ops",
        "get_mongodb_profiler_data",
        "get_mongodb_replica_status",
        "get_mongodb_server_status",
        "inspect_railway_deployment",
        "list_dagster_assets",
        "list_dagster_runs",
        "list_dagster_schedule_ticks",
        "list_dagster_sensor_ticks",
        "llm_set_provider",
        "redeploy_railway_service",
        "replay_slack_thread_locally",
        "shell_run",
        "skill_view",
        "slash_invoke",
        "task_cancel",
    }
)


def _production_tool_names() -> tuple[str, ...]:
    """Every tool name in the production registry (external packages excluded).

    Other tests register bench/external tool packages as a side effect; those
    extra tools would inflate the parametrize set and break the ratchet. Snapshot
    the production registry in isolation so the IDs cannot go stale.
    """
    saved = list(registry_module._external_tool_packages)
    registry_module._external_tool_packages.clear()
    registry_module.clear_tool_registry_cache()
    try:
        return tuple(tool.name for tool in registry_module._load_registry_snapshot())
    finally:
        registry_module._external_tool_packages[:] = saved
        registry_module.clear_tool_registry_cache()


@pytest.fixture(autouse=True)
def _clean_production_registry() -> Iterator[None]:
    """Pin a production-only registry around each check.

    Bench and external tool packages registered by other tests would widen the
    violator set (breaking the ratchet) or shift the floor count. Each check
    reads a production-only snapshot so the result is stable regardless of test
    run order.
    """
    saved = list(registry_module._external_tool_packages)
    registry_module._external_tool_packages.clear()
    registry_module.clear_tool_registry_cache()
    yield
    registry_module._external_tool_packages[:] = saved
    registry_module.clear_tool_registry_cache()


def _sources_with_siblings(tools: tuple[RegisteredTool, ...]) -> frozenset[str]:
    """Source values that back more than one tool — those tools are siblings.

    When a source has siblings the model needs ``use_cases`` to disambiguate
    which one to pick, so a single-tool source is exempt from that rule.
    """
    counts: dict[str, int] = {}
    for tool in tools:
        counts[str(tool.source)] = counts.get(str(tool.source), 0) + 1
    return frozenset(src for src, count in counts.items() if count > 1)


def _description_contract_violations(
    tool: RegisteredTool,
    *,
    sibling_sources: frozenset[str],
) -> list[str]:
    """Human-readable rule violations for one tool; empty list means clean."""
    reasons: list[str] = []
    description = (tool.description or "").strip()

    if not description:
        reasons.append("description is empty")
    elif len(description) < _MIN_DESCRIPTION_CHARS:
        reasons.append(
            f"description is only {len(description)} chars (minimum {_MIN_DESCRIPTION_CHARS})"
        )

    if _PLACEHOLDER_RE.search(description):
        reasons.append("description starts with placeholder text (TODO/TBD/FIXME)")

    if _SECRET_RE.search(description):
        reasons.append("description may contain a secret or credential")

    if str(tool.source) in sibling_sources and not tool.use_cases:
        reasons.append("use_cases is empty despite siblings in the same source")

    guidance = tool.skill_guidance or ""
    if len(guidance) > _MAX_TOOL_SKILL_GUIDANCE_CHARS:
        reasons.append(
            f"skill_guidance is {len(guidance)} chars (budget {_MAX_TOOL_SKILL_GUIDANCE_CHARS})"
        )

    return reasons


def test_description_contract_registry_floor() -> None:
    snapshot = registry_module._load_registry_snapshot()
    assert len(snapshot) >= _MIN_REGISTRY_SIZE, (
        f"registry has {len(snapshot)} tools, expected at least "
        f"{_MIN_REGISTRY_SIZE}. Discovery swallows import errors, so a short "
        f"registry makes the per-tool checks pass by testing nothing."
    )


@pytest.mark.parametrize("tool_name", _production_tool_names())
def test_description_contract(tool_name: str) -> None:
    snapshot = registry_module._load_registry_snapshot()
    tools_by_name = {tool.name: tool for tool in snapshot}
    tool = tools_by_name[tool_name]
    sibling_sources = _sources_with_siblings(snapshot)
    violations = _description_contract_violations(tool, sibling_sources=sibling_sources)

    if not violations:
        return  # clean

    if tool.name in _DESCRIPTION_CONTRACT_ALLOWLIST:
        pytest.skip(f"{tool.name} quarantined under #5498: {'; '.join(violations)}")

    pytest.fail(
        f"{tool.name} violates the description contract: "
        f"{'; '.join(violations)}. Fix the description, or quarantine the tool "
        f"in _DESCRIPTION_CONTRACT_ALLOWLIST with a tracking ticket."
    )


def test_description_contract_allowlist_ratchets() -> None:
    snapshot = registry_module._load_registry_snapshot()
    sibling_sources = _sources_with_siblings(snapshot)
    violators = {
        tool.name
        for tool in snapshot
        if _description_contract_violations(tool, sibling_sources=sibling_sources)
    }

    allowlist = _DESCRIPTION_CONTRACT_ALLOWLIST
    new_violators = sorted(violators - allowlist)
    stale_entries = sorted(allowlist - violators)

    assert not new_violators, (
        f"new description-contract violators not in the allowlist: "
        f"{new_violators}. Fix the description, or quarantine the tool in "
        f"_DESCRIPTION_CONTRACT_ALLOWLIST with a tracking ticket."
    )
    assert not stale_entries, (
        f"allowlist entries that no longer violate (stale — remove them so the "
        f"allowlist keeps shrinking): {stale_entries}."
    )
