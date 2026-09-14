"""Enforce forbidden *direct* import edges between top-level packages.

Unlike import-linter (which flags transitive chains), this checker looks at
**module-top-level** and **nested** (function/class-body) ``import`` /
``from … import`` statements. Module-top-level uses the same AST walk as
``check_import_cycles``; nested imports close the loophole where lazy
``surfaces.*`` imports bypass the module-level graph.

Used by ``make check-imports`` (and ``check_imports``) alongside
import-linter's config contract.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_import_cycles import (  # noqa: E402
    _build_graph,
    _nested_imports,
    discover_first_party_roots,
    module_from_path,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ``source_prefix -> forbidden destination roots`` for direct imports only.
# Enforces the layering contract documented in ``surfaces/__init__.py``:
# "Nothing first-party may import from surfaces/". Adds an explicit bound
# on ``infrastructure``, ``core``, ``gateway`` so the surfaces ban
# is CI-enforced, not just doc-described.
_FORBIDDEN_DIRECT: dict[str, frozenset[str]] = {
    "infrastructure": frozenset({"surfaces"}),
    "core": frozenset({"surfaces"}),
    "gateway": frozenset({"surfaces"}),
    "integrations": frozenset({"tools", "surfaces"}),
    "tools": frozenset({"surfaces"}),
}

# Known direct violations being burned down — remove entries as fixes land.
# Format: ``"source.module -> dest.module"`` (exact modules from the graph).
_BASELINE_IGNORES: frozenset[str] = frozenset()

# Function/class-body lazy imports that bypass the module-level graph.
# Format matches ``_BASELINE_IGNORES``; burn down by moving shared code
# below ``surfaces/`` or making tools UI-agnostic.
_NESTED_BASELINE_IGNORES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DirectViolation:
    source: str
    target: str

    @property
    def edge(self) -> str:
        return f"{self.source} -> {self.target}"


@dataclass(frozen=True)
class NestedViolation:
    source: str
    target: str
    lineno: int

    @property
    def edge(self) -> str:
        return f"{self.source} -> {self.target}"


def _source_root(module: str) -> str:
    return module.split(".", 1)[0]


def find_direct_violations(
    graph: dict[str, set[str]],
    *,
    forbidden: dict[str, frozenset[str]] | None = None,
    baseline_ignores: frozenset[str] | None = None,
) -> list[DirectViolation]:
    rules = forbidden or _FORBIDDEN_DIRECT
    ignores = baseline_ignores if baseline_ignores is not None else _BASELINE_IGNORES
    violations: list[DirectViolation] = []

    for source_module, targets in sorted(graph.items()):
        source_root = _source_root(source_module)
        forbidden_roots = rules.get(source_root)
        if not forbidden_roots:
            continue
        for target_module in sorted(targets):
            target_root = _source_root(target_module)
            if target_root not in forbidden_roots:
                continue
            edge = DirectViolation(source_module, target_module)
            if edge.edge in ignores:
                continue
            violations.append(edge)
    return violations


def find_nested_direct_violations(
    root: Path,
    first_party_roots: tuple[str, ...],
    *,
    forbidden: dict[str, frozenset[str]] | None = None,
    baseline_ignores: frozenset[str] | None = None,
) -> list[NestedViolation]:
    rules = forbidden or _FORBIDDEN_DIRECT
    ignores = baseline_ignores if baseline_ignores is not None else _NESTED_BASELINE_IGNORES
    roots = frozenset(first_party_roots)
    violations: list[NestedViolation] = []

    for pkg in first_party_roots:
        if pkg not in rules:
            continue
        pkg_path = root / pkg
        if not pkg_path.exists():
            continue
        for py in pkg_path.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            source_module = module_from_path(root, py)
            source_root = _source_root(source_module)
            forbidden_roots = rules.get(source_root)
            if not forbidden_roots:
                continue
            source = py.read_text(encoding="utf-8")
            for target_module, lineno in _nested_imports(source, first_party_roots=roots):
                target_root = _source_root(target_module)
                if target_root not in forbidden_roots:
                    continue
                violation = NestedViolation(source_module, target_module, lineno)
                if violation.edge in ignores:
                    continue
                violations.append(violation)

    return sorted(violations, key=lambda item: (item.source, item.lineno, item.target))


def main(argv: list[str] | None = None) -> int:
    del argv
    root = _REPO_ROOT
    first_party_roots = discover_first_party_roots(root)
    graph = _build_graph(root, first_party_roots)
    module_violations = find_direct_violations(graph)
    nested_violations = find_nested_direct_violations(root, first_party_roots)

    if not module_violations and not nested_violations:
        print(
            "No forbidden direct import edges found "
            f"(module baseline: {len(_BASELINE_IGNORES)}, "
            f"nested baseline: {len(_NESTED_BASELINE_IGNORES)})."
        )
        return 0

    if module_violations:
        print(f"FAIL: {len(module_violations)} forbidden module-level direct import edge(s):")
        for violation in module_violations:
            print(f"  {violation.edge}")

    if nested_violations:
        if module_violations:
            print()
        print(f"FAIL: {len(nested_violations)} forbidden nested direct import edge(s):")
        for nested_violation in nested_violations:
            print(f"  {nested_violation.edge} (line {nested_violation.lineno})")

    print(
        "\nFix by moving shared code to a lower layer (a job-named infrastructure.* package, core/contracts) "
        "or add a temporary baseline entry in .github/ci/check_direct_imports.py "
        "with a linked issue — do not use function-level lazy imports to hide "
        "new direct edges."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
