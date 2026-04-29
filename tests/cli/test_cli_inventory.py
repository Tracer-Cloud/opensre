from __future__ import annotations

import json
import unittest.mock

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.cli.tests.catalog import TestCatalogItem, TestRequirement
from app.cli.tests.runner import format_command, run_catalog_items


def test_tests_list_filters_ci_safe_inventory() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "list", "--category", "ci-safe"])

    assert result.exit_code == 0
    assert "make:test-cov" in result.output
    assert "make:test-full" in result.output
    assert "rca:pipeline_error_in_logs" not in result.output


def test_tests_run_dry_run_prints_command() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["tests", "run", "make:test-cov", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "make test-cov" in result.output


# --- Always-on discovery ---


def test_tests_list_works_in_non_interactive_env() -> None:
    """opensre tests list must succeed regardless of TUI availability."""
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "list"])

    assert result.exit_code == 0
    assert len(result.output.strip()) > 0


def test_stable_catalog_ids_always_present() -> None:
    """Core catalog IDs must be stable across runs."""
    runner = CliRunner()

    result = runner.invoke(cli, ["--json", "tests", "list"])

    assert result.exit_code == 0
    ids = {item["id"] for item in json.loads(result.output)}
    assert "make:test-cov" in ids
    assert "make:test-full" in ids


# --- Filtering ---


def test_tests_list_search_filter_narrows_results() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "list", "--search", "pipeline"])

    assert result.exit_code == 0
    assert "rca:pipeline_error_in_logs" in result.output
    assert "make:test-cov" not in result.output


def test_tests_list_category_synthetic() -> None:
    """--category synthetic must be accepted and return real entries."""
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "list", "--category", "synthetic"])

    assert result.exit_code == 0
    assert "synthetic:001-replication-lag" in result.output


def test_tests_list_category_rca_excludes_make_targets() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "list", "--category", "rca"])

    assert result.exit_code == 0
    assert "make:test-cov" not in result.output


def test_tests_list_search_no_match_returns_empty() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "list", "--search", "zzz_no_match_xyz_abc"])

    assert result.exit_code == 0
    assert result.output.strip() == ""


# --- JSON output ---


def test_tests_list_json_output_has_required_fields() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--json", "tests", "list"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) > 0
    first = data[0]
    assert {"id", "name", "tags", "description", "children"} <= set(first.keys())


def test_tests_list_json_filtered_by_category() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--json", "tests", "list", "--category", "ci-safe"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = {item["id"] for item in data}
    assert "make:test-cov" in ids
    assert "rca:pipeline_error_in_logs" not in ids


# --- Helpful errors ---


def test_tests_run_unknown_id_gives_helpful_error() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["tests", "run", "make:does-not-exist-xyz"])

    assert result.exit_code != 0
    output = result.output
    assert "does-not-exist-xyz" in output
    assert "opensre tests list" in output


def test_tests_no_subcommand_non_interactive_gives_clear_error() -> None:
    """opensre tests with no subcommand in a non-tty env must not traceback."""
    runner = CliRunner()

    result = runner.invoke(cli, ["tests"])

    # Must not exit with an unhandled exception / traceback
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # Should surface actionable guidance
    assert "tests list" in result.output or "tests run" in result.output or result.exit_code != 0


def test_tests_no_subcommand_missing_tui_deps_gives_opensre_error() -> None:
    """When questionary is absent the interactive path degrades to a structured error."""
    runner = CliRunner()

    with unittest.mock.patch(
        "app.cli.tests.interactive._questionary",
        None,
    ):
        result = runner.invoke(cli, ["tests"])

    # Must not produce a raw traceback — exit with a structured error
    assert result.exception is None or isinstance(result.exception, SystemExit)
    output = result.output
    assert "traceback" not in output.lower()
    assert "tests list" in output or "tests run" in output or result.exit_code != 0


# --- Command rendering ---


def test_format_command_renders_make_target() -> None:
    item = TestCatalogItem(
        id="make:test-cov",
        kind="make_target",
        display_name="Coverage Suite",
        description="Run coverage.",
        command=("make", "test-cov"),
        tags=("ci-safe",),
        requirements=TestRequirement(),
    )

    assert format_command(item) == "make test-cov"


def test_format_command_renders_opensre_subcommand() -> None:
    item = TestCatalogItem(
        id="synthetic:001-replication-lag",
        kind="cli_command",
        display_name="001-replication-lag",
        description="Synthetic scenario.",
        command=("opensre", "tests", "synthetic", "--scenario", "001-replication-lag"),
        tags=("synthetic",),
        requirements=TestRequirement(env_vars=("ANTHROPIC_API_KEY",)),
    )

    assert "opensre" in format_command(item)
    assert "001-replication-lag" in format_command(item)


# --- runner.run_catalog_items non-runnable skip ---


def test_run_catalog_items_skips_non_runnable_and_prints_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    no_cmd = TestCatalogItem(
        id="suite:empty",
        kind="suite",
        display_name="Empty Suite",
        description="No command.",
        command=(),
        tags=(),
        requirements=TestRequirement(),
    )

    exit_code = run_catalog_items([no_cmd])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "suite:empty" in captured.err
    assert "Skipping" in captured.err


# ---------------------------------------------------------------------------
# Bundled-binary degradation for ``opensre tests synthetic`` (regression #1078)
#
# ``packaging/opensre.spec`` excludes the ``tests`` tree from PyInstaller
# bundles, so ``from tests.synthetic.rds_postgres.run_suite import main``
# raises ``ModuleNotFoundError`` in a packaged binary. Surface a clean
# ``OpenSREError`` instead of a raw traceback so users know to run from a
# source checkout.
# ---------------------------------------------------------------------------


def test_tests_synthetic_surfaces_clean_error_when_suite_not_bundled() -> None:
    runner = CliRunner()

    real_import = __import__

    def _fail_synthetic_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("tests.synthetic.rds_postgres"):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with unittest.mock.patch("builtins.__import__", side_effect=_fail_synthetic_import):
        result = runner.invoke(cli, ["tests", "synthetic", "--scenario", "001-replication-lag"])

    # OpenSREError is rendered by the CLI as a non-zero exit + a clear
    # message; we don't want the raw ModuleNotFoundError traceback.
    assert result.exit_code != 0
    output = result.output or ""
    assert "synthetic" in output.lower() or "rds_postgres" in output
    # No raw traceback or stdlib exception name reaches the user.
    assert "ModuleNotFoundError" not in output
    assert "Traceback" not in output
