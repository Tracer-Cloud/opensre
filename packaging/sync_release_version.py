"""Sync release-facing version strings to a Git tag-derived version."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT / "pyproject.toml"
APP_VERSION_PATH = ROOT / "app" / "version.py"
VERSION_PATTERN = re.compile(r"v?(?P<version>\d{4}\.\d{1,2}\.\d{1,2})")


def _normalize_release_version(raw_value: str) -> str:
    match = VERSION_PATTERN.fullmatch(raw_value.strip())
    if match is None:
        msg = f"Release tag must look like 'vYYYY.M.D' or 'YYYY.M.D'; got {raw_value!r}."
        raise ValueError(msg)

    return match.group("version")


def _replace_project_version(version: str, text: str) -> str:
    lines = text.splitlines(keepends=True)
    in_project_section = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("[") and stripped.endswith("]"):
            in_project_section = stripped == "[project]"
            continue

        if in_project_section and line.lstrip().startswith("version = "):
            lines[index] = re.sub(
                r'(?P<prefix>\bversion\s*=\s*")[^"]+(?P<suffix>")',
                rf"\g<prefix>{version}\g<suffix>",
                line,
                count=1,
            )
            return "".join(lines)

    msg = f"Could not find [project].version in {PYPROJECT_PATH}."
    raise RuntimeError(msg)


def _replace_default_version(version: str, text: str) -> str:
    lines = text.splitlines(keepends=True)

    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if stripped.startswith('DEFAULT_VERSION = "'):
            line_ending = _line_ending_for(line)
            lines[index] = f'DEFAULT_VERSION = "{version}"{line_ending}'
            return "".join(lines)

    msg = f"Could not find DEFAULT_VERSION in {APP_VERSION_PATH}."
    raise RuntimeError(msg)


def _line_ending_for(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _sync_file(path: Path, updater: Callable[[str, str], str], version: str) -> None:
    original_text = path.read_text(encoding="utf-8")
    updated_text = updater(version, original_text)
    path.write_text(updated_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        required=True,
        help="Release tag to sync from, e.g. v2026.4.13.",
    )
    args = parser.parse_args()

    version = _normalize_release_version(args.tag)
    _sync_file(PYPROJECT_PATH, _replace_project_version, version)
    _sync_file(APP_VERSION_PATH, _replace_default_version, version)
    print(f"Synchronized release version to {version}")


if __name__ == "__main__":
    main()
