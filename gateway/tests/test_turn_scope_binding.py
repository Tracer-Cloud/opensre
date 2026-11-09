"""Every gateway turn handed to a worker thread binds the tenant scope on that worker.

The scope is a ``ContextVar`` and does not cross ``run_in_executor``. A turn
body that resolves storage on the worker without binding a scope falls back to
the shared home directory — a tenant's data misfiled where nothing reads it.
Every transport binds explicitly at the point of use; this pins that
convention so a new transport (or a refactor) cannot drop it unnoticed.

Only *turn* hops matter: a hop whose callable runs the inbound message
(``_run_turn`` / ``dispatcher.dispatch`` / the agent callback). Pollers,
heartbeats, and the HTTP server thread hand over ``poll_once`` / ``wait`` /
``server.run`` and never touch storage.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_TRANSPORTS = REPO_ROOT / "gateway" / "transports"
_HOP_CALLS = frozenset({"run_in_executor", "to_thread"})
_BINDERS = frozenset({"bound_storage_scope", "in_current_scope"})
#: Names of callables that run an inbound message when handed to a worker.
_TURN_CALLABLES = frozenset({"_run_turn", "dispatch"})


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _handed_over(call: ast.Call) -> str:
    """The name of the callable a hop hands to the worker, or ``""``."""
    args = call.args
    if not args:
        return ""
    # loop.run_in_executor(executor, fn, ...) → fn is args[1]; asyncio.to_thread(fn, ...) → args[0]
    target = args[1] if _call_name(call) == "run_in_executor" and len(args) > 1 else args[0]
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _turn_hops(tree: ast.AST) -> list[str]:
    hops: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in _HOP_CALLS:
            handed = _handed_over(node)
            if handed in _TURN_CALLABLES:
                hops.append(handed)
    return hops


def _binds_scope(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_name(node) in _BINDERS for node in ast.walk(tree)
    )


def _function_body(tree: ast.AST, name: str) -> ast.AST | None:
    """The definition of function/method ``name`` in ``tree`` (nested defs included)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _module_defining(name: str, package: Path) -> Path | None:
    """The transport module that defines a function or method called ``name``."""
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
                return path
    return None


def test_every_turn_hop_binds_the_scope_on_the_worker() -> None:
    offenders: list[str] = []
    for path in sorted(_TRANSPORTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for handed in _turn_hops(tree):
            rel = str(path.relative_to(REPO_ROOT))
            # The bind must be *inside the callable that runs on the worker* — a
            # bind on the async side does not reach the thread. Look at the body
            # of the handed-over function, here (Telegram/Buzz nest ``_run_turn``)
            # or in the module that defines it (Discord hands ``dispatcher.dispatch``).
            body = _function_body(tree, handed)
            if body is None:
                transport_pkg = path.parent
                while transport_pkg.parent != _TRANSPORTS:
                    transport_pkg = transport_pkg.parent
                definer = _module_defining(handed, transport_pkg)
                if definer is not None:
                    body = _function_body(
                        ast.parse(definer.read_text(encoding="utf-8"), filename=str(definer)),
                        handed,
                    )
            if body is not None and _binds_scope(body):
                continue
            offenders.append(
                f"{rel} hands `{handed}` to a worker without binding a scope inside it"
            )
    assert offenders == [], (
        "turn hops without a tenant scope on the worker:\n  "
        + "\n  ".join(offenders)
        + "\nEnter bound_storage_scope(scope) inside the turn callable, or wrap it with "
        "config.scope_context.in_current_scope on the calling thread."
    )


def test_the_guard_sees_the_known_turn_hops() -> None:
    """The check must actually cover the transports; an empty scan is a broken guard."""
    seen: set[str] = set()
    for path in sorted(_TRANSPORTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if _turn_hops(ast.parse(path.read_text(encoding="utf-8"))):
            seen.add(path.relative_to(_TRANSPORTS).parts[0])
    assert {"telegram", "buzz", "discord"} <= seen, seen
