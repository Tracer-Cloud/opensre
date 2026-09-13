"""Hidden child-process entry for supervised CI repair in installed binaries."""

from __future__ import annotations

from pathlib import Path

import click

from config.constants.ci_repair import CI_REPAIR_WORKER_COMMAND
from integrations.github import run_ci_repair_worker


@click.command(name=CI_REPAIR_WORKER_COMMAND, hidden=True)
@click.argument("store_directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("run_id")
def ci_repair_worker_command(store_directory: Path, run_id: str) -> None:
    """Execute the already-authorized repair under the supervisor's deadline."""
    run_ci_repair_worker(store_directory, run_id)
