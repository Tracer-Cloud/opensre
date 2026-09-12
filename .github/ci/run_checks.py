"""Run shared quality checks and locally scoped tests, reporting all failures."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from check_catalog import Check, quality_checks
from git_changes import changed_files, is_documentation
from test_scope_rules import select_tests


def _run(check: Check, root: Path, output: Path) -> tuple[int, float]:
    started = time.monotonic()
    environment = os.environ.copy()
    # A checkout's editable installation must not redirect imports to another tree.
    environment["PYTHONPATH"] = str(root)
    with output.open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                [sys.executable, *check.args],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
            code = result.returncode
        except OSError as exc:
            log.write(str(exc))
            code = 1
    return code, time.monotonic() - started


def run_checks(checks: tuple[Check, ...], *, root: Path, workers: int = 3) -> int:
    """Finish independent checks and return failure if any check fails."""
    started = time.monotonic()
    failures: list[str] = []
    with (
        tempfile.TemporaryDirectory(prefix="opensre-checks-") as directory,
        concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        pending = {}
        for index, check in enumerate(checks):
            output = Path(directory) / f"{index}.log"
            print(
                f"START {check.name}: {shlex.join(['uv', 'run', 'python', *check.args])}",
                flush=True,
            )
            pending[pool.submit(_run, check, root, output)] = (check, output)
        for future in concurrent.futures.as_completed(pending):
            check, output = pending[future]
            code, elapsed = future.result()
            print(f"{'FAIL' if code else 'PASS'} {check.name} ({elapsed:.1f}s)", flush=True)
            if code:
                failures.append(check.name)
                print(output.read_text(encoding="utf-8"), flush=True)
    elapsed = time.monotonic() - started
    print(
        f"Validation: {len(checks) - len(failures)}/{len(checks)} passed in {elapsed:.1f}s.",
        flush=True,
    )
    if elapsed > 60:
        print(
            "Exceeded the 60-second target; all required checks were allowed to finish.", flush=True
        )
    if failures:
        print(f"Failed: {', '.join(sorted(failures))}", flush=True)
    return int(bool(failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("static", "types", "all"), default="all")
    parser.add_argument("--check", choices=tuple(check.name for check in quality_checks()))
    parser.add_argument(
        "--scope", action="store_true", help="Include tests selected from the diff."
    )
    parser.add_argument("--base", help="Explicit branch/commit to compare against.")
    parser.add_argument("--head", help="Compare committed revisions only (used by the push hook).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    root = Path.cwd()
    checks = [
        check
        for check in quality_checks()
        if (check.name == args.check if args.check else args.group in ("all", check.group))
    ]
    errors: tuple[str, ...] = ()
    if args.scope:
        try:
            changed = changed_files(root, args.base, args.head)
        except subprocess.CalledProcessError:
            print(
                "Cannot determine the diff. Fetch the base branch or pass --base explicitly.",
                file=sys.stderr,
            )
            return 1
        if not changed or all(is_documentation(path) for path in changed):
            print("No runtime changes; no code checks required.")
            return 0
        selection = select_tests(changed, root=root)
        errors = selection.errors
        # Global tests remain separate so import order cannot mask discovery failures.
        covered = {arg for check in checks for arg in check.args if arg.startswith("tests/")}
        targets = tuple(target for target in selection.targets if target not in covered)
        if targets:
            checks.append(Check("affected-tests", "scoped", ("-m", "pytest", "-q", *targets)))
        for error in errors:
            print(f"SCOPE ERROR: {error}. Update .github/ci/test_scope_rules.py.", file=sys.stderr)
    if args.dry_run:
        for check in checks:
            print(f"{check.name}: {shlex.join(['uv', 'run', 'python', *check.args])}")
        return int(bool(errors))
    result = run_checks(tuple(checks), root=root, workers=args.workers)
    return result or int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
