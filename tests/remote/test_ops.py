from __future__ import annotations

from unittest.mock import patch

import pytest

from app.remote.ops import (
    RailwayRemoteOpsProvider,
    RemoteOpsError,
    RemoteOpsProvider,
    RemoteServiceScope,
    resolve_remote_ops_provider,
)


def test_resolve_remote_ops_provider_supports_railway() -> None:
    provider = resolve_remote_ops_provider("railway")
    assert isinstance(provider, RailwayRemoteOpsProvider)


def test_resolve_remote_ops_provider_rejects_unknown() -> None:
    with pytest.raises(RemoteOpsError):
        resolve_remote_ops_provider("unknown")


def test_remote_ops_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        RemoteOpsProvider()


def test_railway_provider_requires_cli_installed() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway")

    with patch("app.remote.ops.shutil.which", return_value=None), pytest.raises(RemoteOpsError):
        provider.status(scope)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_railway_provider_scopes_with_link_when_project_provided() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj-a", service="svc-a")

    def _fake_run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)

        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        if cmd == ["railway", "status", "--json"]:
            return _Result(0, stdout='{"service":"svc-a","project":"proj-a"}')
        return _Result(1, stderr="unexpected command")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_fake_run),
    ):
        status = provider.status(scope)

    assert status.project == "proj-a"
    assert status.service == "svc-a"


def test_railway_provider_logs() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    captured: list[list[str]] = []

    def _run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)
        captured.append(list(cmd))
        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        return _Result(0)

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_run),
    ):
        provider.logs(scope, lines=10, follow=True)

    logs_cmds = [c for c in captured if c[:2] == ["railway", "logs"]]
    assert len(logs_cmds) == 1, f"Expected exactly one 'railway logs' call, got: {logs_cmds}"
    assert logs_cmds[0] == ["railway", "logs", "--tail", "10", "--follow"]


def test_railway_provider_fetch_logs() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    def _run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)
        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        if cmd[:2] == ["railway", "logs"]:
            return _Result(0, stdout="line 1\nline 2", stderr="warning")
        return _Result(1)

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_run),
    ):
        result = provider.fetch_logs(scope, lines=10)

    assert "line 1\nline 2" in result
    assert "[stderr: warning]" in result


def test_railway_provider_fetch_logs_stderr_only() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    def _run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)
        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        if cmd[:2] == ["railway", "logs"]:
            return _Result(0, stdout="", stderr="only stderr")
        return _Result(1)

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_run),
    ):
        result = provider.fetch_logs(scope, lines=10)

    assert result == "only stderr"


def test_railway_provider_restart() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    def _run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)
        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        if cmd[:2] == ["railway", "redeploy"]:
            return _Result(0, stdout='{"id":"dep-123","status":"REDEPLOYING"}')
        return _Result(1)

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_run),
    ):
        result = provider.restart(scope)

    assert result.deployment_id == "dep-123"
    assert "REDEPLOYING" in result.message


def test_railway_provider_read_json_invalid_json() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", return_value=_Result(0, stdout="invalid-json")),
        pytest.raises(RemoteOpsError, match="Failed to parse Railway JSON output"),
    ):
        provider.status(scope)


def test_railway_provider_read_json_not_a_dict() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", return_value=_Result(0, stdout="[]")),
        pytest.raises(RemoteOpsError, match="Unexpected Railway JSON output shape"),
    ):
        provider.status(scope)
