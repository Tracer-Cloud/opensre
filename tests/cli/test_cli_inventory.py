from __future__ import annotations

from click.testing import CliRunner

from app.cli.__main__ import cli


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
