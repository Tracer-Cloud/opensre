"""Validate that a built wheel contains essential runtime data files."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[3]


def missing_skill_files(wheel_path: Path, *, repo_root: Path = ROOT) -> tuple[str, ...]:
    """Return required skill-data paths absent from ``wheel_path``."""
    manifest = runpy.run_path(
        str(repo_root / "infrastructure/deployment/packaging/release_manifest.py")
    )
    required_skill_files = manifest["required_skill_files"]
    expected = {path.relative_to(repo_root).as_posix() for path in required_skill_files(repo_root)}
    with ZipFile(wheel_path) as wheel:
        archived = set(wheel.namelist())
    return tuple(sorted(expected - archived))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Wheel artifact to validate")
    args = parser.parse_args()

    missing = missing_skill_files(args.wheel)
    if missing:
        paths = "\n".join(f"- {path}" for path in missing)
        raise SystemExit(f"Wheel is missing required skill data:\n{paths}")
    print(f"Validated required skill data in {args.wheel}")


if __name__ == "__main__":
    main()
