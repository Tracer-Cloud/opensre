"""Run affected tests without implicitly starting full coverage."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from git_changes import changed_files
from test_scope_rules import select_tests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    selection = select_tests(changed_files(root, args.base), root=root)
    if selection.errors:
        for error in selection.errors:
            print(error, file=sys.stderr)
        return 1
    if not selection.targets:
        print("No affected tests.")
        return 0
    command = [sys.executable, "-m", "pytest", "-q", *selection.targets]
    print(shlex.join(command), flush=True)
    return 0 if args.dry_run else subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
