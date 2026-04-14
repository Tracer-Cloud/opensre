"""DatadogBackend Protocol and FixtureDatadogBackend for synthetic K8s testing.

The Protocol defines the minimal Datadog surface the Kubernetes investigation
agent uses.  FixtureDatadogBackend satisfies it by serving scenario fixture
data in the exact shape the Datadog tools under ``app/tools/DataDog*/`` return.

Usage
-----
    resolved_integrations = {
        "datadog": {
            "api_key": "",
            "app_key": "",
            "_backend": FixtureDatadogBackend(fixture),
        }
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tests.synthetic.eks.scenario_loader import K8sScenarioFixture


_ERROR_KEYWORDS = (
    "error",
    "fail",
    "exception",
    "traceback",
    "pipeline_error",
    "critical",
    "killed",
    "oomkilled",
    "crash",
    "panic",
    "timeout",
)


@runtime_checkable
class DatadogBackend(Protocol):
    """Minimal Datadog interface used by the Kubernetes investigation agent.

    Two methods — one per evidence source under ``app/tools/DataDog*/``:
        query_logs     → DataDogLogsTool response shape
        query_monitors → DataDogMonitorsTool response shape
    """

    def query_logs(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        """Return a response matching ``query_datadog_logs``."""

    def query_monitors(self, query: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Return a response matching ``query_datadog_monitors``."""


class FixtureDatadogBackend:
    """DatadogBackend implementation backed by a K8sScenarioFixture.

    Each method wraps the corresponding fixture file in the envelope that the
    real tool function returns.  Calling a method for an evidence source that
    the scenario did not declare in ``available_evidence`` raises ValueError.
    """

    def __init__(self, fixture: K8sScenarioFixture) -> None:
        self._fixture = fixture

    def query_logs(self, query: str = "", **_: Any) -> dict[str, Any]:
        logs_fixture = self._fixture.evidence.datadog_logs
        if logs_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: query_logs called but "
                "'datadog_logs' is not declared in available_evidence"
            )
        logs = list(logs_fixture.get("logs", []))
        error_logs = [
            log
            for log in logs
            if any(kw in str(log.get("message", "")).lower() for kw in _ERROR_KEYWORDS)
        ]
        return {
            "source": "datadog_logs",
            "available": True,
            "logs": logs,
            "error_logs": error_logs,
            "total": len(logs),
            "query": query,
        }

    def query_monitors(self, query: str | None = None, **_: Any) -> dict[str, Any]:
        monitors_fixture = self._fixture.evidence.datadog_monitors
        if monitors_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: query_monitors called but "
                "'datadog_monitors' is not declared in available_evidence"
            )
        monitors = list(monitors_fixture.get("monitors", []))
        return {
            "source": "datadog_monitors",
            "available": True,
            "monitors": monitors,
            "total": len(monitors),
            "query_filter": query,
        }
