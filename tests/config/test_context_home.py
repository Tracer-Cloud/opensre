"""Where each customer's context lives, and what stays shared inside an org.

The layout exists to give a Slack member the same private conversation history a
laptop user has, while the team keeps one set of integration credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.memory import OPENSRE_MEMORY_DIR_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope

ACME = Principal.org("org_acme")
GLOBEX = Principal.org("org_globex")
ALICE = Actor(id="U_ALICE")
BOB = Actor(id="U_BOB")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every path assertion off the developer's real home directory."""
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    # The global test isolation fixture pins OPENSRE_MEMORY_DIR to a flat
    # tmp path; clear it so get_memory_dir() follows session_home() scoping.
    monkeypatch.delenv(OPENSRE_MEMORY_DIR_ENV, raising=False)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    return tmp_path


def _member(principal: Principal, actor: Actor) -> StorageScope:
    return StorageScope(principal=principal, actor=actor)


def test_laptop_run_keeps_the_plain_home_layout(_home: Path) -> None:
    # Arrange: no scope bound, exactly as a terminal run.

    # Act
    org_root = paths.opensre_home()
    member_root = paths.session_home()

    # Assert: nothing is nested, so an existing install is untouched.
    assert org_root == _home
    assert member_root == _home
    assert paths.integrations_store_path() == _home / "integrations.json"


def test_members_of_one_org_share_integration_credentials(_home: Path) -> None:
    # Arrange: two people in the same workspace.

    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        alice_store = paths.integrations_store_path()
    with bound_storage_scope(_member(ACME, BOB)):
        bob_store = paths.integrations_store_path()

    # Assert: credentials belong to the team, not to whoever is speaking.
    assert alice_store == bob_store
    assert ALICE.id not in str(alice_store)


def test_members_of_one_org_keep_separate_conversation_context(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        alice_sessions = paths.session_home()
        alice_memory = paths.get_memory_dir()
    with bound_storage_scope(_member(ACME, BOB)):
        bob_sessions = paths.session_home()
        bob_memory = paths.get_memory_dir()

    # Assert: one member's history is never reachable from another's root.
    assert alice_sessions != bob_sessions
    assert alice_memory != bob_memory
    assert BOB.id not in str(alice_sessions)
    assert ALICE.id not in str(bob_sessions)


def test_member_context_nests_inside_its_own_org(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        member_root = paths.session_home()
        org_root = paths.opensre_home()

    # Assert: deleting the org directory takes its members with it.
    assert org_root in member_root.parents


def test_two_orgs_never_share_a_context_root(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        acme_root = paths.opensre_home()
    with bound_storage_scope(_member(GLOBEX, ALICE)):
        globex_root = paths.opensre_home()

    # Assert: the same person acting in two orgs reads two different stores.
    assert acme_root != globex_root
    assert globex_root not in acme_root.parents
    assert acme_root not in globex_root.parents


def test_the_same_member_id_in_two_orgs_stays_separate(_home: Path) -> None:
    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        acme_alice = paths.session_home()
    with bound_storage_scope(_member(GLOBEX, ALICE)):
        globex_alice = paths.session_home()

    # Assert
    assert acme_alice != globex_alice


@pytest.mark.parametrize("hostile_id", ["../escape", "a/b", "..", "with space"])
def test_ids_that_would_escape_their_directory_are_rejected(hostile_id: str) -> None:
    # Arrange: an id that would climb out of the context root if interpolated.
    scope = _member(Principal.org(hostile_id), ALICE)

    # Act / Assert
    with bound_storage_scope(scope), pytest.raises(paths.UnsafePathSegmentError):
        paths.opensre_home()


def test_hostile_actor_ids_are_rejected_too() -> None:
    # Arrange
    scope = _member(ACME, Actor(id="../../etc"))

    # Act / Assert
    with bound_storage_scope(scope), pytest.raises(paths.UnsafePathSegmentError):
        paths.session_home()


def test_mounted_volume_is_used_as_the_org_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployed service mounts a volume already scoped to one organization."""
    # Arrange: the infrastructure chroots the mount to this org, so no org
    # segment belongs on top of it.
    mount = tmp_path / "workspace" / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        org_root = paths.opensre_home()
        user_root = paths.session_home()
        integrations = paths.integrations_store_path()

    # Assert: the org id never appears in the path; the mount already is the org.
    assert org_root == mount
    assert ACME.id not in str(user_root)
    assert user_root == mount / "users" / ALICE.id
    assert integrations == mount / "integrations.json"


def test_mount_refuses_a_turn_for_a_different_org(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount declares its owner via ORGANIZATION_ID; a turn for any
    other org must fail closed rather than write into the wrong org's volume."""
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(tmp_path / "memories"))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    with (
        bound_storage_scope(_member(GLOBEX, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError),
    ):
        paths.opensre_home()


def test_mount_serves_its_declared_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The declared owner still resolves to the mount unchanged."""
    mount = tmp_path / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    with bound_storage_scope(_member(ACME, ALICE)):
        assert paths.opensre_home() == mount


def test_mount_refuses_a_turn_when_no_organization_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mount with no declared owner fails closed rather than guessing."""
    # Arrange
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(tmp_path / "memories"))
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)

    # Act / Assert
    with (
        bound_storage_scope(_member(ACME, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError, match="no organization is configured"),
    ):
        paths.opensre_home()


def test_mounted_volume_still_separates_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(tmp_path / "memories"))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        alice = paths.session_home()
    with bound_storage_scope(_member(ACME, BOB)):
        bob = paths.session_home()

    # Assert
    assert alice != bob
    assert BOB.id not in str(alice)


def test_unbound_callers_never_write_into_a_customer_volume(
    _home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telegram and boot-time reads share the Slack task but name no organization.

    The mount belongs to one customer, so a turn that does not name that
    customer must stay on the host root.
    """
    # Arrange: the deployed Slack task always has the mount configured.
    mount = _home / "workspace" / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    # Act: nothing bound, exactly as a Telegram turn or a boot-time read.
    org_root = paths.opensre_home()
    sessions = paths.session_home()
    integrations = paths.integrations_store_path()

    # Assert: none of it lands on the customer's volume.
    assert org_root == _home
    assert sessions == _home
    assert integrations == _home / "integrations.json"
    assert mount not in integrations.parents


def test_a_bound_org_still_uses_the_mount(_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    mount = _home / "workspace" / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    # Act
    with bound_storage_scope(_member(ACME, ALICE)):
        sessions = paths.session_home()

    # Assert
    assert sessions == mount / "users" / ALICE.id


# --- deployment_home: the one root the shell and a chat transport share -------
#
# opensre_home() keeps an unbound caller on the host root so the CLI never writes
# into a customer's volume. Background investigation records are the one artifact
# where that separation is wrong: the shell starts the investigation and a chat
# transport retrieves it, and on a deployment that names an organization those are
# the same person looking at the same work.


def test_deployment_home_matches_opensre_home_for_a_bound_org(
    _home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bound callers must see no difference: the two roots agree, mounted or not."""
    with bound_storage_scope(_member(ACME, ALICE)):
        assert paths.deployment_home() == paths.opensre_home()

    mount = _home / "workspace" / "memories"
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(mount))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)
    with bound_storage_scope(_member(ACME, ALICE)):
        assert paths.deployment_home() == paths.opensre_home() == mount


def test_deployment_home_resolves_an_unbound_caller_to_the_configured_org(
    _home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shell binds no scope. Without this it writes the host root while a chat
    turn for the same deployment reads the org root, so the two never meet."""
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    unbound = paths.deployment_home()
    with bound_storage_scope(_member(ACME, ALICE)):
        bound = paths.deployment_home()

    assert unbound == bound == _home / "orgs" / ACME.id
    # opensre_home() must keep its own contract: unbound stays on the host root.
    assert paths.opensre_home() == _home


def test_deployment_home_stays_on_the_host_root_with_no_organization(_home: Path) -> None:
    """A laptop names no organization, so nothing changes for it."""
    assert paths.deployment_home() == _home


def test_deployment_home_refuses_a_mount_owned_by_another_org(
    _home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner check is the whole tenancy guarantee; it must survive being
    shared with a second entry point rather than being reimplemented beside it."""
    monkeypatch.setenv(paths.CONTEXT_ROOT_ENV, str(_home / "memories"))
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)

    with (
        bound_storage_scope(_member(GLOBEX, ALICE)),
        pytest.raises(paths.ContextRootOwnerMismatchError),
    ):
        paths.deployment_home()


def test_deployment_home_rejects_a_hostile_configured_organization(
    _home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unbound caller takes the org id from the environment rather than from a
    validated principal, so it must pass through the same segment check."""
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "../escape")

    with pytest.raises(paths.UnsafePathSegmentError):
        paths.deployment_home()
