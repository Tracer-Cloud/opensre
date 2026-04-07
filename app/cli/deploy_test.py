"""Tests for the deploy command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.cli.deploy import (
    _extract_railway_url,
    deploy_to_railway,
    get_railway_auth_status,
    is_railway_cli_installed,
    run_deploy,
)


class TestIsRailwayCliInstalled:
    """Tests for is_railway_cli_installed function."""

    def test_returns_true_when_cli_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(*args, **kwargs):  # noqa: ARG001
            class Result:
                returncode = 0

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run)
        assert is_railway_cli_installed() is True

    def test_returns_false_when_cli_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(*args, **kwargs):  # noqa: ARG001
            class Result:
                returncode = 127

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run)
        assert is_railway_cli_installed() is False


class TestGetRailwayAuthStatus:
    """Tests for get_railway_auth_status function."""

    def test_authenticated_when_whoami_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(*args, **kwargs):  # noqa: ARG001
            class Result:
                returncode = 0
                stdout = "user@example.com"
                stderr = ""

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run)
        result = get_railway_auth_status()
        assert result["authenticated"] is True
        assert result["detail"] == "user@example.com"

    def test_not_authenticated_when_whoami_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run(*args, **kwargs):  # noqa: ARG001
            class Result:
                returncode = 1
                stdout = ""
                stderr = "Not logged in"

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run)
        result = get_railway_auth_status()
        assert result["authenticated"] is False
        detail = result["detail"]
        assert isinstance(detail, str) and "Not logged in" in detail


class TestExtractRailwayUrl:
    """Tests for _extract_railway_url function."""

    def test_extracts_railway_app_url(self) -> None:
        stdout = "Deployed at https://myapp.up.railway.app"
        assert _extract_railway_url(stdout) == "https://myapp.up.railway.app"

    def test_extracts_railway_app_url_from_stderr(self) -> None:
        stderr = "Live at https://myapp.up.railway.app"
        assert _extract_railway_url(stderr) == "https://myapp.up.railway.app"

    def test_returns_last_match(self) -> None:
        stdout = "Old: https://old.up.railway.app New: https://new.up.railway.app"
        assert _extract_railway_url(stdout) == "https://new.up.railway.app"

    def test_returns_none_when_no_url(self) -> None:
        stdout = "No deployment URL found"
        assert _extract_railway_url(stdout) is None


class TestDeployToRailway:
    """Tests for deploy_to_railway function."""

    def test_returns_error_when_cli_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: False)
        result = deploy_to_railway()
        assert result["success"] is False
        error = result.get("error")
        assert isinstance(error, str) and "not found" in error.lower()

    def test_returns_error_when_not_authenticated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": False, "detail": "Not logged in"},
        )
        result = deploy_to_railway()
        assert result["success"] is False
        error = result.get("error")
        assert isinstance(error, str) and "Not authenticated" in error

    def test_dry_run_returns_success(self) -> None:
        result = deploy_to_railway(dry_run=True)
        assert result["success"] is True
        assert result["dry_run"] is True
        assert "dry-run" in result["logs"][0].lower()

    def test_successful_deploy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": True, "detail": "user@example.com"},
        )

        commands_run: list[list[str]] = []

        def mock_run_command(cmd: list[str], **kwargs: object) -> object:  # noqa: ARG001
            commands_run.append(cmd)

            class Result:
                returncode = 0
                stdout = "https://myapp.up.railway.app"
                stderr = ""

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run_command)

        # Mock httpx.get for health check
        class MockResponse:
            status_code = 200

        monkeypatch.setattr("httpx.get", lambda *_a, **_kw: MockResponse())
        monkeypatch.setattr("time.sleep", lambda _x: None)
        monkeypatch.setattr("time.time", lambda: 0)

        result = deploy_to_railway(wait_for_health=False)
        assert result["success"] is True
        assert result["url"] == "https://myapp.up.railway.app"

    def test_health_check_uses_ok_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": True, "detail": "user@example.com"},
        )

        def mock_run_command(cmd: list[str], **kwargs: object) -> object:  # noqa: ARG001
            class Result:
                returncode = 0
                stdout = "https://myapp.up.railway.app"
                stderr = ""

            return Result()

        requested_urls: list[str] = []

        class MockResponse:
            status_code = 200

        def mock_httpx_get(url: str, **_kwargs: object) -> MockResponse:
            requested_urls.append(url)
            return MockResponse()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run_command)
        monkeypatch.setattr("httpx.get", mock_httpx_get)
        monkeypatch.setattr("time.sleep", lambda _x: None)

        times = iter([0.0, 0.0, 0.1])
        monkeypatch.setattr("time.time", lambda: next(times))

        result = deploy_to_railway(wait_for_health=True)
        assert result["success"] is True
        assert requested_urls == ["https://myapp.up.railway.app/ok"]

    def test_domain_fallback_adds_https_scheme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": True, "detail": "user@example.com"},
        )

        command_calls = {"count": 0}

        def mock_run_command(cmd: list[str], **kwargs: object) -> object:  # noqa: ARG001
            command_calls["count"] += 1

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            result = Result()
            if cmd[:2] == ["railway", "domain"]:
                result.stdout = "myapp.up.railway.app"
            return result

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run_command)

        result = deploy_to_railway(wait_for_health=False)
        assert result["success"] is True
        assert result["url"] == "https://myapp.up.railway.app"

    def test_prevalidated_auth_logs_expected_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.cli.deploy.is_railway_cli_installed",
            lambda: pytest.fail("CLI check should be skipped"),
        )
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: pytest.fail("Auth check should be skipped"),
        )

        def mock_run_command(cmd: list[str], **kwargs: object) -> object:  # noqa: ARG001
            class Result:
                returncode = 0
                stdout = "https://myapp.up.railway.app"
                stderr = ""

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run_command)

        result = deploy_to_railway(wait_for_health=False, auth_detail="user@example.com")
        assert result["success"] is True
        assert result["logs"][0] == "Using pre-validated Railway auth"

    def test_skips_duplicate_prereq_checks_with_prevalidated_auth(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "app.cli.deploy.is_railway_cli_installed",
            lambda: pytest.fail("CLI check should be skipped"),
        )
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: pytest.fail("Auth check should be skipped"),
        )

        def mock_run_command(cmd: list[str], **kwargs: object) -> object:  # noqa: ARG001
            class Result:
                returncode = 0
                stdout = "https://myapp.up.railway.app"
                stderr = ""

            return Result()

        monkeypatch.setattr("app.cli.deploy._run_command", mock_run_command)

        result = deploy_to_railway(wait_for_health=False, auth_detail="user@example.com")
        assert result["success"] is True


class TestRunDeploy:
    """Tests for run_deploy function."""

    def test_returns_error_for_unsupported_target(self) -> None:
        rc = run_deploy(target="unsupported", yes=True)
        assert rc == 1

    def test_returns_error_when_cli_not_installed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: False)
        rc = run_deploy(target="railway", yes=True)
        assert rc == 1

    def test_returns_error_when_not_authenticated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": False, "detail": "Not logged in"},
        )
        rc = run_deploy(target="railway", yes=True)
        assert rc == 1

    def test_successful_deploy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": True, "detail": "user@example.com"},
        )
        received_kwargs: dict[str, object] = {}

        def mock_deploy_to_railway(**kwargs: object) -> dict[str, object]:
            received_kwargs.update(kwargs)
            return {
                "success": True,
                "url": "https://myapp.up.railway.app",
                "logs": ["Deployment started", "Deployment complete"],
                "health_ok": True,
            }

        monkeypatch.setattr(
            "app.cli.deploy.deploy_to_railway",
            mock_deploy_to_railway,
        )
        rc = run_deploy(target="railway", yes=True)
        assert rc == 0
        assert received_kwargs["auth_detail"] == "user@example.com"

    def test_dry_run_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.cli.deploy.is_railway_cli_installed", lambda: True)
        monkeypatch.setattr(
            "app.cli.deploy.get_railway_auth_status",
            lambda: {"authenticated": True, "detail": "user@example.com"},
        )
        monkeypatch.setattr(
            "app.cli.deploy.deploy_to_railway",
            lambda **_kw: {
                "success": True,
                "url": "https://example.up.railway.app",
                "logs": ["[dry-run] Would deploy"],
                "dry_run": True,
            },
        )
        rc = run_deploy(target="railway", dry_run=True, yes=True)
        assert rc == 0


class TestDeployCommand:
    """Tests for the deploy CLI command."""

    def test_deploy_command_railway_success(self) -> None:
        runner = CliRunner()

        with (
            patch("app.cli.deploy.is_railway_cli_installed", return_value=True),
            patch(
                "app.cli.deploy.get_railway_auth_status",
                return_value={"authenticated": True, "detail": "user@example.com"},
            ),
            patch(
                "app.cli.deploy.deploy_to_railway",
                return_value={
                    "success": True,
                    "url": "https://myapp.up.railway.app",
                    "logs": ["Deployed"],
                    "health_ok": True,
                },
            ),
        ):
            result = runner.invoke(cli, ["deploy", "railway", "--yes"])

        assert result.exit_code == 0
        assert "railway" in result.output.lower() or "Deployed" in result.output

    def test_deploy_command_with_project_and_service(self) -> None:
        runner = CliRunner()

        captured: dict[str, object] = {}

        def _deploy(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "success": True,
                "url": "https://myapp.up.railway.app",
                "logs": ["Deployed"],
                "health_ok": True,
            }

        with (
            patch("app.cli.deploy.is_railway_cli_installed", return_value=True),
            patch(
                "app.cli.deploy.get_railway_auth_status",
                return_value={"authenticated": True, "detail": "user@example.com"},
            ),
            patch("app.cli.deploy.deploy_to_railway", side_effect=_deploy),
        ):
            result = runner.invoke(
                cli,
                [
                    "deploy",
                    "railway",
                    "--project",
                    "myproject",
                    "--service",
                    "myservice",
                    "--yes",
                ],
            )

        assert result.exit_code == 0
        assert captured["project_name"] == "myproject"
        assert captured["service_name"] == "myservice"

    def test_deploy_command_requires_subcommand_when_yes(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["--yes", "deploy"])

        assert result.exit_code == 1
        assert "no subcommand provided" in result.output.lower()

    def test_deploy_command_dry_run(self) -> None:
        runner = CliRunner()

        with (
            patch("app.cli.deploy.is_railway_cli_installed", return_value=True),
            patch(
                "app.cli.deploy.get_railway_auth_status",
                return_value={"authenticated": True, "detail": "user@example.com"},
            ),
            patch(
                "app.cli.deploy.deploy_to_railway",
                return_value={
                    "success": True,
                    "url": "https://example.up.railway.app",
                    "logs": ["[dry-run] Would deploy"],
                    "dry_run": True,
                },
            ),
        ):
            result = runner.invoke(cli, ["deploy", "railway", "--dry-run"])

        assert result.exit_code == 0
        assert "dry" in result.output.lower()

    def test_deploy_command_missing_cli(self) -> None:
        runner = CliRunner()

        with patch("app.cli.deploy.is_railway_cli_installed", return_value=False):
            result = runner.invoke(cli, ["deploy", "railway", "--yes"])

        assert result.exit_code == 1
        assert "not installed" in result.output.lower()

    def test_deploy_command_not_authenticated(self) -> None:
        runner = CliRunner()

        with (
            patch("app.cli.deploy.is_railway_cli_installed", return_value=True),
            patch(
                "app.cli.deploy.get_railway_auth_status",
                return_value={"authenticated": False, "detail": "Not logged in"},
            ),
        ):
            result = runner.invoke(cli, ["deploy", "railway", "--yes"])

        assert result.exit_code == 1
        assert "not authenticated" in result.output.lower()
