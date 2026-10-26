"""Host capability seams are typed contracts declared once.

Three capabilities reach the agent from its host: named commands, LLM-provider
switching and task cancellation. Each re-declared the same seven-parameter
``execution_allowed`` — one approval contract written out multiple times
across three tiers — and the harness's factory aliases for them were
re-declared again inside the gateway.

The gate lives beside ``ExecutionPolicyResult``, the type it takes: the
capabilities are host-to-tool contracts that ``core`` only transports, so
declaring them in ``core`` would mean ``core`` importing ``tools``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_SOURCE_TIERS = ("core", "gateway", "surfaces", "tools", "infrastructure", "integrations")

#: The one module allowed to declare the approval gate.
_GATE_HOME = "tools/interactive_shell/shared/host_contracts.py"

#: The one module allowed to declare the factory aliases (the harness API).
_ALIAS_HOME = "core/agent_harness/ports.py"

#: Host-capability protocols that must take the gate by inheritance, not copy.
_CAPABILITY_PROTOCOLS = {
    "SlashPorts",
    "LlmProviderPorts",
    "TaskCancelPorts",
}


def _python_sources() -> list[Path]:
    return [
        path
        for tier in _SOURCE_TIERS
        for path in sorted((REPO_ROOT / tier).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _is_protocol_class(node: ast.ClassDef) -> bool:
    names = {b.id for b in node.bases if isinstance(b, ast.Name)}
    names |= {b.attr for b in node.bases if isinstance(b, ast.Attribute)}
    return "Protocol" in names


def _declares_the_wide_gate(node: ast.ClassDef) -> bool:
    """True when the class writes out the host-gate signature itself.

    Keyed on the ``session``/``console`` pair, which is what makes it the wide
    host form. ``SubprocessPresenter`` has a narrower two-parameter
    ``execution_allowed`` — it already holds the session and console — so it is
    a different contract, not a copy of this one.
    """
    for child in node.body:
        if not isinstance(child, ast.FunctionDef) or child.name != "execution_allowed":
            continue
        names = {arg.arg for arg in child.args.args} | {arg.arg for arg in child.args.kwonlyargs}
        if {"session", "console"} <= names:
            return True
    return False


def _protocols_declaring_the_gate() -> set[str]:
    """Every ``Protocol`` subclass that writes out the gate, as ``path::Class``."""
    found: set[str] = set()
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and _is_protocol_class(node)
                and _declares_the_wide_gate(node)
            ):
                found.add(f"{path.relative_to(REPO_ROOT).as_posix()}::{node.name}")
    return found


def test_the_execution_gate_is_declared_once() -> None:
    # Arrange — the gate belongs to the harness API; hosts implement it.
    expected = {f"{_GATE_HOME}::ExecutionGate"}

    # Act
    declarations = _protocols_declaring_the_gate()

    # Assert
    assert declarations == expected, (
        "the approval gate must be declared once, beside the policy type it takes; "
        f"found {sorted(declarations)}"
    )


def test_every_host_capability_protocol_takes_the_gate_by_inheritance() -> None:
    # Arrange — a protocol that lists ``execution_allowed`` again is a copy.
    seen: dict[str, list[str]] = {}

    # Act
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name not in _CAPABILITY_PROTOCOLS:
                continue
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            seen[node.name] = bases

    # Assert
    assert set(seen) == _CAPABILITY_PROTOCOLS, f"protocol missing or renamed: {sorted(seen)}"
    without_gate = sorted(name for name, bases in seen.items() if "ExecutionGate" not in bases)
    assert without_gate == [], (
        f"these declare their own approval gate instead of inheriting it: {without_gate}"
    )


def _aliases_bound_by(tree: ast.Module) -> set[str]:
    """Every top-level name this module binds, in all three alias forms.

    ``X = ...`` (Assign), ``X: TypeAlias = ...`` (AnnAssign) and PEP 695
    ``type X = ...`` (TypeAlias) are all established in this repository, so a
    ratchet that reads only one of them would miss a duplicate written in
    either of the others.
    """
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
            bound.add(node.name.id)
    return bound


def test_no_tier_redeclares_a_host_port_factory_alias() -> None:
    # Arrange — the aliases are part of the harness API; a second copy drifts.
    aliases = {
        "LlmProviderPortsFactory",
        "TaskCancelPortsFactory",
        "SlashPortsFactory",
    }

    # Act
    offenders: dict[str, list[str]] = {}
    for path in _python_sources():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == _ALIAS_HOME:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assigned = sorted(_aliases_bound_by(tree) & aliases)
        if assigned:
            offenders[relative] = assigned

    # Assert
    assert offenders == {}, (
        f"host port factory aliases are re-declared outside the API: {offenders}"
    )
