"""Every shipped directory is a real package, so the wheel carries it.

``[tool.setuptools.packages.find]`` collects only directories holding an
``__init__.py``. A directory without one still imports from a source checkout —
Python treats it as a namespace package — so lint, typecheck and the whole test
suite pass while the installed build is missing it entirely.

Nothing else in CI builds a wheel (``python -m build`` appears once, in
``release.yml``), which is why five integrations plus the investigation
pipeline's stage tree shipped unimportable for weeks. The first execution that
could observe it was a user's install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shared.product_sources import product_python_files

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Roots listed in ``[tool.setuptools.packages.find]``.
_SHIPPED_ROOTS = (
    "bootstrap",
    "config",
    "core",
    "gateway",
    "integrations",
    "infrastructure",
    "tools",
)

#: Directories that hold data files rather than importable modules.
_NOT_PACKAGES = frozenset({"__pycache__", "skills"})


def _directories_needing_a_marker(root: Path) -> list[Path]:
    """Every directory that must be a package for a module under ``root`` to ship.

    Includes the parents of a module directory, not just the directory itself.
    ``integrations/ec2/`` holds no module of its own — only ``tools/`` — yet
    without its own ``__init__.py`` setuptools drops the whole branch.
    """
    found: list[Path] = []
    for path in product_python_files(root):
        directory = path.parent
        if any(part in _NOT_PACKAGES for part in directory.relative_to(_REPO_ROOT).parts):
            continue
        while directory != _REPO_ROOT:
            if directory not in found:
                found.append(directory)
            directory = directory.parent
    return found


@pytest.mark.parametrize("root_name", _SHIPPED_ROOTS)
def test_every_shipped_module_directory_is_an_importable_package(root_name: str) -> None:
    # Arrange
    root = _REPO_ROOT / root_name

    # Act
    missing = [
        str(directory.relative_to(_REPO_ROOT))
        for directory in _directories_needing_a_marker(root)
        if not (directory / "__init__.py").exists()
    ]

    # Assert
    assert missing == [], (
        f"{len(missing)} directory(ies) under {root_name}/ have Python modules but no "
        f"__init__.py, so setuptools drops them from the wheel: {missing}"
    )
