from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from gateway.core.storage import FileBindingStore, open_file_binding_store
from gateway.core.storage.session.file_bindings import UnreadableBindingsError
from gateway.core.storage.session.paths import bindings_file_path, gateway_dir


@pytest.fixture
def binding_store(tmp_path: Path) -> FileBindingStore:
    return FileBindingStore(tmp_path / "bindings.json")


@pytest.fixture
def principal() -> Principal:
    return Principal.individual("test-user-1")


def test_bind_and_get(binding_store: FileBindingStore, principal: Principal) -> None:
    binding_store.bind(
        platform="telegram",
        chat_id="123",
        session_id="uuid-1",
        principal=principal,
    )
    assert (
        binding_store.get_session_id(
            platform="telegram",
            chat_id="123",
            principal=principal,
        )
        == "uuid-1"
    )


def test_rotate_assigns_new_session(binding_store: FileBindingStore, principal: Principal) -> None:
    binding_store.bind(
        platform="telegram",
        chat_id="123",
        session_id="uuid-1",
        principal=principal,
    )
    new_id = binding_store.rotate(
        platform="telegram",
        chat_id="123",
        principal=principal,
    )
    assert new_id != "uuid-1"
    assert (
        binding_store.get_session_id(
            platform="telegram",
            chat_id="123",
            principal=principal,
        )
        == new_id
    )


def test_bindings_are_isolated_by_principal(binding_store: FileBindingStore) -> None:
    a = Principal.org("org-a")
    b = Principal.org("org-b")
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-a",
        principal=a,
    )
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-b",
        principal=b,
    )
    assert binding_store.get_session_id(platform="slack", chat_id="T:C:1", principal=a) == "sess-a"
    assert binding_store.get_session_id(platform="slack", chat_id="T:C:1", principal=b) == "sess-b"


def test_bindings_are_isolated_by_actor(binding_store: FileBindingStore) -> None:
    org = Principal.org("org-a")
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-alice",
        principal=org,
        actor="U_ALICE",
    )
    binding_store.bind(
        platform="slack",
        chat_id="T:C:1",
        session_id="sess-bob",
        principal=org,
        actor="U_BOB",
    )
    assert (
        binding_store.get_session_id(
            platform="slack", chat_id="T:C:1", principal=org, actor="U_ALICE"
        )
        == "sess-alice"
    )
    assert (
        binding_store.get_session_id(
            platform="slack", chat_id="T:C:1", principal=org, actor="U_BOB"
        )
        == "sess-bob"
    )


def test_has_any_actor_binding_sees_any_member(binding_store: FileBindingStore) -> None:
    org = Principal.org("org-a")
    binding_store.bind(
        platform="slack",
        chat_id="T:C:thread1",
        session_id="sess-alice",
        principal=org,
        actor="U_ALICE",
    )
    assert binding_store.has_any_actor_binding(
        platform="slack",
        chat_id="T:C:thread1",
        principal=org,
    )
    assert not binding_store.has_any_actor_binding(
        platform="slack",
        chat_id="T:C:other",
        principal=org,
    )


def test_bindings_live_on_the_org_context_root_not_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced task must still resolve a thread to the transcript it has."""
    # Arrange: a mounted context root, separate from the host home.
    host = tmp_path / "host"
    mount = tmp_path / "mount"
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", host)
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_acme")

    # Act
    with bound_storage_scope(
        StorageScope(principal=Principal.org("org_acme"), actor=Actor(id="U_ALICE"))
    ):
        bindings = bindings_file_path()
    host_gateway_dir = gateway_dir()

    # Assert: bindings follow the customer's volume; process state stays on host.
    assert bindings == mount / "gateway" / "bindings.json"
    assert host_gateway_dir == host / "gateway"


def test_bindings_survive_a_new_store_over_the_same_volume(tmp_path: Path) -> None:
    # Arrange: one task binds a thread to a session.
    db_path = tmp_path / "bindings.json"
    first = FileBindingStore(db_path)
    first.bind(platform="slack", chat_id="T1:C1:100.1", session_id="s-1", actor="U_ALICE")
    first.close()

    # Act: the task is replaced and a fresh store opens the same volume.
    second = FileBindingStore(db_path)
    resolved = second.get_session_id(platform="slack", chat_id="T1:C1:100.1", actor="U_ALICE")
    second.close()

    # Assert: the thread still resolves to its existing session.
    assert resolved == "s-1"


def test_legacy_sqlite_bindings_are_adopted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading off SQLite must not silently drop existing conversations."""
    # Arrange: a SQLite index in exactly the shape main wrote.
    import sqlite3

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    gateway_dir_path = tmp_path / "gateway"
    gateway_dir_path.mkdir(parents=True)
    legacy = sqlite3.connect(gateway_dir_path / "state.db")
    legacy.executescript(
        """
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL, chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id)
        );
        INSERT INTO gateway_session_bindings VALUES ('telegram','42','legacy-session',1.0);
        """
    )
    legacy.commit()
    legacy.close()

    # Act: the JSON store opens for the first time.
    store = open_file_binding_store(gateway_dir_path / "bindings.json")
    resolved = store.get_session_id(platform="telegram", chat_id="42")

    # Assert: the conversation carries over as an unscoped binding.
    assert resolved == "legacy-session"


def test_a_damaged_bindings_file_is_never_silently_replaced(tmp_path: Path) -> None:
    """Treating an unreadable index as empty would drop every conversation."""
    # Arrange: a file that exists but cannot be parsed.
    path = tmp_path / "bindings.json"
    path.write_text("{not json", encoding="utf-8")
    store = FileBindingStore(path)

    # Act / Assert: reads refuse, and a write does not overwrite the damage.
    with pytest.raises(UnreadableBindingsError):
        store.get_session_id(platform="slack", chat_id="C1")
    with pytest.raises(UnreadableBindingsError):
        store.bind(platform="slack", chat_id="C1", session_id="s-1")
    assert path.read_text(encoding="utf-8") == "{not json"
