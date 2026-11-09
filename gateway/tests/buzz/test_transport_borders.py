"""Regression: Buzz is a peer transport, not a client of Telegram/Slack/Discord.

Borders under test
------------------
* ``gateway.transports.buzz`` never imports another transport — the shared
  approval broker and session store live in ``gateway.core.middleware`` /
  ``gateway.core.storage``.
* Importing the Buzz transport pulls in no Discord or Slack SDK.
* Buzz sessions are isolated per ``(channel, sender pubkey)``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from gateway.core.storage import FileBindingStore

REPO_ROOT = Path(__file__).resolve().parents[3]
_OTHER_TRANSPORTS = ("telegram", "slack", "discord")


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


def test_buzz_package_never_imports_another_transport() -> None:
    for other in _OTHER_TRANSPORTS:
        offenders = _offenders("gateway.transports.buzz", f"gateway.transports.{other}")
        assert offenders == [], f"Buzz reached into {other}:\n" + "\n".join(offenders)


def test_importing_buzz_transport_loads_no_other_transport_sdk() -> None:
    """A Buzz-only deployment must not pay for, or ship, another transport's SDK."""
    probe = (
        "import sys; import gateway.transports.buzz.startup; "
        "print(len([m for m in sys.modules if m.startswith('discord')]), "
        "len([m for m in sys.modules if m.startswith('slack_sdk')]), "
        "len([m for m in sys.modules if m.startswith('gateway.transports.telegram')]), "
        "len([m for m in sys.modules if m.startswith('gateway.transports.slack')]), "
        "len([m for m in sys.modules if m.startswith('gateway.transports.discord')]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    assert result.stdout.split() == ["0", "0", "0", "0", "0"], result.stdout


def test_two_buzz_actors_in_one_channel_keep_private_sessions(tmp_path: Path) -> None:
    # Buzz's session key is "{channel}:{pubkey}" (see session_rotation.py) —
    # channels have many members, unlike Telegram's 1:1 DMs, so the pubkey alone
    # cannot isolate sessions.
    store = FileBindingStore(tmp_path / "bindings.json")
    channel = "11111111-1111-1111-1111-111111111111"
    alice_key = f"{channel}:{'a' * 64}"
    bob_key = f"{channel}:{'b' * 64}"

    store.bind(platform="buzz", chat_id=alice_key, session_id="buzz-alice")
    store.bind(platform="buzz", chat_id=bob_key, session_id="buzz-bob")

    assert store.get_session_id(platform="buzz", chat_id=alice_key) == "buzz-alice"
    assert store.get_session_id(platform="buzz", chat_id=bob_key) == "buzz-bob"
    store.close()
