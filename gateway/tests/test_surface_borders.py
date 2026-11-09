"""Regression borders: scoped chat turns stay on their own org data.

Chat transports (Slack / Discord / Telegram) bind ``StorageScope``; the local
CLI stays unbound. Each test fixes one edge — either "unbound surfaces ignore
the mount" or "an org turn cannot reach another org's data".

Deployed silos set ``OPENSRE_CONTEXT_ROOT``; most tests configure the mount and
assert what *does not* cross organizations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.memory import OPENSRE_MEMORY_DIR_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session.persistence import paths as session_paths
from gateway.core.storage.session.binding_store import open_file_binding_store
from gateway.core.storage.session.file_bindings import FileBindingStore
from gateway.core.storage.session.paths import bindings_file_path, gateway_dir
from gateway.transports.slack.processing.principal import slack_scope

ACME = Principal.org("org_acme")
GLOBEX = Principal.org("org_globex")
ALICE = "U_ALICE"
BOB = "U_BOB"

# A value unmistakable if it ever surfaces where it should not.
ACME_SECRET = "datadog-key-BELONGS-TO-ACME-ONLY"

_TELEGRAM = "telegram"
_SLACK = "slack"


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway host root, with no mount configured."""
    root = tmp_path / "host"
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", root)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    # get_memory_dir() honours this override and would otherwise resolve to one
    # shared directory for every scope, hiding the isolation under test.
    monkeypatch.delenv(OPENSRE_MEMORY_DIR_ENV, raising=False)
    return root


@pytest.fixture
def mounted(host: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A deployed Slack task: host root plus Acme's mounted volume.

    The mount names its owner, which the deployed silo always does; a mount with
    no declared owner is refused.
    """
    mount = host.parent / "workspace" / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)
    return mount


def _every_context_path() -> list[Path]:
    """Every path that follows the bound scope."""
    return [
        paths.opensre_home(),
        paths.session_home(),
        paths.integrations_store_path(),
        paths.get_memory_dir(),
        session_paths.sessions_dir(),
        bindings_file_path(),
    ]


# ── Border 1: the local CLI is untouched ────────────────────────────────────


def test_cli_paths_are_the_plain_host_layout(host: Path) -> None:
    # Arrange: a terminal run binds no scope.

    # Act
    resolved = _every_context_path()

    # Assert: no org or user directory appears anywhere.
    assert paths.opensre_home() == host
    assert paths.session_home() == host
    assert paths.integrations_store_path() == host / "integrations.json"
    assert session_paths.sessions_dir() == host / "sessions"
    for path in resolved:
        assert paths.ORGS_DIR_NAME not in path.parts
        assert paths.USERS_DIR_NAME not in path.parts


@pytest.mark.usefixtures("host")
def test_cli_is_unaffected_by_a_configured_mount(mounted: Path) -> None:
    """A CLI process on a host that also runs Slack must stay on the host root."""
    # Act: nothing bound, but the mount env is present.
    resolved = _every_context_path()

    # Assert: the customer's volume is never touched.
    for path in resolved:
        assert mounted not in path.parents
        assert path != mounted


# ── Border 2: Telegram is scoped like Slack; platforms stay separate ─────────


def test_telegram_bindings_use_scoped_keys(host: Path) -> None:
    """Telegram production shape: org principal + Telegram user actor."""
    store = FileBindingStore(host / "bindings.json")
    store.bind(
        platform=_TELEGRAM,
        chat_id="42",
        session_id="tg-1",
        principal=ACME,
        actor="tg-42",
    )
    assert (
        store.get_session_id(platform=_TELEGRAM, chat_id="42", principal=ACME, actor="tg-42")
        == "tg-1"
    )
    assert store.get_session_id(platform=_TELEGRAM, chat_id="42") is None
    store.close()


def test_unbound_boot_writes_nothing_to_a_customer_volume(host: Path, mounted: Path) -> None:
    """With no bound scope (CLI / process boot), paths stay on host storage."""
    sessions = session_paths.sessions_dir()
    bindings = bindings_file_path()
    memory = paths.get_memory_dir()

    for path in (sessions, bindings, memory):
        assert mounted not in path.parents
    assert sessions == host / "sessions"


def test_a_slack_org_cannot_hijack_a_telegram_binding(host: Path) -> None:
    """Same chat id on two platforms must stay separate rows."""
    store = FileBindingStore(host / "bindings.json")
    store.bind(
        platform=_TELEGRAM,
        chat_id="42",
        session_id="tg-session",
        principal=ACME,
        actor="tg-42",
    )
    store.bind(
        platform=_SLACK, chat_id="42", session_id="slack-session", principal=ACME, actor=ALICE
    )

    assert (
        store.get_session_id(platform=_TELEGRAM, chat_id="42", principal=ACME, actor="tg-42")
        == "tg-session"
    )
    assert (
        store.get_session_id(platform=_SLACK, chat_id="42", principal=ACME, actor=ALICE)
        == "slack-session"
    )
    store.close()


def test_an_unscoped_lookup_cannot_read_an_org_binding(host: Path) -> None:
    """The legacy empty-principal key must not match a real organization's row."""
    # Arrange: only an org-scoped binding exists.
    store = FileBindingStore(host / "bindings.json")
    store.bind(
        platform=_SLACK, chat_id="C1:100.1", session_id="acme-session", principal=ACME, actor=ALICE
    )

    # Act: read it the way a pre-scoping caller would.
    leaked = store.get_session_id(platform=_SLACK, chat_id="C1:100.1")
    store.close()

    # Assert
    assert leaked is None


# ── Border 3: one Slack org cannot reach another ────────────────────────────


@pytest.mark.usefixtures("host")
def test_two_orgs_never_share_a_context_path() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme = _every_context_path()
    with bound_storage_scope(slack_scope(GLOBEX, ALICE)):
        globex = _every_context_path()

    # Assert: no path is shared, and neither root contains the other.
    assert set(map(str, acme)).isdisjoint(map(str, globex))


@pytest.mark.usefixtures("host")
def test_one_orgs_credentials_never_appear_under_another() -> None:
    # Arrange: Acme stores a credential carrying a distinctive marker.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme_store = paths.integrations_store_path()
    acme_store.parent.mkdir(parents=True, exist_ok=True)
    acme_store.write_text(ACME_SECRET, encoding="utf-8")

    # Act: read everything Globex can reach under its own root.
    with bound_storage_scope(slack_scope(GLOBEX, BOB)):
        globex_root = paths.opensre_home()
    globex_root.mkdir(parents=True, exist_ok=True)
    visible = [p.read_text(encoding="utf-8") for p in globex_root.rglob("*") if p.is_file()]

    # Assert: the marker is nowhere in Globex's view.
    assert not any(ACME_SECRET in body for body in visible)


def test_org_bindings_are_isolated_by_principal(host: Path) -> None:
    # Arrange: two orgs bind the same conversation key.
    store = FileBindingStore(host / "bindings.json")
    store.bind(platform=_SLACK, chat_id="C1:1.0", session_id="acme", principal=ACME, actor=ALICE)
    store.bind(
        platform=_SLACK, chat_id="C1:1.0", session_id="globex", principal=GLOBEX, actor=ALICE
    )

    # Act
    acme_session = store.get_session_id(
        platform=_SLACK, chat_id="C1:1.0", principal=ACME, actor=ALICE
    )
    globex_session = store.get_session_id(
        platform=_SLACK, chat_id="C1:1.0", principal=GLOBEX, actor=ALICE
    )
    store.close()

    # Assert
    assert acme_session == "acme"
    assert globex_session == "globex"


# ── Border 4: inside one org, users are separate but credentials are shared ──


@pytest.mark.usefixtures("host")
def test_members_share_credentials_and_separate_conversations() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        alice_creds, alice_sessions = paths.integrations_store_path(), paths.session_home()
    with bound_storage_scope(slack_scope(ACME, BOB)):
        bob_creds, bob_sessions = paths.integrations_store_path(), paths.session_home()

    # Assert: one set of team credentials, two private conversation roots.
    assert alice_creds == bob_creds
    assert alice_sessions != bob_sessions
    assert BOB not in str(alice_sessions)


def test_members_get_distinct_sessions_in_one_thread(host: Path) -> None:
    """Each Slack user keeps their own history, as chosen for this layout."""
    # Arrange: Alice and Bob speak in the same thread.
    store = FileBindingStore(host / "bindings.json")
    thread = "T1:C1:100.1"

    # Act
    alice_session = store.rotate(platform=_SLACK, chat_id=thread, principal=ACME, actor=ALICE)
    bob_session = store.rotate(platform=_SLACK, chat_id=thread, principal=ACME, actor=BOB)

    # Assert: separate sessions, and each still resolves to its own.
    assert alice_session != bob_session
    assert (
        store.get_session_id(platform=_SLACK, chat_id=thread, principal=ACME, actor=ALICE)
        == alice_session
    )
    assert store.has_any_actor_binding(platform=_SLACK, chat_id=thread, principal=ACME)
    store.close()


# ── Border 5: the install catalog is host-level, never per customer ─────────


@pytest.mark.parametrize("hostile", ["../../etc", "..", "a/b", "with space"])
@pytest.mark.usefixtures("host")
def test_hostile_org_ids_cannot_escape(hostile: str) -> None:
    with (
        bound_storage_scope(StorageScope(principal=Principal.org(hostile), actor=Actor(id=ALICE))),
        pytest.raises(paths.UnsafePathSegmentError),
    ):
        paths.opensre_home()


@pytest.mark.parametrize("hostile", ["../../etc", "..", "a/b"])
@pytest.mark.usefixtures("host")
def test_hostile_user_ids_cannot_escape(hostile: str) -> None:
    with (
        bound_storage_scope(StorageScope(principal=ACME, actor=Actor(id=hostile))),
        pytest.raises(paths.UnsafePathSegmentError),
    ):
        paths.session_home()


# ── Border 7: the scope never outlives its turn ─────────────────────────────


def test_scope_is_released_when_the_turn_ends(host: Path) -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        inside = paths.session_home()
    after = paths.session_home()

    # Assert: the next turn on this thread starts unscoped, not on Acme's data.
    assert inside != after
    assert after == host


@pytest.mark.usefixtures("host")
def test_scope_does_not_leak_across_nested_turns() -> None:
    # Act: a second organization's turn nested inside the first.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        with bound_storage_scope(slack_scope(GLOBEX, BOB)):
            inner = paths.opensre_home()
        outer_again = paths.opensre_home()

    # Assert: the outer turn is restored, not left pointing at Globex.
    assert inner != outer_again
    assert GLOBEX.id not in str(outer_again)


@pytest.mark.usefixtures("host")
def test_each_org_gets_its_own_bindings_database() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme_db = bindings_file_path()
    with bound_storage_scope(slack_scope(GLOBEX, ALICE)):
        globex_db = bindings_file_path()

    # Assert
    assert acme_db != globex_db


def test_telegram_runtime_store_can_read_and_write_bindings(
    host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the store Telegram actually builds, not a hand-made one.

    The previous test built a store directly, so it kept passing when the
    runtime store pointed at a database without the bindings table.
    """
    # Arrange: the store the Telegram runtime constructs at startup.
    from gateway.core.storage.session.binding_store import open_binding_store

    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    bindings = open_binding_store()

    # Act: bind and resolve a Telegram chat the way the runtime does.
    bindings.bind(platform=_TELEGRAM, chat_id="42", session_id="tg-wired")
    resolved = bindings.get_session_id(platform=_TELEGRAM, chat_id="42")
    bindings.close()

    # Assert: the table exists and the round trip works on the host root.
    assert resolved == "tg-wired"
    assert (host / "gateway" / "bindings.json").is_file()


def test_a_mount_without_a_declared_owner_is_refused(
    host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unidentified volume could belong to anyone, so no turn may write to it."""
    # Arrange: the mount is configured but nothing says which org owns it.
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(host.parent / "memories"))
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)

    # Act / Assert
    with (
        bound_storage_scope(slack_scope(ACME, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError),
    ):
        paths.opensre_home()


@pytest.mark.usefixtures("mounted")
def test_a_turn_for_another_org_cannot_use_the_mount() -> None:
    """The volume is chrooted to one org; a different org must never reach it."""
    # Act / Assert: the mount belongs to Acme, the turn belongs to Globex.
    with (
        bound_storage_scope(slack_scope(GLOBEX, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError),
    ):
        paths.opensre_home()


# ── Border 5: process state stays on the host, context follows the customer ──


def test_process_state_stays_on_host_while_bindings_follow_the_customer(
    host: Path, mounted: Path
) -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        bindings = bindings_file_path()
    process_state = gateway_dir()

    # Assert: the pidfile and logs are the machine's; bindings are the customer's.
    assert bindings == mounted / "gateway" / "bindings.json"
    assert process_state == host / "gateway"
    assert mounted not in process_state.parents


@pytest.mark.usefixtures("host")
def test_each_org_gets_its_own_bindings_file() -> None:
    # Act
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        acme = bindings_file_path()
    with bound_storage_scope(slack_scope(GLOBEX, ALICE)):
        globex = bindings_file_path()

    # Assert
    assert acme != globex


# ── Border 6: upgrading off the SQLite index keeps conversations ────────────


def _write_legacy_sqlite(path: Path, platform: str, chat_id: str, session_id: str) -> None:
    """Write an index in exactly the shape main wrote (no principal/actor)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gateway_session_bindings (
            platform TEXT NOT NULL, chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY (platform, chat_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO gateway_session_bindings VALUES (?, ?, ?, 1.0)",
        (platform, chat_id, session_id),
    )
    conn.commit()
    conn.close()


def test_pre_scoping_sqlite_bindings_are_adopted(host: Path) -> None:
    """Selecting columns a pre-scoping index lacks would drop every conversation."""
    # Arrange
    _write_legacy_sqlite(host / "gateway" / "state.db", _TELEGRAM, "42", "tg-from-main")

    # Act
    store = open_file_binding_store(host / "gateway" / "bindings.json")
    resolved = store.get_session_id(platform=_TELEGRAM, chat_id="42")

    # Assert
    assert resolved == "tg-from-main"


def test_sqlite_bindings_are_adopted_from_the_host_onto_the_mount(
    host: Path, mounted: Path
) -> None:
    """A deployment's rows sit on the host while its new file lives on the volume."""
    # Arrange
    _write_legacy_sqlite(host / "gateway" / "state.db", _SLACK, "C1:1.0", "silo-session")

    # Act: the first Slack turn after cutover opens the mounted file.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        store = open_file_binding_store()
        resolved = store.get_session_id(platform=_SLACK, chat_id="C1:1.0")

    # Assert
    assert resolved == "silo-session"
    assert (mounted / "gateway" / "bindings.json").is_file()


def test_adoption_reaches_the_mount_not_only_the_boot_path(host: Path, mounted: Path) -> None:
    """The worker opens the store before any scope is bound.

    Adoption must therefore follow the path the turn actually uses, not the
    unbound host path the store happened to see at boot.
    """
    # Arrange: pre-upgrade rows on the host, and a worker started unbound.
    _write_legacy_sqlite(host / "gateway" / "state.db", _SLACK, "C1:1.0", "pre-upgrade")
    store = open_file_binding_store()

    # Act: the first Slack turn binds the org, moving the path onto the mount.
    with bound_storage_scope(slack_scope(ACME, ALICE)):
        resolved = store.get_session_id(platform=_SLACK, chat_id="C1:1.0")

    # Assert: the thread still resolves to the session it had before the upgrade,
    # and the carried-over rows landed on the mount rather than the boot path.
    assert resolved == "pre-upgrade"
    assert (mounted / "gateway" / "bindings.json").is_file()
