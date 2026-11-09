"""Every chat transport implements the same concerns — not just the same borders.

The border tests pin what transports may *not* do (import peers, reach around
the registry). Nothing pinned what each one *must* contain, and the newest
transport shipped without principal binding, a turn timeout, ``/stop`` or
credit metering while the whole border suite stayed green — the concerns were
copied from the nearest neighbour, minus whatever the copy dropped.

Concerns are detected in package source rather than by module name: Slack
may keep a concern in ``security.py`` while Telegram uses
``inbound_security.py``. Importing transport modules would pull platform
SDKs into this test.

Known gaps are a ledger, asserted *exactly*: fixing one fails the test until
its entry is removed, so the ledger can only shrink, and a new transport (auto-
discovered, never listed by hand) starts with no entries — fully conformant or
red.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _discover_transport_packages() -> tuple[str, ...]:
    """Every chat transport package on disk (discovered, never hand-listed)."""
    root = REPO_ROOT / "gateway" / "transports"
    return tuple(
        child.name
        for child in sorted(root.iterdir())
        if child.is_dir() and (child / "__init__.py").is_file()
    )


_TRANSPORTS = _discover_transport_packages()

#: Concern → the source marker that proves the transport implements it.
_REQUIRED_CONCERNS: dict[str, str | tuple[str, ...]] = {
    # Identity: who owns this turn's data, bound before any storage access.
    "principal scope resolver": "def resolve_",
    "storage scope bound around the turn": "bound_storage_scope",
    # Session lifecycle goes through the shared step, not a local copy.
    "shared inbound-decision step": "apply_inbound_decision",
    # A hung turn must not hang the conversation forever.
    "turn timeout setting": "turn_timeout_seconds",
    # A user can stop a running turn.
    "stop command handling": "is_stop_command",
    # Turns bind their charge for the shared runner to apply after capacity.
    "credit metering": "bound_turn_metering",
    # Turn output cooperates with host-side cancellation instead of relying
    # on the harness patching the attribute on — declared directly, or inherited
    # from the shared ``SingleMessageTurnOutput`` base.
    "turn output declares turn_cancel": ("self.turn_cancel", "SingleMessageTurnOutput"),
}

#: Concerns each existing transport is known to lack. Ledger, not allowlist:
#: the assertion below is exact equality, so closing a gap requires deleting
#: its entry here, and nothing can be added for a new transport unnoticed.
#: Empty: every transport implements every concern. Scoped to transports —
#: scheduled runs reach the agent through ``scheduler_runners`` rather than a
#: transport, and this file says nothing about them.
_KNOWN_GAPS: dict[str, frozenset[str]] = {}

#: Local reimplementations of concerns that were hoisted to ``gateway.core``.
_FORBIDDEN_LOCAL_COPIES: dict[str, str] = {
    "identity-policy store copy": "def _load_policy",
    "pre-capacity credit consumption": "consume_credits",
    "rotate-session sentinel literal": '"__ROTATE_SESSION__"',
}


def _package_source(transport: str) -> str:
    package_root = REPO_ROOT / "gateway" / "transports" / transport
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(package_root.rglob("*.py"))
    )


def test_the_registry_lists_every_discovered_transport() -> None:
    """A transport on disk but absent from ``TRANSPORTS`` never starts."""
    from gateway.transports.startup import TRANSPORTS

    registered = {registration.name for registration in TRANSPORTS}
    assert registered == set(_TRANSPORTS)


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_transport_implements_the_shared_concerns(transport: str) -> None:
    source = _package_source(transport)

    missing = frozenset(
        concern
        for concern, marker in _REQUIRED_CONCERNS.items()
        if not any(m in source for m in ((marker,) if isinstance(marker, str) else marker))
    )

    expected_missing = _KNOWN_GAPS.get(transport, frozenset())
    newly_missing = sorted(missing - expected_missing)
    assert newly_missing == [], (
        f"transport {transport!r} lost or never had: {newly_missing}. New transports "
        "implement every concern; do not add ledger entries for them."
    )
    fixed = sorted(expected_missing - missing)
    assert fixed == [], (
        f"transport {transport!r} now implements {fixed} — remove those entries from "
        "_KNOWN_GAPS so the ledger keeps shrinking."
    )


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_transport_keeps_no_local_copy_of_hoisted_concerns(transport: str) -> None:
    source = _package_source(transport)

    copies = sorted(name for name, marker in _FORBIDDEN_LOCAL_COPIES.items() if marker in source)

    assert copies == [], (
        f"transport {transport!r} reintroduced {copies}; use the shared "
        "gateway.core implementation instead."
    )


def _metering_calls(transport: str) -> list[ast.Call]:
    """Every ``bound_turn_metering(...)`` call in one transport package.

    Parsed, not imported: importing a transport pulls in its platform SDK.
    """
    root = REPO_ROOT / "gateway" / "transports" / transport
    calls: list[ast.Call] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "bound_turn_metering":
                calls.append(node)
    return calls


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_metered_turns_bind_an_interpolated_per_delivery_key(transport: str) -> None:
    """Each metered turn names the delivery it charges, with a real value.

    A substring marker is not enough here: ``idempotency_key=""`` makes the
    client omit the header entirely, so a constant or empty key would pass a
    textual check while reintroducing the double debit it exists to stop.
    Requiring an f-string with at least one interpolation is what pins the key
    to *this* delivery rather than to the transport.
    """
    calls = _metering_calls(transport)
    assert calls, f"transport '{transport}' never binds turn metering"

    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert "idempotency_key" in keywords, (
            f"transport '{transport}' meters a turn without naming its delivery"
        )
        value = keywords["idempotency_key"]
        assert isinstance(value, ast.JoinedStr), (
            f"transport '{transport}' uses a constant idempotency key; it must be "
            "derived from the delivery id"
        )
        assert any(isinstance(part, ast.FormattedValue) for part in value.values), (
            f"transport '{transport}' interpolates nothing into its idempotency key"
        )
