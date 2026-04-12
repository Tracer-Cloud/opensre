"""Test catalog CLI commands."""

from __future__ import annotations

import json
from typing import Any

import click

from app.analytics.cli import (
    capture_test_run_started,
    capture_test_synthetic_started,
    capture_tests_listed,
    capture_tests_picker_opened,
)
from app.cli.context import is_json_output, is_yes
from app.cli.errors import OpenSREError

_TEST_CATEGORIES: tuple[str, ...] = ("all", "rca", "demo", "infra-heavy", "ci-safe")


class _TestIdType(click.ParamType):
    """Click parameter type that provides dynamic shell completion for test IDs."""

    name = "test_id"

    def shell_complete(
        self,
        ctx: click.Context,
        param: click.Parameter,
        incomplete: str,  # noqa: ARG002
    ) -> list[click.shell_completion.CompletionItem]:
        try:
            from app.cli.tests.discover import load_test_catalog

            catalog = load_test_catalog()
            return [
                click.shell_completion.CompletionItem(item.id)
                for item in catalog.all_items()
                if item.id.startswith(incomplete) and item.is_runnable
            ]
        except Exception:  # noqa: BLE001
            return []


def _echo_catalog_item(item: Any, *, indent: int = 0) -> None:
    prefix = "  " * indent
    tag_text = f" [{', '.join(item.tags)}]" if item.tags else ""
    click.echo(f"{prefix}{item.id} - {item.display_name}{tag_text}")
    if item.description:
        click.echo(f"{prefix}  {item.description}")
    for child in item.children:
        _echo_catalog_item(child, indent=indent + 1)


def _build_synthetic_argv(*, scenario: str, output_json: bool, mock_grafana: bool) -> list[str]:
    argv: list[str] = []
    if scenario:
        argv.extend(["--scenario", scenario])
    if output_json:
        argv.append("--json")
    if mock_grafana:
        argv.append("--mock-grafana")
    return argv


@click.group(name="tests", invoke_without_command=True)
@click.pass_context
def tests(ctx: click.Context) -> None:
    """Browse and run inventoried tests from the terminal."""
    if ctx.invoked_subcommand is not None:
        return

    if is_yes() or is_json_output():
        raise OpenSREError(
            "No subcommand provided.",
            suggestion="Run 'opensre tests list' or 'opensre tests run <test_id>'.",
        )

    from app.cli.tests.discover import load_test_catalog
    from app.cli.tests.interactive import run_interactive_picker

    capture_tests_picker_opened()
    raise SystemExit(run_interactive_picker(load_test_catalog()))


@tests.command(name="synthetic")
@click.option(
    "--scenario", default="", help="Pin to a single scenario directory, e.g. 001-replication-lag."
)
@click.option("--json", "output_json", is_flag=True, help="Print machine-readable JSON results.")
@click.option(
    "--mock-grafana",
    is_flag=True,
    default=True,
    show_default=True,
    help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
)
def run_synthetic_suite(scenario: str, output_json: bool, mock_grafana: bool) -> None:
    """Run the synthetic RDS PostgreSQL RCA benchmark."""
    from tests.synthetic.rds_postgres.run_suite import main as run_suite_main

    capture_test_synthetic_started(scenario or "all", mock_grafana=mock_grafana)
    raise SystemExit(
        run_suite_main(
            _build_synthetic_argv(
                scenario=scenario,
                output_json=output_json,
                mock_grafana=mock_grafana,
            )
        )
    )


def _catalog_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.display_name,
        "tags": list(item.tags) if item.tags else [],
        "description": item.description or "",
        "children": [_catalog_item_to_dict(c) for c in item.children],
    }


@tests.command(name="list")
@click.option(
    "--category",
    type=click.Choice(_TEST_CATEGORIES),
    default="all",
    show_default=True,
    help="Filter the inventory by category tag.",
)
@click.option("--search", default="", help="Case-insensitive text filter.")
def list_tests(category: str, search: str) -> None:
    """List available tests and suites."""
    from app.cli.tests.discover import load_test_catalog

    capture_tests_listed(category, search=bool(search))

    catalog = load_test_catalog()
    items = list(catalog.filter(category=category, search=search))

    if is_json_output():
        click.echo(json.dumps([_catalog_item_to_dict(i) for i in items], indent=2))
        return

    for item in items:
        _echo_catalog_item(item)


@tests.command(name="run")
@click.argument("test_id", type=_TestIdType())
@click.option("--dry-run", is_flag=True, help="Print the selected command without running it.")
def run_test(test_id: str, dry_run: bool) -> None:
    """Run a test or suite by stable inventory id."""
    from app.cli.tests.runner import find_test_item, run_catalog_item

    item = find_test_item(test_id)
    if item is None:
        raise OpenSREError(
            f"Unknown test id: {test_id}",
            suggestion="Run 'opensre tests list' to see available test ids.",
        )

    capture_test_run_started(test_id, dry_run=dry_run)
    raise SystemExit(run_catalog_item(item, dry_run=dry_run))
