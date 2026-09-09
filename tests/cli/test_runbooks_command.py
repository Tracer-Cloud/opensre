from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from config import local_settings
from surfaces.cli.app import cli
from surfaces.cli.commands import runbooks


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_settings.paths, "OPENSRE_HOME_DIR", tmp_path)


def test_source_add_list_and_remove() -> None:
    runner = CliRunner()

    added = runner.invoke(
        cli,
        [
            "runbooks",
            "add",
            "github",
            "--name",
            "platform-runbooks",
            "--repo",
            "acme/operations",
            "--ref",
            "main",
            "--manifest",
            ".opensre/runbooks.yaml",
        ],
    )
    listed = runner.invoke(cli, ["runbooks", "list"])
    removed = runner.invoke(
        cli,
        ["runbooks", "remove", "platform-runbooks"],
    )

    assert added.exit_code == 0, added.output
    assert "platform-runbooks" in added.output
    assert listed.exit_code == 0, listed.output
    assert "platform-runbooks" in listed.output
    assert "acme/operations" in listed.output
    assert ".opensre/runbooks.yaml" in listed.output
    assert removed.exit_code == 0, removed.output
    assert "Removed" in removed.output


def test_duplicate_source_name_is_a_cli_error() -> None:
    runner = CliRunner()
    args = [
        "runbooks",
        "add",
        "github",
        "--name",
        "platform-runbooks",
        "--repo",
        "acme/operations",
    ]

    assert runner.invoke(cli, args).exit_code == 0
    duplicate = runner.invoke(cli, args)

    assert duplicate.exit_code != 0
    assert "already exists" in duplicate.output


def test_empty_source_list_has_actionable_output() -> None:
    result = CliRunner().invoke(cli, ["runbooks", "list"])

    assert result.exit_code == 0, result.output
    assert "No runbook sources configured" in result.output


def test_source_verify_reports_provider_result(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    added = runner.invoke(
        cli,
        [
            "runbooks",
            "add",
            "github",
            "--name",
            "platform-runbooks",
            "--repo",
            "acme/operations",
        ],
    )
    assert added.exit_code == 0, added.output
    monkeypatch.setattr(
        runbooks,
        "_verify_source",
        lambda _source: (True, "Verified access to acme/operations@main."),
    )

    result = runner.invoke(
        cli,
        ["runbooks", "verify", "platform-runbooks"],
    )

    assert result.exit_code == 0, result.output
    assert "Verified access" in result.output


def test_source_verify_fails_when_provider_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    added = runner.invoke(
        cli,
        [
            "runbooks",
            "add",
            "github",
            "--name",
            "platform-runbooks",
            "--repo",
            "acme/operations",
        ],
    )
    assert added.exit_code == 0, added.output
    monkeypatch.setattr(
        runbooks,
        "_verify_source",
        lambda _source: (False, "GitHub integration is unavailable."),
    )

    result = runner.invoke(
        cli,
        ["runbooks", "verify", "platform-runbooks"],
    )

    assert result.exit_code != 0
    assert "GitHub integration is unavailable" in result.output
