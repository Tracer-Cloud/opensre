"""The shell's shipped turn path runs through the turn host, not its own agent.

The REPL uses :class:`infrastructure.turn_host.turn_runner.TurnRunner` — the same
handler Slack, Telegram and Discord use. ``build_shell_agent`` still exists so
tests can get an agent without standing up a host, but nothing shipped may
call it: that would rebuild the agent beside the host and let the surfaces
drift apart again.

AST, not text search, so a commented-out or renamed-import call cannot keep this
green. The allowlist is compared exactly, so it can only shrink.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.product_sources import product_python_files

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Same list the Makefile type-checks and lints (``PYTHON_SOURCE_PATHS``).
_PRODUCT_PACKAGES = (
    "bootstrap",
    "config",
    "core",
    "gateway",
    "integrations",
    "infrastructure",
    "surfaces",
    "tools",
)

_SHELL_AGENT_BUILDER = "build_shell_agent"
_TURN_HANDLER = "TurnRunner"
_TURN_HANDLER_MODULE = "infrastructure.turn_host.turn_runner"
_TURN_PATH = Path("surfaces/interactive_shell/runtime/shell_turn_execution.py")

#: The module that defines the builder, plus its own ``__all__``. Nothing else.
_DEFINING_MODULE = Path("surfaces/interactive_shell/runtime/shell_agent.py")

#: Shipped modules still reaching for the builder. Empty: only tests call it.
_ALLOWED_CALLERS: frozenset[str] = frozenset()


def _product_files() -> list[Path]:
    files: list[Path] = []
    for package in _PRODUCT_PACKAGES:
        files.extend(product_python_files(REPO_ROOT / package))
    return sorted(files)


def _references_builder(tree: ast.AST) -> bool:
    """True when the module imports, names, or passes the shell agent builder.

    Catches ``from … import build_shell_agent``, bare / attribute calls, and
    indirect uses such as ``factory = module.build_shell_agent`` (no call yet).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == _SHELL_AGENT_BUILDER for alias in node.names):
                return True
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == _SHELL_AGENT_BUILDER
            or isinstance(node, ast.Name)
            and node.id == _SHELL_AGENT_BUILDER
        ):
            return True
    return False


def _turn_handler_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to ``TurnRunner`` via ``from … import TurnRunner [as X]``."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != _TURN_HANDLER_MODULE:
            continue
        for alias in node.names:
            if alias.name == _TURN_HANDLER:
                aliases.add(alias.asname or alias.name)
    return aliases


def _annotation_names(annotation: ast.AST | None) -> set[str]:
    if annotation is None:
        return set()
    return {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}


def _is_turn_handler_call(node: ast.AST, handler_aliases: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in handler_aliases
    )


def _walk_excluding_nested_scopes(node: ast.AST) -> list[ast.AST]:
    """AST nodes under ``node``, skipping nested function/class bodies."""
    out: list[ast.AST] = []

    def _visit(current: ast.AST, *, skip_self: bool = False) -> None:
        if not skip_self:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return
            out.append(current)
        for child in ast.iter_child_nodes(current):
            _visit(child)

    _visit(node, skip_self=True)
    return out


def _assign_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_assign_target_names(elt))
        return names
    return []


def _bind_from_assignment(
    bound: set[str],
    *,
    names: list[str],
    value: ast.AST | None,
    annotation: ast.AST | None,
    handler_aliases: set[str],
) -> set[str]:
    """Update handler bindings after one name assignment in the current scope."""
    next_bound = set(bound)
    if not names:
        return next_bound
    keeps_handler = False
    if value is not None and _is_turn_handler_call(value, handler_aliases):
        keeps_handler = True
    elif value is None and annotation is not None:
        keeps_handler = bool(_annotation_names(annotation) & handler_aliases)
    elif (
        value is not None
        and annotation is not None
        and _annotation_names(annotation) & handler_aliases
        and isinstance(value, ast.Constant)
        and value.value is None
    ):
        # ``handler: TurnRunner | None = None`` — still a TurnRunner slot.
        keeps_handler = True
    if keeps_handler:
        next_bound.update(names)
    else:
        next_bound.difference_update(names)
    return next_bound


def _bindings_after_block(
    stmts: list[ast.stmt],
    bound: set[str],
    handler_aliases: set[str],
) -> set[str]:
    """Handler names still bound after ``stmts`` run in order in one scope."""
    current = set(bound)
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.Assign):
            names = [name for target in stmt.targets for name in _assign_target_names(target)]
            current = _bind_from_assignment(
                current,
                names=names,
                value=stmt.value,
                annotation=None,
                handler_aliases=handler_aliases,
            )
        elif isinstance(stmt, ast.AnnAssign):
            current = _bind_from_assignment(
                current,
                names=_assign_target_names(stmt.target),
                value=stmt.value,
                annotation=stmt.annotation,
                handler_aliases=handler_aliases,
            )
        elif isinstance(stmt, ast.AugAssign):
            current = _bind_from_assignment(
                current,
                names=_assign_target_names(stmt.target),
                value=stmt.value,
                annotation=None,
                handler_aliases=handler_aliases,
            )
        elif isinstance(stmt, ast.If):
            after_body = _bindings_after_block(stmt.body, current, handler_aliases)
            after_else = (
                _bindings_after_block(stmt.orelse, current, handler_aliases)
                if stmt.orelse
                else set(current)
            )
            current = after_body & after_else
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            after_body = _bindings_after_block(stmt.body, current, handler_aliases)
            after_else = (
                _bindings_after_block(stmt.orelse, current, handler_aliases)
                if getattr(stmt, "orelse", None)
                else set(current)
            )
            # Body may not run — keep only names still bound on every path.
            current = current & after_body & after_else
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            current = _bindings_after_block(stmt.body, current, handler_aliases)
        elif isinstance(stmt, ast.Try):
            after_body = _bindings_after_block(stmt.body, current, handler_aliases)
            handler_states = [
                _bindings_after_block(handler.body, after_body, handler_aliases)
                for handler in stmt.handlers
            ]
            after_else = (
                _bindings_after_block(stmt.orelse, after_body, handler_aliases)
                if stmt.orelse
                else set(after_body)
            )
            paths = [after_body, after_else, *handler_states]
            current = set.intersection(*paths) if paths else set(after_body)
            current = _bindings_after_block(stmt.finalbody, current, handler_aliases)
    return current


def _statement_calls_handler_run(
    stmt: ast.stmt,
    bound: set[str],
    handler_aliases: set[str],
) -> bool:
    for node in _walk_excluding_nested_scopes(stmt):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "run":
            continue
        receiver = func.value
        if isinstance(receiver, ast.Name) and receiver.id in bound:
            return True
        if _is_turn_handler_call(receiver, handler_aliases):
            return True
    return False


def _block_calls_handler_run(
    stmts: list[ast.stmt],
    bound: set[str],
    handler_aliases: set[str],
) -> bool:
    """True when a statement in this scope calls ``run`` on a live TurnRunner binding."""
    current = set(bound)
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _function_calls_handler_run(stmt, handler_aliases):
                return True
            continue
        if isinstance(stmt, ast.ClassDef):
            continue
        if isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            if _block_calls_handler_run(stmt.body, current, handler_aliases):
                return True
            if _block_calls_handler_run(stmt.orelse, current, handler_aliases):
                return True
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            if _block_calls_handler_run(stmt.body, current, handler_aliases):
                return True
        elif isinstance(stmt, ast.Try):
            if _block_calls_handler_run(stmt.body, current, handler_aliases):
                return True
            for handler in stmt.handlers:
                if _block_calls_handler_run(handler.body, current, handler_aliases):
                    return True
            if _block_calls_handler_run(stmt.orelse, current, handler_aliases):
                return True
            if _block_calls_handler_run(stmt.finalbody, current, handler_aliases):
                return True
        elif _statement_calls_handler_run(stmt, current, handler_aliases):
            return True
        current = _bindings_after_block([stmt], current, handler_aliases)
    return False


def _function_calls_handler_run(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    handler_aliases: set[str],
) -> bool:
    bound = {
        arg.arg
        for arg in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)
        if _annotation_names(arg.annotation) & handler_aliases
    }
    return _block_calls_handler_run(fn.body, bound, handler_aliases)


def _calls_turn_handler_run(tree: ast.AST) -> bool:
    """True when ``run`` is invoked on a ``TurnRunner`` in the same scope that binds it.

    Bindings do not leak across functions. Reassigning a tracked name to a
    non-``TurnRunner`` value clears it before a later ``.run()`` can count.
    """
    handler_aliases = _turn_handler_aliases(tree)
    if not handler_aliases:
        return False
    if not isinstance(tree, ast.Module):
        return False
    return _block_calls_handler_run(tree.body, set(), handler_aliases)


def test_no_shipped_module_builds_a_second_shell_agent() -> None:
    # Arrange: every product module except the one that defines the builder.
    candidates = [
        path for path in _product_files() if path.relative_to(REPO_ROOT) != _DEFINING_MODULE
    ]

    # Act
    callers = {
        str(path.relative_to(REPO_ROOT))
        for path in candidates
        if _references_builder(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    }

    # Assert: exact both ways, so a stale allowlist entry fails too.
    assert callers == set(_ALLOWED_CALLERS), (
        "the shell agent builder belongs to tests; shipped code goes through the "
        f"turn host instead:\nunexpected: {sorted(callers - set(_ALLOWED_CALLERS))}\n"
        f"stale allowlist: {sorted(set(_ALLOWED_CALLERS) - callers)}"
    )


def test_the_shell_turn_path_runs_the_turn_host() -> None:
    # Arrange
    path = REPO_ROOT / _TURN_PATH
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    # Act
    imports_host = bool(_turn_handler_aliases(tree))
    runs_host = _calls_turn_handler_run(tree)

    # Assert
    assert imports_host, f"{_TURN_PATH} must import TurnRunner — it is the shared turn"
    assert runs_host, (
        f"{_TURN_PATH} must call TurnRunner.run (or handler.run where handler is "
        "a TurnRunner), not an unrelated .run()"
    )


def test_builder_guard_sees_an_attribute_passed_as_a_factory() -> None:
    tree = ast.parse(
        "import surfaces.interactive_shell.runtime.shell_agent as sa\n"
        "factory = sa.build_shell_agent\n"
    )
    assert _references_builder(tree) is True


def test_builder_guard_ignores_unrelated_names() -> None:
    tree = ast.parse("factory = build_other_agent\n")
    assert _references_builder(tree) is False


def test_turn_host_guard_rejects_an_unrelated_run_call() -> None:
    tree = ast.parse(
        "from infrastructure.turn_host.turn_runner import TurnRunner\n"
        "def execute_shell_turn():\n"
        "    agent = object()\n"
        "    return agent.run()\n"
    )
    assert _calls_turn_handler_run(tree) is False


def test_turn_host_guard_accepts_handler_run_on_a_typed_parameter() -> None:
    tree = ast.parse(
        "from infrastructure.turn_host.turn_runner import TurnRunner\n"
        "def execute_shell_turn(handler: TurnRunner | None = None):\n"
        "    return handler.run('hi', None, None, None)\n"
    )
    assert _calls_turn_handler_run(tree) is True


def test_turn_host_guard_does_not_leak_bindings_across_functions() -> None:
    tree = ast.parse(
        "from infrastructure.turn_host.turn_runner import TurnRunner\n"
        "def _other():\n"
        "    handler = TurnRunner()\n"
        "def execute_shell_turn():\n"
        "    handler = object()\n"
        "    return handler.run()\n"
    )
    assert _calls_turn_handler_run(tree) is False


def test_turn_host_guard_rejects_run_after_reassignment() -> None:
    tree = ast.parse(
        "from infrastructure.turn_host.turn_runner import TurnRunner\n"
        "def execute_shell_turn(handler: TurnRunner | None = None):\n"
        "    handler = object()\n"
        "    return handler.run()\n"
    )
    assert _calls_turn_handler_run(tree) is False


def test_turn_host_guard_accepts_run_after_optional_construction() -> None:
    tree = ast.parse(
        "from infrastructure.turn_host.turn_runner import TurnRunner\n"
        "def execute_shell_turn(handler: TurnRunner | None = None):\n"
        "    if handler is None:\n"
        "        handler = TurnRunner()\n"
        "    return handler.run('hi', None, None, None)\n"
    )
    assert _calls_turn_handler_run(tree) is True


def test_turn_host_guard_accepts_run_on_handler_built_inside_if() -> None:
    tree = ast.parse(
        "from infrastructure.turn_host.turn_runner import TurnRunner\n"
        "def execute_shell_turn():\n"
        "    if True:\n"
        "        handler = TurnRunner()\n"
        "        return handler.run()\n"
    )
    assert _calls_turn_handler_run(tree) is True
