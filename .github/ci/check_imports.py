"""Run all import-graph quality checks in one command.

Orchestrates, in order:

1. **Cycles** — module-load SCCs (``check_import_cycles``)
2. **Layers** — package layers and gateway borders via import-linter (``.importlinter``)
3. **Direct edges** — forbidden top-level imports (``check_direct_imports``)

Used by ``make check-imports`` and CI.

Exit codes:
    0 — all checks passed
    1 — one or more checks failed (each section prints its own detail)
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parent
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_direct_imports import main as check_direct_imports  # noqa: E402
from check_import_cycles import main as check_import_cycles  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ImportCheck:
    name: str
    run: Callable[[], int]


def _run_importlinter() -> int:
    scripts_dir = Path(sys.executable).parent
    lint_imports = next(
        (
            candidate
            for candidate in (
                scripts_dir / "lint-imports",
                scripts_dir / "lint-imports.exe",
            )
            if candidate.is_file()
        ),
        None,
    )
    if lint_imports is None:
        print(
            "lint-imports not found — install dev deps (import-linter package).",
            file=sys.stderr,
        )
        return 1
    completed = subprocess.run([str(lint_imports)], cwd=_REPO_ROOT, check=False)
    return int(completed.returncode)


def import_checks() -> Sequence[ImportCheck]:
    return (
        ImportCheck("Import cycles (Tarjan SCC)", check_import_cycles),
        ImportCheck("Import layers (import-linter)", _run_importlinter),
        ImportCheck("Forbidden direct import edges (module + nested)", check_direct_imports),
    )


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args:
        print(f"Unknown arguments: {' '.join(args)}", file=sys.stderr)
        print("Usage: check_imports.py", file=sys.stderr)
        return 2

    checks = import_checks()
    failures: list[str] = []

    for index, check in enumerate(checks, start=1):
        print(f"=== [{index}/{len(checks)}] {check.name} ===")
        exit_code = check.run()
        if exit_code != 0:
            failures.append(check.name)
        print()

    if not failures:
        print(f"All {len(checks)} import checks passed.")
        return 0

    print(f"FAIL: {len(failures)} import check(s) failed:")
    for name in failures:
        print(f"  - {name}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
