from __future__ import annotations

import json
import unittest.mock
from pathlib import Path

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


def test_tests_synthetic_clean_error_when_data_dir_missing(tmp_path: Path) -> None:
    """Real bundled-binary failure mode (per live PyInstaller verification):
    the ``tests.synthetic.rds_postgres`` Python package is bundled
    transitively but the per-scenario data directories are absent. Pre-check
    on the data dir must surface a structured error before the import fires."""
    runner = CliRunner()

    # Point SYNTHETIC_SCENARIOS_DIR at a path that doesn't exist so the
    # pre-check in ``run_synthetic_suite`` short-circuits to OpenSREError.
    missing = tmp_path / "missing-rds-postgres"
    with unittest.mock.patch("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", missing):
        result = runner.invoke(cli, ["tests", "synthetic", "--scenario", "001-replication-lag"])

    output = result.output or ""
    assert result.exit_code == 1, f"unexpected exit code {result.exit_code}; output={output!r}"
    # Pin the contractual message — these strings are user-facing and a
    # silent rewording would be a regression for support docs / scripts.
    assert "synthetic RDS PostgreSQL suite is not available" in output
    assert "pip install -e ." in output
    # No raw traceback or stdlib exception name reaches the user.
    assert "FileNotFoundError" not in output
    assert "ModuleNotFoundError" not in output
    assert "Traceback" not in output


def test_tests_synthetic_clean_error_when_module_not_bundled(tmp_path: Path) -> None:
    """Adjacent failure mode: the synthetic Python package is missing
    entirely. The narrowed ``ModuleNotFoundError`` catch must convert it
    into the same structured error."""
    runner = CliRunner()

    real_import = __import__

    def _fail_synthetic_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("tests.synthetic.rds_postgres"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    # Make the data-dir pre-check pass so the import path is what fails.
    scenarios_dir = tmp_path / "rds_postgres"
    (scenarios_dir / "001-replication-lag").mkdir(parents=True)

    with (
        unittest.mock.patch("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", scenarios_dir),
        unittest.mock.patch("builtins.__import__", side_effect=_fail_synthetic_import),
    ):
        result = runner.invoke(cli, ["tests", "synthetic", "--scenario", "001-replication-lag"])

    output = result.output or ""
    assert result.exit_code == 1, f"unexpected exit code {result.exit_code}; output={output!r}"
    assert "synthetic RDS PostgreSQL suite is not available" in output
    assert "ModuleNotFoundError" not in output
    assert "Traceback" not in output


def test_tests_synthetic_unrelated_module_not_found_propagates(tmp_path: Path) -> None:
    """Narrow-catch contract: if the synthetic suite *is* available but a
    transitive dep (e.g. ``psycopg``) is missing, the user must see the
    real cause — not a misleading "not bundled" message."""
    runner = CliRunner()

    real_import = __import__

    def _fail_unrelated_import(name: str, *args: object, **kwargs: object) -> object:
        # Synthetic module's run_suite fails because of a missing transitive
        # dep (``psycopg``), not because the synthetic package is absent.
        if name == "tests.synthetic.rds_postgres.run_suite":
            raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    scenarios_dir = tmp_path / "rds_postgres"
    (scenarios_dir / "001-replication-lag").mkdir(parents=True)

    with (
        unittest.mock.patch("app.cli.tests.discover.SYNTHETIC_SCENARIOS_DIR", scenarios_dir),
        unittest.mock.patch("builtins.__import__", side_effect=_fail_unrelated_import),
    ):
        result = runner.invoke(cli, ["tests", "synthetic", "--scenario", "001-replication-lag"])

    output = result.output or ""
    # The misleading "not bundled" message MUST NOT be shown — the user
    # needs to see the real missing-dep cause so they can fix it.
    assert "synthetic RDS PostgreSQL suite is not available" not in output


# ---------------------------------------------------------------------------
# Bundled-binary degradation for ``opensre deploy ec2`` (audit finding)
#
# Same class of bug as #1078: ``app/cli/commands/deploy.py:323,329`` import
# from ``tests.deployment.ec2.infrastructure_sdk`` which is excluded from
# PyInstaller bundles. Without a guard, ``opensre deploy ec2`` and
# ``opensre deploy ec2 --down`` both crash with raw ModuleNotFoundError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["deploy", "ec2"],
        ["deploy", "ec2", "--down"],
    ],
)
def test_deploy_ec2_clean_error_when_not_bundled(argv: list[str]) -> None:
    runner = CliRunner()

    real_import = __import__

    def _fail_ec2_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("tests.deployment.ec2"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with unittest.mock.patch("builtins.__import__", side_effect=_fail_ec2_import):
        result = runner.invoke(cli, argv)

    output = result.output or ""
    assert result.exit_code == 1, f"unexpected exit code {result.exit_code}; output={output!r}"
    assert "EC2 deployment is not available" in output
    assert "pip install -e ." in output
    assert "ModuleNotFoundError" not in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    ("argv", "patched_module"),
    [
        # Destroy path (--down): try/except wraps `destroy_remote` import.
        (
            ["deploy", "ec2", "--down"],
            "tests.deployment.ec2.infrastructure_sdk.destroy_remote",
        ),
        # Deploy path (no --down): try/except wraps `deploy_remote` import,
        # which has its own narrow-catch in deploy_ec2 — covered separately
        # so a regression in either branch is caught independently.
        (
            ["deploy", "ec2"],
            "tests.deployment.ec2.infrastructure_sdk.deploy_remote",
        ),
    ],
)
def test_deploy_ec2_unrelated_module_not_found_propagates(
    argv: list[str], patched_module: str
) -> None:
    """Narrow-catch contract for EC2 deploy: an unrelated transitive
    missing dep (e.g. ``boto3``) must surface as the real error, not the
    "EC2 not bundled" suggestion. Both ``deploy ec2`` and ``deploy ec2 --down``
    have their own try/except blocks — exercise both."""
    runner = CliRunner()

    real_import = __import__

    def _fail_unrelated_import(name: str, *args: object, **kwargs: object) -> object:
        if name == patched_module:
            raise ModuleNotFoundError("No module named 'boto3'", name="boto3")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with unittest.mock.patch("builtins.__import__", side_effect=_fail_unrelated_import):
        result = runner.invoke(cli, argv)

    output = result.output or ""
    assert "EC2 deployment is not available" not in output
