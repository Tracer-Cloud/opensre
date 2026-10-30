"""Every folder directly under ``integrations/`` must be a Python package.

Removing an integration but leaving a non-Python file behind (a ``SKILL.md``,
a fixture) keeps an empty-looking vendor folder in the tree that nothing
imports. Delete the whole folder when the integration goes.
"""

from __future__ import annotations

from pathlib import Path

_INTEGRATIONS = Path(__file__).resolve().parents[2] / "integrations"


def _has_files(folder: Path) -> bool:
    """True when ``folder`` holds a file outside ``__pycache__`` (local bytecode is ignored)."""
    return any(path.is_file() and "__pycache__" not in path.parts for path in folder.rglob("*"))


def test_every_integration_folder_is_a_package() -> None:
    orphans = sorted(
        folder.name
        for folder in _INTEGRATIONS.iterdir()
        if folder.is_dir()
        and folder.name != "__pycache__"
        and not (folder / "__init__.py").is_file()
        and _has_files(folder)
    )

    assert orphans == [], f"integration folders with no __init__.py: {orphans}"
