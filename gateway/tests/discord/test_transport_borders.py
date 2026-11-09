"""Regression: Discord is a peer transport, not a client of Slack.

Borders under test
------------------
* ``gateway.transports.discord`` and ``gateway.transports.slack`` never import each other — the
  shared approval broker and attention gate live in ``gateway.core.middleware``.
* Importing the Discord transport pulls in no Slack SDK.
* Discord is org-scoped like Slack: two members of one org get private
  sessions, and a Discord row never answers a lookup from another transport.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from config.principal import Principal
from gateway.core.storage import FileBindingStore

REPO_ROOT = Path(__file__).resolve().parents[3]


def _python_files(package: str) -> list[Path]:
    root = REPO_ROOT / Path(*package.split("."))
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _offenders(package: str, banned_prefix: str) -> list[str]:
    found: list[str] = []
    for path in _python_files(package):
        for name in _imported_modules(path):
            if name == banned_prefix or name.startswith(f"{banned_prefix}."):
                found.append(f"{path.relative_to(REPO_ROOT)} → {name}")
    return found


# ── SoC: transport packages stay peers ──────────────────────────────────────


def test_discord_package_never_imports_slack() -> None:
    # Arrange / Act
    offenders = _offenders("gateway.transports.discord", "gateway.transports.slack")

    # Assert
    assert offenders == [], "Discord reached into the Slack transport:\n" + "\n".join(offenders)


def test_slack_package_never_imports_discord() -> None:
    # Arrange / Act
    offenders = _offenders("gateway.transports.slack", "gateway.transports.discord")

    # Assert
    assert offenders == [], "Slack reached into the Discord transport:\n" + "\n".join(offenders)


def test_shared_approval_module_imports_no_transport() -> None:
    """The extracted broker must not depend on the transports that use it."""
    # Arrange
    shared = REPO_ROOT / "gateway" / "core" / "middleware" / "approvals.py"

    # Act
    imported = _imported_modules(shared)

    # Assert
    assert not [
        n
        for n in imported
        if n.startswith(
            (
                "gateway.transports.slack",
                "gateway.transports.discord",
                "gateway.transports.telegram",
            )
        )
    ]


def test_importing_discord_transport_loads_no_slack_sdk() -> None:
    """A Discord-only deployment must not pay for, or ship, the Slack SDK."""
    # Arrange: a clean interpreter, so nothing another test imported counts.
    probe = (
        "import sys; import gateway.transports.discord.worker; "
        "print(len([m for m in sys.modules if m.startswith('slack_sdk')]), "
        "len([m for m in sys.modules if m.startswith('gateway.transports.slack')]))"
    )

    # Act
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )

    # Assert
    assert result.stdout.split() == ["0", "0"], result.stdout


# ── Discord org users: isolation ────────────────────────────────────────────


def test_two_discord_actors_in_one_org_keep_private_sessions(tmp_path: Path) -> None:
    # Arrange
    store = FileBindingStore(tmp_path / "bindings.json")
    org = Principal.org("org_acme")
    channel = "G:C:1"

    # Act: both members speak in the same channel.
    store.bind(
        platform="discord",
        chat_id=channel,
        session_id="discord-alice",
        principal=org,
        actor="U_ALICE",
    )
    store.bind(
        platform="discord",
        chat_id=channel,
        session_id="discord-bob",
        principal=org,
        actor="U_BOB",
    )

    # Assert: each member reads back only their own session.
    assert (
        store.get_session_id(platform="discord", chat_id=channel, principal=org, actor="U_ALICE")
        == "discord-alice"
    )
    assert (
        store.get_session_id(platform="discord", chat_id=channel, principal=org, actor="U_BOB")
        == "discord-bob"
    )
    store.close()


def test_discord_row_never_answers_another_transport(tmp_path: Path) -> None:
    """The same org and member on Slack must not read the Discord session."""
    # Arrange
    store = FileBindingStore(tmp_path / "bindings.json")
    org = Principal.org("org_acme")
    leaked = "discord-only-session-must-not-appear-on-slack"

    # Act
    store.bind(
        platform="discord",
        chat_id="G:C:1",
        session_id=leaked,
        principal=org,
        actor="U_ALICE",
    )

    # Assert: same chat id, same org, same actor — different transport.
    assert (
        store.get_session_id(platform="slack", chat_id="G:C:1", principal=org, actor="U_ALICE")
        is None
    )
    # And an unscoped (Telegram/CLI-shaped) lookup never sees an org row.
    assert store.get_session_id(platform="discord", chat_id="G:C:1") is None
    store.close()


def test_discord_orgs_never_share_a_binding(tmp_path: Path) -> None:
    # Arrange
    store = FileBindingStore(tmp_path / "bindings.json")
    acme = Principal.org("org_acme")
    globex = Principal.org("org_globex")

    # Act: the same guild/channel id resolved under two organizations.
    store.bind(
        platform="discord",
        chat_id="G:C:1",
        session_id="acme-session",
        principal=acme,
        actor="U_ALICE",
    )
    store.bind(
        platform="discord",
        chat_id="G:C:1",
        session_id="globex-session",
        principal=globex,
        actor="U_ALICE",
    )

    # Assert
    assert (
        store.get_session_id(platform="discord", chat_id="G:C:1", principal=acme, actor="U_ALICE")
        == "acme-session"
    )
    assert (
        store.get_session_id(platform="discord", chat_id="G:C:1", principal=globex, actor="U_ALICE")
        == "globex-session"
    )
    store.close()
