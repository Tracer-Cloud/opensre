"""Only the gateway's host layer drives the agent; everything else names its types.

The sibling ``test_harness_api_border`` pins *how* the gateway reaches the
harness (through the API modules, never past them). This one pins *who* may
reach it at all:

* **Contracts** — ``SessionCore``, ``OutputSink``, ``SlashPortsFactory``,
  ``SessionGoal`` — are types in signatures. A transport that types a parameter
  is not driving the agent, so any module may import them.
* **Behaviour** — building ports, binding a turn, running a session, formatting
  goal progress — belongs to the host layer. A transport that calls it has
  become a second turn runner, which is what
  ``infrastructure/turn_host/turn_runner.py`` exists to prevent.
* **Direct chat() entrypoint** — ``AgentSession.chat()`` and ``chat_until_goal()``
  bypass the session pool lock and the process capacity gate. Gateway modules
  must never call them; turns must go through ``TurnRunner`` -> ``HeadlessAgent.handle``.

The allowlist is compared exactly, so it can only shrink: a new caller fails
immediately, and a module that stops calling must be removed from it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_ROOT = REPO_ROOT / "gateway"
HARNESS_PACKAGE = "core.agent_harness"

#: Harness names that are contracts rather than behaviour. Importing one states
#: a type; it does not run a turn, build ports, or persist a session.
_HARNESS_CONTRACTS: frozenset[str] = frozenset(
    {
        # A frozen record of the hooks a host wants; constructing one states
        # configuration and runs nothing. The composer must sit outside the
        # host layer because the host may not reach the tool tier.
        "AgentBuildConfig",
        "OutputSink",
        "SessionCore",
        "SessionGoal",
        "SlashPortsFactory",
        "ToolCallingTurnResult",
        "TurnResult",
    }
)

#: The host layer: every module under it may drive the agent, because driving
#: the agent is what the package is for.
_HOST_PACKAGE = "infrastructure/turn_host/"

#: Modules outside the host layer that still call harness behaviour, and why.
#: Compared exactly, so it can only shrink.
_OUTSIDE_HOST_LAYER: frozenset[str] = frozenset(
    {
        # Session lifecycle is the harness's; the resolver adds the gateway's
        # chat-id binding on top and must flush through SessionManager.
        "gateway/core/storage/session/resolver.py",
    }
)

#: Gateway modules allowed to call unguarded chat() / chat_until_goal() directly.
#: Empty: gateway turns must go through TurnRunner -> HeadlessAgent.handle to ensure
#: same-session serialization (pool lock) and process turn capacity limits.
#: Compared exactly, so it can only shrink.
_ALLOWED_CHAT_CALLERS: frozenset[str] = frozenset()


def _harness_names(tree: ast.AST) -> set[str]:
    """Names imported from the harness in ``tree``, whatever the API module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == HARNESS_PACKAGE or node.module.startswith(HARNESS_PACKAGE + "."))
        ):
            names.update(alias.name for alias in node.names)
    return names


def _gateway_sources() -> list[Path]:
    return [
        path
        for path in sorted(GATEWAY_ROOT.rglob("*.py"))
        if not {"__pycache__", "tests"} & set(path.parts)
    ]


def _behaviour_callers() -> dict[str, set[str]]:
    """Gateway modules importing harness behaviour, mapped to the names they take."""
    callers: dict[str, set[str]] = {}
    for path in _gateway_sources():
        behaviour = _harness_names(ast.parse(path.read_text(encoding="utf-8"))) - _HARNESS_CONTRACTS
        if behaviour:
            callers[str(path.relative_to(REPO_ROOT))] = behaviour
    return callers


def test_only_the_host_layer_drives_the_agent() -> None:
    # Arrange / Act
    callers = _behaviour_callers()
    outside = {name for name in callers if not name.startswith(_HOST_PACKAGE)}

    # Assert: no module outside the host layer calls harness behaviour…
    added = sorted(outside - _OUTSIDE_HOST_LAYER)
    assert added == [], (
        f"gateway modules outside the host layer import harness behaviour: "
        f"{ {name: sorted(callers[name]) for name in added} }. "
        "Route the turn through infrastructure/turn_host/turn_runner.py, or import only "
        f"contracts ({', '.join(sorted(_HARNESS_CONTRACTS))})."
    )

    # …and the allowlist holds no module that stopped calling it.
    stale = sorted(_OUTSIDE_HOST_LAYER - outside)
    assert stale == [], (
        f"{stale} no longer import harness behaviour; remove them from the allowlist "
        "so it keeps shrinking."
    )


def test_transports_name_harness_types_without_driving_the_agent() -> None:
    """The contract half of the rule: typing a parameter stays allowed anywhere."""
    # Arrange: every transport module that touches the harness at all.
    transport_sources = [p for p in _gateway_sources() if "transports" in p.parts]

    # Act
    importers = {
        str(path.relative_to(REPO_ROOT)): _harness_names(
            ast.parse(path.read_text(encoding="utf-8"))
        )
        for path in transport_sources
    }
    importers = {name: names for name, names in importers.items() if names}

    # Assert: transports import harness names, and only contracts.
    assert importers, "expected transports to type their session parameters"
    for name, names in importers.items():
        assert names <= _HARNESS_CONTRACTS, f"{name} imports harness behaviour: {sorted(names)}"


def _chat_methods_in_tree(tree: ast.AST) -> set[str]:
    """Return 'chat' / 'chat_until_goal' attribute method calls present in AST ``tree``."""
    methods: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"chat", "chat_until_goal"}
        ):
            methods.add(node.func.attr)
    return methods


def _chat_callers() -> dict[str, set[str]]:
    """Gateway modules calling .chat() or .chat_until_goal()."""
    callers: dict[str, set[str]] = {}
    for path in _gateway_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = _chat_methods_in_tree(tree)
        if methods:
            callers[str(path.relative_to(REPO_ROOT))] = methods
    return callers


def test_gateway_modules_never_call_unguarded_chat() -> None:
    """Gateway turns must go through TurnRunner -> HeadlessAgent.handle, never chat().

    AgentSession.chat() and chat_until_goal() bypass the session agent pool lock
    and process turn capacity gate. No gateway module is permitted to call them.
    """
    callers = _chat_callers()
    actual = set(callers.keys())

    added = sorted(actual - _ALLOWED_CHAT_CALLERS)
    assert added == [], (
        f"gateway modules calling unguarded chat() / chat_until_goal(): "
        f"{ {name: sorted(callers[name]) for name in added} }. "
        "Route turns through infrastructure/turn_host/turn_runner.py (TurnRunner.run / "
        "HeadlessAgent.handle) to preserve same-session serialization and capacity limits."
    )

    stale = sorted(_ALLOWED_CHAT_CALLERS - actual)
    assert stale == [], (
        f"{stale} no longer call chat(); remove them from the allowlist so it keeps shrinking."
    )


def test_chat_border_fails_when_a_module_calls_chat() -> None:
    """Demonstrate the border scanner catches direct .chat() or .chat_until_goal() invocations."""
    tree = ast.parse("def bad_turn_handler(session, prompt):\n    return session.chat(prompt)\n")
    assert _chat_methods_in_tree(tree) == {"chat"}

    goal_tree = ast.parse(
        "def bad_goal_handler(session, prompt):\n    return session.chat_until_goal(prompt)\n"
    )
    assert _chat_methods_in_tree(goal_tree) == {"chat_until_goal"}
