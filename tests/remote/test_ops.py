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


def test_railway_provider_scopes_with_link_when_project_provided() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj-a", service="svc-a")

    def _fake_run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)

        class Result:
            def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        if cmd[:2] == ["railway", "link"]:
            return Result(0, stdout="{}")
        if cmd == ["railway", "status", "--json"]:
            return Result(0, stdout='{"service":"svc-a","project":"proj-a"}')
        return Result(1, stderr="unexpected command")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_fake_run),
    ):
        status = provider.status(scope)

    assert status.project == "proj-a"
    assert status.service == "svc-a"
