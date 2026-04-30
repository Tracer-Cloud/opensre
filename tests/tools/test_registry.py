from __future__ import annotations

import logging
from collections.abc import Generator
from types import ModuleType
from typing import Any

import pytest

from app.tools import registry as registry_module
from app.tools.base import BaseTool
from app.tools.investigation_registry.actions import get_available_actions
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool
from app.tools.tool_decorator import tool
from app.types.retrieval import RetrievalControls


@pytest.fixture(autouse=True)
def _reset_registry_cache() -> Generator[None]:
    registry_module.clear_tool_registry_cache()
    yield
    registry_module.clear_tool_registry_cache()


def test_tool_decorator_registers_function_tool_with_inferred_schema() -> None:
    module: Any = ModuleType("app.tools.fake_function_tool")

    @tool(
        name="lookup_incident",
        description="Lookup incident metadata.",
        display_name="Incident metadata",
        source="knowledge",
        surfaces=("investigation", "chat"),
    )
    def lookup_incident(incident_id: str, limit: int = 10) -> dict[str, object]:
        return {"incident_id": incident_id, "limit": limit}

    lookup_incident.__module__ = module.__name__
    module.lookup_incident = lookup_incident

    tools = registry_module._collect_registered_tools_from_module(module)

    assert [tool_def.name for tool_def in tools] == ["lookup_incident"]
    registered = tools[0]
    assert registered.input_schema["properties"]["incident_id"]["type"] == "string"
    assert registered.input_schema["properties"]["limit"]["type"] == "integer"
    assert registered.display_name == "Incident metadata"
    assert registered.input_schema["required"] == ["incident_id"]
    assert registered.surfaces == ("investigation", "chat")


def test_tool_decorator_supports_minimal_single_file_function_tool() -> None:
    module: Any = ModuleType("app.tools.single_file_status_tool")

    @tool(source="knowledge")
    def check_status(run_id: str, include_history: bool = False) -> dict[str, object]:
        """Check status for a run."""
        return {"run_id": run_id, "include_history": include_history}

    check_status.__module__ = module.__name__
    module.check_status = check_status

    tools = registry_module._collect_registered_tools_from_module(module)

    assert [tool_def.name for tool_def in tools] == ["check_status"]
    registered = tools[0]
    assert registered.description == "Check status for a run."
    assert registered.source == "knowledge"
    assert registered.input_schema["properties"]["run_id"]["type"] == "string"
    assert registered.input_schema["properties"]["include_history"]["type"] == "boolean"
    assert registered.input_schema["required"] == ["run_id"]
    assert registered.surfaces == ("investigation",)
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
        surfaces=("investigation", "chat"),
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
        surfaces = ("investigation", "chat")
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
        surfaces = ("investigation", "chat")
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


def test_tool_decorator_preserves_tags_and_cost_tier_for_base_tool_instances() -> None:
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
        cost_tier="cheap",
    )

    registered = getattr(decorated, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.tags == ("safe", "fast")
    assert registered.cost_tier == "cheap"


def test_registered_tool_rejects_unknown_cost_tier() -> None:
    def lookup_incident(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    with pytest.raises(ValueError, match="Unsupported cost tier"):
        RegisteredTool.from_function(
            lookup_incident,
            source="knowledge",
            cost_tier="free",  # type: ignore[arg-type]
        )


def test_auto_discovery_populates_investigation_and_chat_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: Any = ModuleType("app.tools.fake_discovered_tool")

    @tool(
        name="get_incident_metadata",
        description="Return normalized incident metadata.",
        source="knowledge",
        surfaces=("investigation", "chat"),
    )
    def get_incident_metadata(incident_id: str) -> dict[str, str]:
        return {"incident_id": incident_id}

    get_incident_metadata.__module__ = module.__name__
    module.get_incident_metadata = get_incident_metadata

    monkeypatch.setattr(
        registry_module, "_iter_tool_module_names", lambda: ["fake_discovered_tool"]
    )
    monkeypatch.setattr(registry_module, "_import_tool_module", lambda _name: module)

    assert [
        tool_def.name for tool_def in registry_module.get_registered_tools("investigation")
    ] == ["get_incident_metadata"]
    assert [tool_def.name for tool_def in registry_module.get_registered_tools("chat")] == [
        "get_incident_metadata"
    ]
    assert registry_module.get_registered_tool_map("chat")["get_incident_metadata"].run(
        "inc-1"
    ) == {"incident_id": "inc-1"}


def test_resolve_tool_display_name_prefers_registered_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module: Any = ModuleType("app.tools.fake_display_name_tool")

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
        registry_module, "_iter_tool_module_names", lambda: ["fake_display_name_tool"]
    )
    monkeypatch.setattr(registry_module, "_import_tool_module", lambda _name: module)

    assert registry_module.resolve_tool_display_name("get_incident_metadata") == "Incident metadata"


def test_resolve_tool_display_name_falls_back_for_unknown_tools() -> None:
    assert (
        registry_module.resolve_tool_display_name("nonexistent_tool_xyz_sentinel")
        == "nonexistent tool xyz sentinel"
    )


def test_real_registry_discovers_migrated_sre_guidance_tool() -> None:
    action_names = {tool_def.name for tool_def in get_available_actions()}
    assert "get_sre_guidance" in action_names


def test_real_registry_discovers_honeycomb_and_coralogix_tools() -> None:
    action_names = {tool_def.name for tool_def in get_available_actions()}
    assert {"query_honeycomb_traces", "query_coralogix_logs"} <= action_names


def test_real_registry_preserves_existing_chat_tool_surface() -> None:
    chat_names = {tool_def.name for tool_def in registry_module.get_registered_tools("chat")}
    assert {"fetch_failed_run", "get_tracer_run", "search_github_code"} <= chat_names


def test_registry_regression_duplicate_tool_names_across_modules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that when two modules export the same tool name, only the first is kept."""
    module1: Any = ModuleType("app.tools.first_module")
    module2: Any = ModuleType("app.tools.second_module")

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
        registry_module,
        "_iter_tool_module_names",
        lambda: ["first_module", "second_module"],
    )
    monkeypatch.setattr(
        registry_module,
        "_import_tool_module",
        lambda name: module1 if name == "first_module" else module2,
    )

    with caplog.at_level(logging.WARNING, logger="app.tools.registry"):
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
    module: Any = ModuleType("app.tools.valid_tool")

    @tool(
        name="valid_tool",
        description="A valid tool.",
        source="knowledge",
    )
    def valid_tool() -> dict[str, str]:
        return {"status": "ok"}

    valid_tool.__module__ = module.__name__
    module.valid_tool = valid_tool

    def mock_import(name: str) -> ModuleType:
        if name == "broken_module":
            raise RuntimeError("Module initialization failed")
        return module

    monkeypatch.setattr(
        registry_module,
        "_iter_tool_module_names",
        lambda: ["broken_module", "valid_tool"],
    )
    monkeypatch.setattr(
        registry_module,
        "_import_tool_module",
        mock_import,
    )

    with caplog.at_level(logging.WARNING, logger="app.tools.registry"):
        tools = registry_module.get_registered_tools()

    tool_names = [t.name for t in tools]

    assert "valid_tool" in tool_names
    assert registry_module.get_registered_tool_map()["valid_tool"].run() == {"status": "ok"}

    assert any(
        "Skipping broken_module" in record.message and record.levelname == "WARNING"
        for record in caplog.records
    )
