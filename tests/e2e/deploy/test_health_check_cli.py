from __future__ import annotations

import importlib

from app.deployment.health import HealthPollStatus

deploy_module = importlib.import_module("app.cli.commands.deploy")


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, message: str = "") -> None:
        self.lines.append(message)


def test_check_deploy_health_reports_success(monkeypatch) -> None:
    console = _Console()

    def _ok_health_poll(*args, **kwargs) -> HealthPollStatus:
        del args, kwargs
        return HealthPollStatus(
            url="http://18.0.0.1:8080/health",
            attempts=2,
            status_code=200,
            elapsed_seconds=3.1,
        )

    monkeypatch.setattr(
        deploy_module,
        "poll_deployment_health",
        _ok_health_poll,
    )

    deploy_module._check_deploy_health({"ip": "18.0.0.1", "port": "8080"}, console)

    output = "\n".join(console.lines)
    assert "Healthy" in output
    assert "18.0.0.1:8080/health" in output


def test_check_deploy_health_reports_timeout(monkeypatch) -> None:
    console = _Console()

    def _timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(deploy_module, "poll_deployment_health", _timeout)

    deploy_module._check_deploy_health({"ip": "18.0.0.1", "port": "8080"}, console)

    output = "\n".join(console.lines)
    assert "Timeout" in output
