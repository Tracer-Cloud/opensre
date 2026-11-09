"""Pin the ``MemoryType`` closed vocabulary and its on-disk string identity."""

from __future__ import annotations

from core.domain.memory.frontmatter import serialize_memory
from core.domain.memory.models import MEMORY_TYPES, MemoryRecord, MemoryType


def test_members_pin_expected_values() -> None:
    assert MemoryType.USER == "user"
    assert MemoryType.INFRASTRUCTURE == "infrastructure"
    assert MemoryType.REPOSITORY == "repository"
    assert MemoryType.PREFERENCE == "preference"
    assert MemoryType.INVESTIGATION_LEARNING == "investigation_learning"


def test_round_trip_from_string() -> None:
    assert MemoryType("user") is MemoryType.USER
    assert MemoryType("infrastructure") is MemoryType.INFRASTRUCTURE
    assert MemoryType("repository") is MemoryType.REPOSITORY
    assert MemoryType("preference") is MemoryType.PREFERENCE
    assert MemoryType("investigation_learning") is MemoryType.INVESTIGATION_LEARNING


def test_memory_types_are_plain_string_values() -> None:
    assert MEMORY_TYPES == (
        "user",
        "infrastructure",
        "repository",
        "preference",
        "investigation_learning",
    )
    assert all(type(value) is str for value in MEMORY_TYPES)


def test_record_coerces_plain_string_to_member() -> None:
    record = MemoryRecord(
        slug="db-primary",
        memory_type="infrastructure",
        description="primary db host",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        body="host: db-1",
    )
    assert record.memory_type is MemoryType.INFRASTRUCTURE


def test_serialized_frontmatter_keeps_bare_value() -> None:
    record = MemoryRecord(
        slug="db-primary",
        memory_type=MemoryType.USER,
        description="prefers concise reports",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        body="likes brevity",
    )
    serialized = serialize_memory(record)
    # StrEnum stringifies to the bare value; a plain Enum would write
    # "type: MemoryType.USER" and break every existing on-disk memory.
    assert "type: user\n" in serialized
    assert "MemoryType" not in serialized
