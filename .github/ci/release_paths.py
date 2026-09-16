"""Classify whether changed paths require a rolling release."""

from __future__ import annotations

import sys
from collections.abc import Iterable

_EXCLUDED_DIRECTORIES = (
    ".claude/",
    "docs/",
    "infrastructure/deployment/cloudflare_install_proxy/",
    "tests/",
)
_EXCLUDED_SUFFIXES = (".md", ".mdx")


def requires_release(paths: Iterable[str]) -> bool:
    """Return whether at least one changed path affects release contents."""
    return any(
        path
        and not path.startswith(_EXCLUDED_DIRECTORIES)
        and not path.endswith(_EXCLUDED_SUFFIXES)
        for path in paths
    )


def main() -> int:
    """Print the GitHub Actions boolean for newline-delimited paths."""
    print(str(requires_release(line.rstrip("\n") for line in sys.stdin)).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
