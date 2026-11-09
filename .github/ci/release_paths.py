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
# Markdown the frozen binary actually loads. A skill-only push has to publish a
# main build, or a container that installs that binary never sees the edit.
_BUNDLED_MARKDOWN = "core/agent_harness/prompts/opensre_system_prompt.md"
_BUNDLED_MARKDOWN_PREFIXES = (
    "core/agent_harness/prompts/loop_templates/",
    "core/agent_harness/prompts/skills/",
)


def _affects_release(path: str) -> bool:
    if not path or path.startswith(_EXCLUDED_DIRECTORIES):
        return False
    if (
        path == _BUNDLED_MARKDOWN
        or path.startswith(_BUNDLED_MARKDOWN_PREFIXES)
        or path.endswith("/SKILL.md")
    ):
        return True
    return not path.endswith(_EXCLUDED_SUFFIXES)


def requires_release(paths: Iterable[str]) -> bool:
    """Return whether at least one changed path affects release contents."""
    return any(_affects_release(path) for path in paths)


def main() -> int:
    """Print the GitHub Actions boolean for newline-delimited paths."""
    print(str(requires_release(line.rstrip("\n") for line in sys.stdin)).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
