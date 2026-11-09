"""Check that code imports each vendor through its main module.

Every ``integrations/<vendor>/`` folder should be used through its main module
``integrations.<vendor>``, not by importing a file deep inside it. This finds
imports that reach inside a vendor folder from outside it, so a test can list
them and require the list to only get shorter over time.

Vendor folders are found on disk, so adding a new ``integrations/<vendor>/``
folder includes it automatically. The 29 loose ``.py`` files directly under
``integrations/`` are not vendors and are not checked here.
"""

from __future__ import annotations

from pathlib import Path

from tests.shared.api_border import ApiBorder

_INTEGRATIONS_DIR = Path(__file__).resolve().parents[2] / "integrations"


def vendor_names() -> tuple[str, ...]:
    """The vendor package names under ``integrations/`` (directories, sorted)."""
    return tuple(
        sorted(
            path.name
            for path in _INTEGRATIONS_DIR.iterdir()
            if path.is_dir() and not path.name.startswith("__") and (path / "__init__.py").exists()
        )
    )


VENDOR_PACKAGES: tuple[str, ...] = tuple(f"integrations.{name}" for name in vendor_names())

#: The main module of every vendor. Importing a file below a vendor folder, or
#: importing a name the vendor's ``__init__`` does not list in ``__all__``,
#: counts as reaching inside the vendor from outside it.
API_MODULES: frozenset[str] = frozenset(VENDOR_PACKAGES)

INTEGRATIONS_BORDER = ApiBorder(packages=VENDOR_PACKAGES, api_modules=API_MODULES)

__all__ = ["API_MODULES", "INTEGRATIONS_BORDER", "VENDOR_PACKAGES", "vendor_names"]
