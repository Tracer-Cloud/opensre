from __future__ import annotations

import subprocess
from pathlib import Path

from app.cli.tests.catalog import TestCatalogItem
from app.cli.tests.discover import REPO_ROOT, load_test_catalog


def format_command(item: TestCatalogItem) -> str:
    return item.command_display


def find_test_item(item_id: str) -> TestCatalogItem | None:
    return load_test_catalog().find(item_id)


def run_catalog_item(
    item: TestCatalogItem,
    *,
    dry_run: bool = False,
    working_directory: Path | None = None,
) -> int:
    if not item.command:
        raise ValueError(f"Test item '{item.id}' does not define a runnable command")

    if dry_run:
        print(format_command(item))
        return 0

    result = subprocess.run(
        list(item.command),
        cwd=working_directory or REPO_ROOT,
        check=False,
    )
    return int(result.returncode)


def run_catalog_items(
    items: list[TestCatalogItem],
    *,
    dry_run: bool = False,
    working_directory: Path | None = None,
) -> int:
    """Run multiple catalog items sequentially. Returns worst (max) exit code.

    Non-runnable items are skipped so callers can safely pass mixed selections.
    """
    worst = 0
    for item in items:
        if not item.is_runnable:
            continue
        code = run_catalog_item(item, dry_run=dry_run, working_directory=working_directory)
        worst = max(worst, code)
    return worst
