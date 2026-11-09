"""Set ``pyproject.toml`` version before release builds."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_VERSION_LINE = re.compile(r'(?m)^version = "[^"]+"')


def canonical_release_version(version: str) -> str:
    """Return the PEP 440 canonical form hatchling writes into dist metadata.

    All-digit local segments (a 7-char SHA that happens to be numeric, e.g.
    ``0273802``) lose their leading zeros, so ``--version`` reports
    ``+main.273802``. The release smoke test and release notes must use this
    same string.
    """
    stripped = version.strip().removeprefix("v")
    if "+" not in stripped:
        return stripped
    public, local = stripped.split("+", 1)
    parts = [
        str(int(part)) if part.isdigit() else part.lower()
        for part in local.replace("_", ".").replace("-", ".").split(".")
    ]
    return f"{public}+{'.'.join(parts)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tag", help="Release tag, e.g. v0.1.2026.6.26")
    group.add_argument("--version", help="Explicit version, e.g. 0.1.2026.6.26+main.abc1234")
    args = parser.parse_args()

    version = canonical_release_version(args.version or args.tag)
    pyproject = ROOT / "pyproject.toml"
    updated, count = _VERSION_LINE.subn(
        f'version = "{version}"',
        pyproject.read_text(encoding="utf-8"),
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Could not update version in {pyproject}")

    pyproject.write_text(updated, encoding="utf-8")
    print(f"Set version to {version}")


if __name__ == "__main__":
    main()
