"""Normal discovery exposes scoped mutations and a read-only status tool."""

from __future__ import annotations

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from tools.registry import clear_tool_registry_cache, get_registered_tool_map


def test_repair_tools_are_discovered_without_inheriting_an_unselected_repo() -> None:
    clear_tool_registry_cache()
    registered = get_registered_tool_map("action")
    schedule = registered["schedule_ci_repair_loop"]
    status = registered["get_ci_repair_loop"]
    assert schedule.surfaces == (ToolSurface.ACTION,)
    assert schedule.requires_approval and schedule.side_effect_level is SideEffectLevel.MUTATING
    assert status.side_effect_level is SideEffectLevel.READ_ONLY
    assert schedule.input_schema["additionalProperties"] is False
    assert set(schedule.input_schema["properties"]) == {"demo", "owner", "repo", "pr_number"}
    assert "github_token" not in status.input_schema["properties"]
    injected = schedule.extract_params(
        {
            "github": {
                "connection_verified": True,
                "owner": "unselected",
                "repo": "production",
                "github_token": "test-token",
            }
        }
    )
    assert injected == {"github_token": "test-token"}
    assert schedule.is_available({"github": {"connection_verified": True}})
    assert not schedule.is_available({})
    clear_tool_registry_cache()
