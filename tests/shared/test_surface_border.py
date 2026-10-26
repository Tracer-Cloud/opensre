"""What a surface may depend on: not its peer, and not the gateway's insides.

``surfaces/cli`` and ``surfaces/interactive_shell`` are peers: what both need
lives below them (``surfaces/shared``, ``config``, ``core``, ``infrastructure``).
Surfaces may also drive the gateway *process* — start it, stop it, read its
status — but not reach into its transports, storage or middleware.

Every edge is pinned as an exact allowlist so it can only shrink: a new import
fails immediately, and an entry no longer imported must be removed.
Underscore-prefixed names never cross.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.shared.harness_api import python_sources

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = "surfaces.cli"
SHELL = "surfaces.interactive_shell"
SURFACES = "surfaces"
GATEWAY = "gateway"

#: CLI modules the shell imports. Empty: the two surfaces are peers.
_SHELL_IMPORTS_FROM_CLI: frozenset[str] = frozenset()

#: Shell modules the CLI imports. Empty: launching the REPL and running the
#: gateway attached come in through ``surfaces.cli.host.CliHost`` from
#: ``surfaces.entrypoint``.
_CLI_IMPORTS_FROM_SHELL: frozenset[str] = frozenset()

#: Gateway modules a surface may import. All three drive the gateway *process*;
#: none of them is the gateway's turn service, and none reaches a transport,
#: a repository or a middleware step. A surface that runs turns is a channel:
#: it implements the host turn contract and is handed to the turn
#: service — it does not import one.
_SURFACES_IMPORT_FROM_GATEWAY: frozenset[str] = frozenset(
    {
        # `opensre gateway start|stop|status` and the REPL's /gateway commands.
        "gateway.core.process",
        # The console entry that constructs the process and starts it.
        "gateway.core.lifecycle.controller",
        # Container MODE=web: `opensre gateway web` serves the health app.
        "gateway.web.web_server",
    }
)


def _imports_of(tree: ast.AST, package: str) -> tuple[set[str], set[str]]:
    """Return ``(modules, private_names)`` imported from ``package`` in ``tree``."""
    modules: set[str] = set()
    private: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == package or node.module.startswith(package + "."):
                modules.add(node.module)
                private.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(package + "."):
                    modules.add(alias.name)
    return modules, private


def _cross_imports(source_package: str, target_package: str) -> tuple[set[str], set[str]]:
    root = REPO_ROOT.joinpath(*source_package.split("."))
    modules: set[str] = set()
    private: set[str] = set()
    for path in python_sources(root):
        found_modules, found_private = _imports_of(
            ast.parse(path.read_text(encoding="utf-8")), target_package
        )
        modules |= found_modules
        private |= found_private
    return modules, private


def _assert_matches(imported: set[str], allowlist: frozenset[str], *, edge: str) -> None:
    added = sorted(imported - allowlist)
    assert added == [], (
        f"{edge} imports not on the allowlist: {added}. "
        "Move the shared code below both surfaces (surfaces/shared, config, core, platform)."
    )
    stale = sorted(allowlist - imported)
    assert stale == [], (
        f"{edge} no longer imports {stale}; remove them so the allowlist keeps shrinking."
    )


def test_shell_imports_from_the_cli_only_what_the_allowlist_names() -> None:
    modules, private = _cross_imports(SHELL, CLI)
    _assert_matches(modules, _SHELL_IMPORTS_FROM_CLI, edge="interactive_shell → cli")
    assert sorted(private) == []


def test_cli_imports_from_the_shell_only_what_the_allowlist_names() -> None:
    modules, private = _cross_imports(CLI, SHELL)
    _assert_matches(modules, _CLI_IMPORTS_FROM_SHELL, edge="cli → interactive_shell")
    assert sorted(private) == []


def test_surfaces_import_from_the_gateway_only_what_the_allowlist_names() -> None:
    modules, private = _cross_imports(SURFACES, GATEWAY)
    _assert_matches(modules, _SURFACES_IMPORT_FROM_GATEWAY, edge="surfaces → gateway")
    assert sorted(private) == []
