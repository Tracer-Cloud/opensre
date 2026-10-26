from __future__ import annotations

import pytest

from core.domain.types.tools import ToolSurface
from core.tool.contracts import EvidenceType, SideEffectLevel
from core.tool.registry import normalize_surfaces


def test_tool_surface_members_round_trip_from_string() -> None:
    assert [member.value for member in ToolSurface] == ["chat", "action"]
    assert ToolSurface("chat") is ToolSurface.CHAT
    assert ToolSurface.CHAT == "chat"


def test_evidence_type_members_round_trip_from_string() -> None:
    assert [member.value for member in EvidenceType] == [
        "logs",
        "metrics",
        "traces",
        "events",
        "topology",
        "deployment_metadata",
        "query_stats",
        "artifact",
        "other",
    ]
    assert EvidenceType("query_stats") is EvidenceType.QUERY_STATS
    assert EvidenceType.QUERY_STATS == "query_stats"


def test_side_effect_level_members_round_trip_from_string() -> None:
    assert [member.value for member in SideEffectLevel] == [
        "none",
        "read_only",
        "mutating",
        "external",
    ]
    assert SideEffectLevel("read_only") is SideEffectLevel.READ_ONLY
    assert SideEffectLevel.READ_ONLY == "read_only"


@pytest.mark.parametrize("enum", [ToolSurface, EvidenceType, SideEffectLevel])
def test_enums_are_closed(enum: type[ToolSurface | EvidenceType | SideEffectLevel]) -> None:
    with pytest.raises(ValueError):
        enum("not_a_real_value")


def test_normalize_surfaces_coerces_plain_strings_to_members() -> None:
    assert normalize_surfaces(["chat", "action"]) == (ToolSurface.CHAT, ToolSurface.ACTION)
    assert normalize_surfaces(None) == (ToolSurface.CHAT,)
