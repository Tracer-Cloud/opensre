from __future__ import annotations

import inspect
import logging
from collections.abc import Generator
from types import ModuleType
from typing import Any

import pytest

from core.domain.types.retrieval import RetrievalControls
from core.domain.types.tools import ToolSurface
from core.tool.contracts import REGISTERED_TOOL_ATTR, BaseTool, RegisteredTool
from core.tool_framework.tool_decorator import tool
from tools import registry as registry_module
from tools import registry_discovery

_V2_TOOL_CONTRACT_NAMES = frozenset(
    {
        "query_grafana_metrics",
        "describe_rds_instance",
        "get_postgresql_slow_queries",
        "query_datadog_metrics",
        "list_eks_pods",
    }
)


@pytest.fixture(autouse=True)
def _reset_registry_cache() -> Generator[None]:
    registry_module.clear_tool_registry_cache()
    yield
    registry_module.clear_tool_registry_cache()


def test_tool_decorator_registers_function_tool_with_inferred_schema() -> None:
    module: Any = ModuleType("tools.fake_function_tool")

    @tool(
        name="lookup_incident",
        description="Lookup incident metadata.",
        display_name="Incident metadata",
        source="knowledge",
        surfaces=("action", "chat"),
    )
    def lookup_incident(incident_id: str, limit: int = 10) -> dict[str, object]:
        return {"incident_id": incident_id, "limit": limit}

    lookup_incident.__module__ = module.__name__
    module.lookup_incident = lookup_incident

    tools = registry_discovery.collect_registered_tools_from_module(module)

    assert [tool_def.name for tool_def in tools] == ["lookup_incident"]
    registered = tools[0]
    assert registered.input_schema["properties"]["incident_id"]["type"] == "string"
    assert registered.input_schema["properties"]["limit"]["type"] == "integer"
    assert registered.display_name == "Incident metadata"
    assert registered.input_schema["required"] == ["incident_id"]
    assert registered.surfaces == ("action", "chat")


def test_tool_decorator_supports_minimal_single_file_function_tool() -> None:
    module: Any = ModuleType("tools.single_file_status_tool")

    @tool(source="knowledge")
    def check_status(run_id: str, include_history: bool = False) -> dict[str, object]:
        """Check status for a run."""
        return {"run_id": run_id, "include_history": include_history}

    check_status.__module__ = module.__name__
    module.check_status = check_status

    tools = registry_discovery.collect_registered_tools_from_module(module)

    assert [tool_def.name for tool_def in tools] == ["check_status"]
    registered = tools[0]
    assert registered.description == "Check status for a run."
    assert registered.source == "knowledge"
    assert registered.input_schema["properties"]["run_id"]["type"] == "string"
    assert registered.input_schema["properties"]["include_history"]["type"] == "boolean"
    assert registered.input_schema["required"] == ["run_id"]
    assert registered.surfaces == ("chat",)
    assert registered.run(run_id="r-1", include_history=True) == {
        "run_id": "r-1",
        "include_history": True,
    }


def test_function_and_class_tools_share_the_same_runtime_contract() -> None:
    def _available(sources: dict[str, dict[str, str]]) -> bool:
        return bool(sources.get("knowledge"))

    def _extract(sources: dict[str, dict[str, str]]) -> dict[str, str]:
        return {"incident_id": sources["knowledge"]["incident_id"]}

    @tool(
        name="lookup_incident_function",
        description="Lookup incident metadata.",
        source="knowledge",
        input_schema={
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        },
        surfaces=("action", "chat"),
        is_available=_available,
        extract_params=_extract,
        outputs={"incident_id": "Incident identifier"},
    )
    def lookup_incident_function(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    class LookupIncidentClassTool(BaseTool):
        name = "lookup_incident_class"
        description = "Lookup incident metadata."
        source = "knowledge"
        surfaces = ("action", "chat")
        input_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        }
        outputs = {"incident_id": "Incident identifier"}

        def is_available(self, sources: dict[str, dict[str, str]]) -> bool:
            return _available(sources)

        def extract_params(self, sources: dict[str, dict[str, str]]) -> dict[str, str]:
            return _extract(sources)

        def run(self, incident_id: str) -> dict[str, str]:
            return {"incident_id": incident_id}

    function_tool = getattr(lookup_incident_function, REGISTERED_TOOL_ATTR)
    assert isinstance(function_tool, RegisteredTool)

    class_tool = RegisteredTool.from_base_tool(LookupIncidentClassTool())
    sources = {"knowledge": {"incident_id": "inc-123"}}

    assert function_tool.inputs == class_tool.inputs
    assert function_tool.extract_params(sources) == class_tool.extract_params(sources)
    assert function_tool.is_available(sources) is class_tool.is_available(sources)
    assert function_tool.run(**function_tool.extract_params(sources)) == class_tool.run(
        **class_tool.extract_params(sources)
    )
    assert function_tool.surfaces == class_tool.surfaces


def test_tool_decorator_allows_retrieval_controls_override_for_base_tool() -> None:
    class LookupIncidentClassTool(BaseTool):
        name = "lookup_incident_class"
        description = "Lookup incident metadata."
        source = "knowledge"
        surfaces = ("action", "chat")
        retrieval_controls = RetrievalControls(limit=True)
        input_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        }

        def run(self, incident_id: str) -> dict[str, str]:
            return {"incident_id": incident_id}

    class_tool = tool(
        LookupIncidentClassTool(),
        retrieval_controls=RetrievalControls(time_bounds=True, filters=True),
    )
    registered = getattr(class_tool, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.retrieval_controls.time_bounds
    assert registered.retrieval_controls.filters
    assert not registered.retrieval_controls.limit


def test_tool_decorator_preserves_tags_for_base_tool_instances() -> None:
    class LookupIncidentClassTool(BaseTool):
        name = "lookup_incident_class"
        description = "Lookup incident metadata."
        source = "knowledge"
        input_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Incident identifier",
                },
            },
            "required": ["incident_id"],
        }

        def run(self, incident_id: str) -> dict[str, str]:
            return {"incident_id": incident_id}

    decorated = tool(
        LookupIncidentClassTool(),
        tags=("safe", "fast"),
    )

    registered = getattr(decorated, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.tags == ("safe", "fast")


def test_auto_discovery_populates_action_and_chat_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: Any = ModuleType("tools.fake_discovered_tool")

    @tool(
        name="get_incident_metadata",
        description="Return normalized incident metadata.",
        source="knowledge",
        surfaces=("action", "chat"),
    )
    def get_incident_metadata(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    get_incident_metadata.__module__ = module.__name__
    module.get_incident_metadata = get_incident_metadata

    monkeypatch.setattr(
        registry_discovery, "_iter_tool_module_names", lambda _pkg: ["fake_discovered_tool"]
    )
    monkeypatch.setattr(registry_module, "_external_tool_packages", [])
    monkeypatch.setattr(registry_module, "INTEGRATION_TOOL_PACKAGES", ())
    monkeypatch.setattr(registry_discovery, "_import_tool_module", lambda _pkg, _name: module)

    # Surface-scoped loads read the on-disk descriptor index; assert surface
    # assignment on the mocked full snapshot instead.
    snapshot = registry_module.get_registered_tools()
    assert [t.name for t in snapshot if "action" in (t.surfaces or ())] == ["get_incident_metadata"]
    assert [t.name for t in snapshot if "chat" in (t.surfaces or ())] == ["get_incident_metadata"]
    assert registry_module.get_registered_tool_map()["get_incident_metadata"].run("inc-1") == {
        "incident_id": "inc-1"
    }


def test_action_surface_is_filtered_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    module: Any = ModuleType("tools.fake_action_tool")

    @tool(
        name="perform_action",
        description="Perform a test action.",
        source="knowledge",
        surfaces=("action",),
    )
    def perform_action(value: str) -> dict[str, str]:
        return {"value": value}

    perform_action.__module__ = module.__name__
    module.perform_action = perform_action

    monkeypatch.setattr(
        registry_discovery, "_iter_tool_module_names", lambda _pkg: ["fake_action_tool"]
    )
    monkeypatch.setattr(registry_module, "_external_tool_packages", [])
    monkeypatch.setattr(registry_module, "INTEGRATION_TOOL_PACKAGES", ())
    monkeypatch.setattr(registry_discovery, "_import_tool_module", lambda _pkg, _name: module)

    # Surface-scoped loads read the on-disk descriptor index; assert surface
    # filtering on the mocked full snapshot instead.
    snapshot = registry_module.get_registered_tools()
    assert [t.name for t in snapshot if "action" in (t.surfaces or ())] == ["perform_action"]
    assert [t.name for t in snapshot if "chat" in (t.surfaces or ())] == []


def test_github_workflow_skill_guidance_is_attached_to_chat_tools() -> None:
    marker = "Use this workflow when the user asks about GitHub engineering status"
    shared_guided_tool_names = {
        "list_github_work_items",
        "summarize_github_pr_status",
        "list_github_security_alerts",
        "generate_work_status_report",
        "summarize_community_followups",
    }

    for surface in (ToolSurface.CHAT,):
        tools_by_name = {
            tool_def.name: tool_def for tool_def in registry_module.get_registered_tools(surface)
        }
        for tool_name in shared_guided_tool_names:
            tool_def = tools_by_name[tool_name]
            assert marker in tool_def.description
            assert "Workflow guidance:" in tool_def.description
            assert marker in tool_def.skill_guidance

    chat_tools_by_name = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("chat")
    }
    for tool_name in {
        "propose_github_issue_mutation_from_slack",
        "execute_github_issue_mutation",
    }:
        tool_def = chat_tools_by_name[tool_name]
        assert marker in tool_def.description
        assert "Workflow guidance:" in tool_def.description
        assert marker in tool_def.skill_guidance


def test_every_registered_skill_guidance_file_exists() -> None:
    """A renamed SKILL.md directory must fail here, not silently drop tool guidance."""
    from tools.registry_skill_guidance import _skill_guidance_files

    paths = _skill_guidance_files()
    assert paths
    missing = [str(path) for path in paths if not path.is_file()]
    assert missing == []


def test_sentry_and_posthog_summary_guidance_attach_through_the_registry() -> None:
    tools_by_name = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools()}

    assert (
        '<skill name="summarizing-sentry-issues"'
        in tools_by_name["search_sentry_issues"].skill_guidance
    )
    assert (
        '<skill name="summarizing-posthog-analytics"'
        in tools_by_name["call_posthog_tool"].skill_guidance
    )


def test_github_workflow_skill_guidance_does_not_attach_to_unrelated_github_tools() -> None:
    tools_by_name = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools()}

    tool_def = tools_by_name["get_github_file_contents"]

    assert "Workflow guidance:" not in tool_def.description
    assert tool_def.skill_guidance == ""


def test_architecture_audit_action_tools_are_registered() -> None:
    tools_by_name = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("action")
    }
    for name in (
        "architecture_clone_repo",
        "architecture_cleanup_repo",
        "architecture_save_observations",
    ):
        assert name in tools_by_name
        assert tools_by_name[name].skill_guidance == ""
    assert "scan_architecture_imports" not in tools_by_name
    assert "scan_module_placement" not in tools_by_name
    assert "find_architecture_violations" not in tools_by_name


def test_architecture_audit_not_on_chat() -> None:
    for surface in (ToolSurface.CHAT,):
        names = {tool_def.name for tool_def in registry_module.get_registered_tools(surface)}
        assert "find_architecture_violations" not in names
        assert "architecture_clone_repo" not in names
        assert "architecture_save_observations" not in names


def test_architecture_audit_skill_guidance_does_not_attach_to_unrelated_tools() -> None:
    tools_by_name = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools()}

    tool_def = tools_by_name["get_github_file_contents"]

    assert "Required reply template" not in tool_def.skill_guidance
    assert "Propose, do not execute" not in tool_def.skill_guidance
    assert tool_def.skill_guidance == "" or "architecture" not in tool_def.skill_guidance


def test_python_execution_skill_guidance_does_not_attach_to_unrelated_tools() -> None:
    tools_by_name = {tool_def.name: tool_def for tool_def in registry_module.get_registered_tools()}

    tool_def = tools_by_name["get_github_file_contents"]

    assert "measuring-github-star-velocity" not in tool_def.skill_guidance


def test_github_issue_mutation_execution_remains_chat_only() -> None:
    chat_tools = {
        tool_def.name: tool_def for tool_def in registry_module.get_registered_tools("chat")
    }
    action_tools = {tool_def.name for tool_def in registry_module.get_registered_tools("action")}

    tool_def = chat_tools["execute_github_issue_mutation"]

    assert "execute_github_issue_mutation" not in action_tools
    assert tool_def.surfaces == ("chat",)
    assert tool_def.requires_approval is False
    assert "never an investigation action" in tool_def.description


def test_manifest_discovery_imports_nested_tool_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_pkg: Any = ModuleType("tools.fake_provider")
    provider_pkg.__path__ = []  # type: ignore[attr-defined]
    provider_pkg.TOOL_MODULES = ("nested.incident_tools",)
    nested_module: Any = ModuleType("tools.fake_provider.nested.incident_tools")

    @tool(
        name="lookup_nested_incident",
        description="Lookup nested incident metadata.",
        source="knowledge",
    )
    def lookup_nested_incident(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    lookup_nested_incident.__module__ = nested_module.__name__
    nested_module.lookup_nested_incident = lookup_nested_incident

    monkeypatch.setattr(registry_module, "_external_tool_packages", [])
    monkeypatch.setattr(registry_module, "INTEGRATION_TOOL_PACKAGES", ())
    monkeypatch.setattr(
        registry_discovery,
        "_iter_tool_module_names",
        lambda package: ["fake_provider"] if package is registry_module.tools_package else [],
    )

    def mock_import(package: ModuleType, name: str) -> ModuleType:
        if package is registry_module.tools_package and name == "fake_provider":
            return provider_pkg
        if package is provider_pkg and name == "nested.incident_tools":
            return nested_module
        raise AssertionError(f"unexpected import: {package.__name__}.{name}")

    monkeypatch.setattr(registry_discovery, "_import_tool_module", mock_import)

    tools = registry_module.get_registered_tools()

    assert [tool_def.name for tool_def in tools] == ["lookup_nested_incident"]
    assert registry_module.get_registered_tool_map()["lookup_nested_incident"].run(
        incident_id="inc-1"
    ) == {"incident_id": "inc-1"}


def test_manifest_discovery_logs_nested_import_failure_with_full_module_path(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider_pkg: Any = ModuleType("tools.fake_provider")
    provider_pkg.__path__ = []  # type: ignore[attr-defined]
    provider_pkg.TOOL_MODULES = ("nested.broken_tools", "nested.valid_tools")
    valid_module: Any = ModuleType("tools.fake_provider.nested.valid_tools")

    @tool(
        name="valid_nested_tool",
        description="A valid nested tool.",
        source="knowledge",
    )
    def valid_nested_tool() -> dict[str, str]:
        return {"status": "ok"}

    valid_nested_tool.__module__ = valid_module.__name__
    valid_module.valid_nested_tool = valid_nested_tool

    monkeypatch.setattr(registry_module, "_external_tool_packages", [])
    monkeypatch.setattr(registry_module, "INTEGRATION_TOOL_PACKAGES", ())
    monkeypatch.setattr(
        registry_discovery,
        "_iter_tool_module_names",
        lambda package: ["fake_provider"] if package is registry_module.tools_package else [],
    )

    def mock_import(package: ModuleType, name: str) -> ModuleType:
        if package is registry_module.tools_package and name == "fake_provider":
            return provider_pkg
        if package is provider_pkg and name == "nested.broken_tools":
            raise RuntimeError("nested initialization failed")
        if package is provider_pkg and name == "nested.valid_tools":
            return valid_module
        raise AssertionError(f"unexpected import: {package.__name__}.{name}")

    monkeypatch.setattr(registry_discovery, "_import_tool_module", mock_import)

    with caplog.at_level(logging.WARNING, logger="tools.registry"):
        tools = registry_module.get_registered_tools()

    assert [tool_def.name for tool_def in tools] == ["valid_nested_tool"]
    assert any(
        "tools.fake_provider.nested.broken_tools" in record.message
        and "nested initialization failed" in record.message
        for record in caplog.records
    )


def test_manifest_discovery_preserves_duplicate_name_first_wins(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider_pkg: Any = ModuleType("tools.fake_provider")
    provider_pkg.__path__ = []  # type: ignore[attr-defined]
    provider_pkg.TOOL_MODULES = ("nested.shadow_tools",)
    nested_module: Any = ModuleType("tools.fake_provider.nested.shadow_tools")

    @tool(
        name="shared_manifest_tool",
        description="Top-level package tool.",
        source="knowledge",
    )
    def top_level_tool() -> dict[str, str]:
        return {"source": "top-level"}

    @tool(
        name="shared_manifest_tool",
        description="Nested package tool.",
        source="knowledge",
    )
    def nested_tool() -> dict[str, str]:
        return {"source": "nested"}

    top_level_tool.__module__ = provider_pkg.__name__
    nested_tool.__module__ = nested_module.__name__
    provider_pkg.top_level_tool = top_level_tool
    nested_module.nested_tool = nested_tool

    monkeypatch.setattr(registry_module, "_external_tool_packages", [])
    monkeypatch.setattr(registry_module, "INTEGRATION_TOOL_PACKAGES", ())
    monkeypatch.setattr(
        registry_discovery,
        "_iter_tool_module_names",
        lambda package: ["fake_provider"] if package is registry_module.tools_package else [],
    )
    monkeypatch.setattr(
        registry_discovery,
        "_import_tool_module",
        lambda package, name: (
            provider_pkg
            if package is registry_module.tools_package and name == "fake_provider"
            else nested_module
        ),
    )

    with caplog.at_level(logging.WARNING, logger="tools.registry"):
        tool_map = registry_module.get_registered_tool_map()

    assert list(tool_map) == ["shared_manifest_tool"]
    assert tool_map["shared_manifest_tool"].run() == {"source": "top-level"}
    assert any(
        "Duplicate tool name 'shared_manifest_tool' across modules" in record.message
        for record in caplog.records
    )


def test_top_level_discovery_unchanged_without_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top_level_module: Any = ModuleType("tools.fake_top_level_tool")

    @tool(
        name="top_level_status",
        description="Return top-level status.",
        source="knowledge",
    )
    def top_level_status() -> dict[str, str]:
        return {"status": "ok"}

    top_level_status.__module__ = top_level_module.__name__
    top_level_module.top_level_status = top_level_status
    import_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(registry_module, "_external_tool_packages", [])
    monkeypatch.setattr(registry_module, "INTEGRATION_TOOL_PACKAGES", ())
    monkeypatch.setattr(
        registry_discovery,
        "_iter_tool_module_names",
        lambda package: ["fake_top_level_tool"] if package is registry_module.tools_package else [],
    )

    def mock_import(package: ModuleType, name: str) -> ModuleType:
        import_calls.append((package.__name__, name))
        return top_level_module

    monkeypatch.setattr(registry_discovery, "_import_tool_module", mock_import)

    tools = registry_module.get_registered_tools()

    assert [tool_def.name for tool_def in tools] == ["top_level_status"]
    assert import_calls == [("tools", "fake_top_level_tool")]


def test_resolve_tool_display_name_prefers_registered_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: Any = ModuleType("tools.fake_display_name_tool")

    @tool(
        name="get_incident_metadata",
        display_name="Incident metadata",
        description="Return normalized incident metadata.",
        source="knowledge",
    )
    def get_incident_metadata(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    get_incident_metadata.__module__ = module.__name__
    module.get_incident_metadata = get_incident_metadata

    monkeypatch.setattr(
        registry_discovery, "_iter_tool_module_names", lambda _pkg: ["fake_display_name_tool"]
    )
    monkeypatch.setattr(registry_discovery, "_import_tool_module", lambda _pkg, _name: module)

    assert registry_module.resolve_tool_display_name("get_incident_metadata") == "Incident metadata"


def test_resolve_tool_display_name_falls_back_for_unknown_tools() -> None:
    assert (
        registry_module.resolve_tool_display_name("nonexistent_tool_xyz_sentinel")
        == "nonexistent tool xyz sentinel"
    )


def test_real_registry_discovers_migrated_sre_guidance_tool() -> None:
    chat_names = {
        tool_def.name for tool_def in registry_module.get_registered_tools(ToolSurface.CHAT)
    }
    assert "get_sre_guidance" in chat_names


def test_real_registry_discovers_honeycomb_and_coralogix_tools() -> None:
    chat_names = {
        tool_def.name for tool_def in registry_module.get_registered_tools(ToolSurface.CHAT)
    }
    assert {"query_honeycomb_traces", "query_coralogix_logs"} <= chat_names


def test_real_registry_preserves_existing_chat_tool_surface() -> None:
    chat_names = {tool_def.name for tool_def in registry_module.get_registered_tools("chat")}
    assert {"fetch_failed_run", "get_tracer_run", "search_github_code"} <= chat_names


def test_real_registry_does_not_emit_duplicate_tool_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saved_external_packages = list(registry_module._external_tool_packages)
    try:
        registry_module._external_tool_packages.clear()
        registry_module.clear_tool_registry_cache()

        with caplog.at_level(logging.WARNING, logger="tools.registry"):
            tool_names = [tool_def.name for tool_def in registry_module.get_registered_tools()]

        assert len(tool_names) == len(set(tool_names))
        assert {"get_supabase_service_health", "get_supabase_storage_buckets"} <= set(tool_names)
        assert not any("Duplicate tool name" in record.message for record in caplog.records)
    finally:
        registry_module._external_tool_packages[:] = saved_external_packages
        registry_module.clear_tool_registry_cache()


def test_registry_regression_duplicate_tool_names_across_modules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that when two modules export the same tool name, only the first is kept."""
    module1: Any = ModuleType("tools.first_module")
    module2: Any = ModuleType("tools.second_module")

    first_tool = tool(
        name="shared_tool_name",
        description="Tool in first module.",
        source="knowledge",
    )(lambda: {"module": "first"})

    second_tool = tool(
        name="shared_tool_name",
        description="Tool in second module.",
        source="knowledge",
    )(lambda: {"module": "second"})

    first_tool.__module__ = module1.__name__
    second_tool.__module__ = module2.__name__
    module1.shared_tool_first = first_tool
    module2.shared_tool_second = second_tool

    monkeypatch.setattr(
        registry_discovery,
        "_iter_tool_module_names",
        lambda _pkg: ["first_module", "second_module"],
    )
    monkeypatch.setattr(
        registry_discovery,
        "_import_tool_module",
        lambda _pkg, name: module1 if name == "first_module" else module2,
    )

    with caplog.at_level(logging.WARNING, logger="tools.registry"):
        tools = registry_module.get_registered_tools()

    tool_names = [t.name for t in tools]

    assert tool_names.count("shared_tool_name") == 1
    registered_tool = registry_module.get_registered_tool_map()["shared_tool_name"]
    assert registered_tool.run() == {"module": "first"}

    assert any(
        "Duplicate tool name 'shared_tool_name' across modules" in record.message
        for record in caplog.records
        if record.levelname == "WARNING"
    )


def test_registry_regression_import_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that registry gracefully skips modules with import failures."""
    module: Any = ModuleType("tools.valid_tool")

    @tool(
        name="valid_tool",
        description="A valid tool.",
        source="knowledge",
    )
    def valid_tool() -> dict[str, str]:
        return {"status": "ok"}

    valid_tool.__module__ = module.__name__
    module.valid_tool = valid_tool

    def mock_import(_pkg: ModuleType, name: str) -> ModuleType:
        if name == "broken_module":
            raise RuntimeError("Module initialization failed")
        return module

    monkeypatch.setattr(
        registry_discovery,
        "_iter_tool_module_names",
        lambda _pkg: ["broken_module", "valid_tool"],
    )
    monkeypatch.setattr(
        registry_discovery,
        "_import_tool_module",
        mock_import,
    )

    with caplog.at_level(logging.WARNING, logger="tools.registry"):
        tools = registry_module.get_registered_tools()

    tool_names = [t.name for t in tools]

    assert "valid_tool" in tool_names
    assert registry_module.get_registered_tool_map()["valid_tool"].run() == {"status": "ok"}

    # Log message now includes the package prefix (``tools.``) so the
    # operator can tell WHICH discovery path failed when multiple packages
    # are registered.
    assert any(
        "broken_module" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    )


def _v2_tools() -> list[RegisteredTool]:
    return [
        tool
        for tool in registry_module.get_registered_tools()
        if tool.name in _V2_TOOL_CONTRACT_NAMES
    ]


def test_v2_registry_tool_contracts_exist() -> None:
    discovered = {tool.name for tool in _v2_tools()}
    assert discovered == _V2_TOOL_CONTRACT_NAMES


def test_v2_registry_schemas_are_closed_objects() -> None:
    for tool_def in _v2_tools():
        schema = tool_def.public_input_schema
        assert schema.get("type") == "object"
        assert schema.get("additionalProperties") is False


def test_v2_registry_property_schemas_are_typed_and_described() -> None:
    for tool_def in _v2_tools():
        schema = tool_def.public_input_schema
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for prop_name, prop_schema in properties.items():
            assert isinstance(prop_schema, dict), (
                f"{tool_def.name}.{prop_name} must have an object property schema."
            )
            has_type = isinstance(prop_schema.get("type"), str) or isinstance(
                prop_schema.get("oneOf"), list
            )
            has_type = has_type or isinstance(prop_schema.get("anyOf"), list)
            assert has_type, f"{tool_def.name}.{prop_name} must declare type or oneOf."
            assert str(prop_schema.get("description", "")).strip(), (
                f"{tool_def.name}.{prop_name} must include description."
            )


def test_v2_registry_required_fields_are_declared() -> None:
    for tool_def in _v2_tools():
        schema = tool_def.public_input_schema
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        assert isinstance(properties, dict)
        assert isinstance(required, list)
        for required_field in required:
            assert required_field in properties, (
                f"{tool_def.name} required field {required_field!r} missing from schema properties."
            )


def test_v2_registry_public_schema_matches_run_signature() -> None:
    for tool_def in _v2_tools():
        schema = tool_def.public_input_schema
        schema_props = set(schema.get("properties", {}).keys())
        run_sig = inspect.signature(tool_def.run)
        public_params = {
            param.name
            for param in run_sig.parameters.values()
            if param.kind
            in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
            and not param.name.startswith("_")
            and param.name not in set(tool_def.injected_params)
        }
        assert schema_props == public_params, (
            f"{tool_def.name} schema properties do not match run signature public params."
        )


def test_v2_registry_injected_params_are_hidden_from_public_schema() -> None:
    for tool_def in _v2_tools():
        schema = tool_def.public_input_schema
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for injected in tool_def.injected_params:
            assert injected not in props, f"{tool_def.name} leaked injected param {injected!r}."
            assert injected not in required, (
                f"{tool_def.name} keeps injected param {injected!r} as required."
            )


def test_v2_registry_tools_define_output_schema() -> None:
    for tool_def in _v2_tools():
        output_schema = tool_def.output_schema
        assert isinstance(output_schema, dict), f"{tool_def.name} must define output_schema."
        assert output_schema.get("type") == "object"


# --------------------------------------------------------------------------- #
# External tool package registration (extension point for test suites and    #
# downstream integrators that ship their own tools outside tools).        #
# --------------------------------------------------------------------------- #


def test_register_external_tool_package_is_thread_safe_under_concurrent_calls() -> None:
    """Greptile P2: the check-then-append used to be a race window — two
    threads could both pass the ``not in`` check and both append the same
    package, causing every tool in that package to be logged as a duplicate
    and silently dropped on subsequent registry walks. The locked version
    must register exactly once even under high concurrency."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    fake_pkg: Any = ModuleType("synthetic.concurrent_registration_pkg")
    fake_pkg.__path__ = []  # type: ignore[attr-defined] — empty walk, no tools

    barrier = threading.Barrier(parties=8)
    saved = list(registry_module._external_tool_packages)
    try:
        # Make sure the package isn't already registered from a prior test.
        if fake_pkg in registry_module._external_tool_packages:
            registry_module._external_tool_packages.remove(fake_pkg)

        def attempt_register() -> None:
            # Synchronize starts so all threads enter the critical section
            # within the same scheduling quantum — maximizes race exposure.
            barrier.wait()
            registry_module.register_external_tool_package(fake_pkg)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(attempt_register) for _ in range(8)]
            for f in futures:
                f.result()

        # Exactly one registration regardless of how many threads raced.
        count = registry_module._external_tool_packages.count(fake_pkg)
        assert count == 1, (
            f"Expected exactly 1 registration for the same package across 8 "
            f"concurrent callers, got {count}. The lock around check-then-"
            f"append is missing or broken."
        )
    finally:
        # Restore the registration list to its pre-test state.
        registry_module._external_tool_packages[:] = saved
        registry_module.clear_tool_registry_cache()


def test_register_external_tool_package_is_idempotent() -> None:
    """Registering the same package twice doesn't add duplicates."""
    fake_pkg: Any = ModuleType("synthetic.idempotent_registration_pkg")
    fake_pkg.__path__ = []  # type: ignore[attr-defined] — empty walk, no tools

    saved = list(registry_module._external_tool_packages)
    try:
        registry_module.register_external_tool_package(fake_pkg)
        registry_module.register_external_tool_package(fake_pkg)

        assert registry_module._external_tool_packages.count(fake_pkg) == 1
    finally:
        registry_module._external_tool_packages[:] = saved
        registry_module.clear_tool_registry_cache()


def test_registry_clears_cache_on_external_registration_so_new_tools_appear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lru_cache on _load_registry_snapshot would freeze the tool list
    on the first lookup. register_external_tool_package MUST clear the
    cache or registrations after that first lookup would be invisible —
    a silent bug class."""
    # Prime the cache as the production lifecycle would.
    initial_tools = registry_module.get_registered_tools()
    initial_names = {t.name for t in initial_tools}

    # Register a synthetic external package after the cache is warm.
    fake_pkg: Any = ModuleType("synthetic.tool_pkg_for_cache_test")
    fake_pkg.__path__ = []  # type: ignore[attr-defined] — empty walk, no tools

    saved = list(registry_module._external_tool_packages)
    try:
        registry_module.register_external_tool_package(fake_pkg)
        # Cache must have been cleared; next lookup re-runs discovery.
        # No tools to add from fake_pkg, but the call shouldn't crash and
        # the result shape must be equivalent to before.
        post_register_tools = registry_module.get_registered_tools()
        post_register_names = {t.name for t in post_register_tools}
        # The canonical tools are still there.
        assert initial_names == post_register_names
    finally:
        # Drop the synthetic package from the registration list to avoid
        # leaking into other tests.
        registry_module._external_tool_packages[:] = saved
        registry_module.clear_tool_registry_cache()


def test_registry_canonical_tool_wins_when_external_package_redefines_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First registration wins for a given tool name. The canonical
    ``tools.*`` package is walked first, so a downstream integrator
    can't accidentally shadow a production tool with their own version of
    the same name — only a clear log warning, original tool preserved."""
    canonical_tool_name = "ListEKSPodsTool".replace("Tool", "").lower()
    # Build a fake external package that 'declares' a tool with a name
    # that overlaps with a known canonical tool. We use the same
    # discovery path the registry uses, so this is realistic.
    pre_existing = {t.name for t in registry_module.get_registered_tools()}
    if "list_eks_pods" not in pre_existing:
        pytest.skip("Canonical EKS pod tool absent; can't validate shadowing protection.")

    fake_module: Any = ModuleType("synthetic.shadowing_module")

    @tool(name="list_eks_pods", description="external shadow", source="knowledge")
    def shadow_eks_pods() -> dict[str, str]:
        return {"shadow": "true"}

    shadow_eks_pods.__module__ = fake_module.__name__
    fake_module.shadow_eks_pods = shadow_eks_pods

    fake_pkg: Any = ModuleType("synthetic.shadow_pkg")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]

    # Inject the fake by patching the walk + import.
    real_iter = registry_discovery._iter_tool_module_names
    real_import = registry_discovery._import_tool_module

    def fake_iter(package: ModuleType) -> list[str]:
        if package is fake_pkg:
            return ["shadowing_module"]
        return real_iter(package)

    def fake_import(package: ModuleType, name: str) -> ModuleType:
        if package is fake_pkg and name == "shadowing_module":
            return fake_module
        return real_import(package, name)

    monkeypatch.setattr(registry_discovery, "_iter_tool_module_names", fake_iter)
    monkeypatch.setattr(registry_discovery, "_import_tool_module", fake_import)

    saved = list(registry_module._external_tool_packages)
    try:
        registry_module.register_external_tool_package(fake_pkg)
        registry_module.clear_tool_registry_cache()

        tool_map = registry_module.get_registered_tool_map()
        # Canonical tool survives — the shadow was rejected.
        canonical = tool_map.get("list_eks_pods")
        assert canonical is not None
        # Canonical's description is not the shadow's — proves we kept the
        # original, not the override.
        assert canonical.description != "external shadow", (
            f"External package shadowed canonical tool name "
            f"{canonical_tool_name!r}. First-registration-wins rule broken."
        )
    finally:
        registry_module._external_tool_packages[:] = saved
        registry_module.clear_tool_registry_cache()


def test_resolve_tool_activity_labels_uses_registry_metadata() -> None:
    assert registry_module.resolve_tool_activity_labels("query_grafana_metrics") == (
        "Grafana",
        "Mimir",
    )
    assert registry_module.resolve_tool_activity_labels("get_sre_guidance") == (
        "Knowledge",
        "SRE runbook",
    )
    assert registry_module.resolve_tool_activity_labels("query_grafana_mystery") == (
        "Tools",
        "query grafana mystery",
    )


def test_activity_source_badge_drops_trailing_mcp_segment() -> None:
    assert registry_module._activity_source_badge("posthog_mcp") == "Posthog"
    assert registry_module._activity_source_badge("sentry_mcp") == "Sentry"
    assert registry_module._activity_source_badge("grafana") == "Grafana"
    assert registry_module._activity_source_badge("mcp") == "Mcp"
