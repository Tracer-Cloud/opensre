"""``list_memories`` must reuse parsed records without ever serving stale ones.

Every action-agent turn renders the memory index, so the whole store was read
and parsed per turn — a cost that grows as a user accumulates memories. Records
are now reused until the files change.

Correctness is the whole game here: a cache that misses a write is a agent that
answers from a memory the user just deleted or edited. These tests exercise each
way the store can change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV
from core.domain.memory import store


@pytest.fixture(autouse=True)
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the store per test, and start from a cold cache."""
    directory = tmp_path / "memory"
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(directory))
    store.parsed_memories.cache_clear()
    return directory


def _save(slug: str, body: str) -> None:
    saved = store.save_memory(
        slug=slug,
        memory_type="infrastructure",
        description=f"description for {slug}",
        body=body,
    )
    assert saved is not None, f"failed to save {slug}"


def _slugs() -> list[str]:
    return sorted(record.slug for record in store.list_memories())


def test_a_new_memory_is_visible_immediately() -> None:
    """A memory saved after a listing must appear in the next one."""
    # Arrange
    _save("first-fact", "the prod cluster is eks-prod-1")
    assert _slugs() == ["first-fact"]

    # Act
    _save("second-fact", "the payments db is rds-pay-1")

    # Assert
    assert _slugs() == ["first-fact", "second-fact"]


def test_an_edited_body_is_re_read() -> None:
    """Editing a memory in place must not keep serving the old body.

    This is why the signature stats each file rather than the directory:
    rewriting a file leaves the directory's own mtime untouched.
    """
    # Arrange
    _save("cluster-fact", "the prod cluster is eks-prod-1")
    assert store.list_memories()[0].body == "the prod cluster is eks-prod-1"

    # Act
    _save("cluster-fact", "the prod cluster is eks-prod-2")

    # Assert
    assert store.list_memories()[0].body == "the prod cluster is eks-prod-2"


def test_a_deleted_memory_disappears() -> None:
    """A forgotten memory must not survive in the cache."""
    # Arrange
    _save("stale-fact", "an old fact")
    _save("kept-fact", "a current fact")
    assert _slugs() == ["kept-fact", "stale-fact"]

    # Act
    assert store.delete_memory("stale-fact") is True

    # Assert
    assert _slugs() == ["kept-fact"]


def test_repeat_listings_do_not_re_parse_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-turn path must not pay a full parse when nothing changed."""
    # Arrange
    for i in range(5):
        _save(f"fact-{i}", f"durable fact number {i}")
    store.list_memories()  # prime

    parses = 0
    real_parse = store.parse_memory_file

    def _counting_parse(text: str) -> object:
        nonlocal parses
        parses += 1
        return real_parse(text)

    monkeypatch.setattr(store, "parse_memory_file", _counting_parse)

    # Act
    for _ in range(10):
        store.list_memories()

    # Assert
    assert parses == 0, f"re-parsed memory files {parses} times with nothing changed"


def test_one_principals_memories_never_reach_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two principals with look-alike stores must not share cached records.

    Each Slack user in an org keeps memories under their own directory. The
    cache is keyed by the parsed *contents* of a directory, so if the key were
    the file signature alone, two users whose stores happen to match on name,
    size and mtime would be served each other's memories — a cross-user leak
    that no amount of correct file permissions would catch.
    """
    # Arrange: two stores, same filename, same byte length, same mtime.
    alice = tmp_path / "users" / "U_ALICE" / "memory"
    bob = tmp_path / "users" / "U_BOB" / "memory"
    store.parsed_memories.cache_clear()

    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(alice))
    _save("shared-slug", "alice prod cluster is eks-alice")

    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(bob))
    _save("shared-slug", "bobxx prod cluster is eks-bobxx")

    alice_file = alice / "shared-slug.md"
    bob_file = bob / "shared-slug.md"
    assert alice_file.stat().st_size == bob_file.stat().st_size, (
        "sizes must match to be a real test"
    )
    shared_mtime = alice_file.stat().st_mtime_ns
    import os

    os.utime(bob_file, ns=(shared_mtime, shared_mtime))
    assert bob_file.stat().st_mtime_ns == alice_file.stat().st_mtime_ns

    # Act: read one store, then the other.
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(alice))
    alice_bodies = [record.body for record in store.list_memories()]
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(bob))
    bob_bodies = [record.body for record in store.list_memories()]

    # Assert
    assert alice_bodies == ["alice prod cluster is eks-alice"]
    assert bob_bodies == ["bobxx prod cluster is eks-bobxx"]
