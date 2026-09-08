"""Coverage for tool-level Sentry capture.

``test_tool_reports_exactly_one_sentry_event`` is the parameterised
"every migrated tool reports a Sentry event when its underlying client
raises" assertion called out in #1463 acceptance criteria. Each row
forces the client used by the tool body to raise and verifies the helper
produced exactly one event with the expected ``surface=tool``,
``tool_name``, and ``source`` tags.

``test_eks_client_error_path_uses_warning_severity`` exercises the EKS
``except ClientError`` branch (the whole reason for the severity split)
by patching the underlying client to raise ``botocore.exceptions.ClientError``
and asserting the helper logged at ``WARNING``, not ``ERROR``.

Direct ``report_run_error`` helper tests live in
``tests/core/tool_framework/test_telemetry.py``.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


@dataclass
class CapturedSentryEvent:
    """One Sentry capture, with the scope extras that were attached.

    ``report_exception`` flattens tags into ``extra`` with a ``tag.`` prefix
    (see ``utils/errors.py``), so a tag set via
    ``report_run_error(tool_name="X")`` shows up here as
    ``extras["tag.tool_name"] == "X"``.
    """

    exc: BaseException
    extras: dict[str, Any]


@pytest.fixture
def captured_sentry_events(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[CapturedSentryEvent]]:
    """Patch the Sentry SDK so every capture lands in a local list.

    Tests rely on this rather than the real ``sentry_sdk`` because:
      * ``conftest`` sets ``OPENSRE_SENTRY_DISABLED=1`` to keep the suite
        offline — we re-enable it here.
      * ``capture_exception`` and ``push_scope`` both need to be present
        for the contextual-tag path inside ``infrastructure.observability.errors.sentry``.

    The mock ``push_scope`` returns a per-call ``_Scope`` instance that
    records every ``set_extra`` and ``set_tag`` call. ``capture_exception``
    snapshots the current scope's extras alongside the exception so tests
    can assert on the tags that reached Sentry.
    """
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    events: list[CapturedSentryEvent] = []
    scope_stack: list[_RecordingScope] = []

    class _RecordingScope:
        def __init__(self) -> None:
            self.extras: dict[str, Any] = {}

        def __enter__(self) -> _RecordingScope:
            scope_stack.append(self)
            return self

        def __exit__(self, *_args: object) -> None:
            if scope_stack and scope_stack[-1] is self:
                scope_stack.pop()
            return None

        def set_tag(self, key: str, value: str) -> None:
            # Mirror the existing ``report_exception`` convention so tests
            # see a single flat extras dict regardless of whether a value
            # was attached via set_tag or set_extra.
            self.extras[f"tag.{key}"] = value

        def set_extra(self, key: str, value: object) -> None:
            self.extras[key] = value

    def _capture(exc: BaseException) -> None:
        current_extras = dict(scope_stack[-1].extras) if scope_stack else {}
        events.append(CapturedSentryEvent(exc=exc, extras=current_extras))

    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(capture_exception=_capture, push_scope=_RecordingScope),
    )
    yield events


# ---------------------------------------------------------------------------
# Parameterised tool coverage
#
# Each case patches the lowest-level dependency the tool reaches for and forces
# it to raise. The helper must then produce exactly one Sentry event so the
# silent ``{"available": False}`` return is no longer invisible to operators.
# ---------------------------------------------------------------------------


@dataclass
class ToolFailureCase:
    id: str
    patch: Callable[[pytest.MonkeyPatch], None]
    invoke: Callable[[], dict[str, Any]]
    expected_tool_name: str
    expected_source: str


def _azure_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.azure.tools import azure_monitor_logs_tool as mod

        mp.setattr(mod, "httpx", SimpleNamespace(post=MagicMock(side_effect=RuntimeError("net"))))

    def invoke() -> dict[str, Any]:
        from integrations.azure.tools.azure_monitor_logs_tool import query_azure_monitor_logs

        return query_azure_monitor_logs(workspace_id="w", access_token="t")

    return ToolFailureCase("azure_monitor_logs", patch, invoke, "query_azure_monitor_logs", "azure")


def _openobserve_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.openobserve.tools import openobserve_logs_tool as mod

        mp.setattr(mod, "httpx", SimpleNamespace(post=MagicMock(side_effect=RuntimeError("net"))))

    def invoke() -> dict[str, Any]:
        from integrations.openobserve.tools.openobserve_logs_tool import query_openobserve_logs

        return query_openobserve_logs(
            base_url="https://oo.example",
            org="default",
            stream="default",
            query="*",
            api_token="t",
        )

    return ToolFailureCase(
        "openobserve_logs", patch, invoke, "query_openobserve_logs", "openobserve"
    )


def _snowflake_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.snowflake.tools import snowflake_query_history_tool as mod

        mp.setattr(mod, "httpx", SimpleNamespace(post=MagicMock(side_effect=RuntimeError("net"))))

    def invoke() -> dict[str, Any]:
        from integrations.snowflake.tools.snowflake_query_history_tool import (
            query_snowflake_history,
        )

        return query_snowflake_history(
            account_identifier="acc",
            token="tok",
            query="select 1",
        )

    return ToolFailureCase(
        "snowflake_query_history", patch, invoke, "query_snowflake_history", "snowflake"
    )


def _cloudwatch_logs_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.cloudwatch.tools import cloudwatch_logs_tool as mod

        mp.setattr(
            mod,
            "boto3",
            SimpleNamespace(client=MagicMock(side_effect=RuntimeError("aws"))),
        )

    def invoke() -> dict[str, Any]:
        from integrations.cloudwatch.tools.cloudwatch_logs_tool import get_cloudwatch_logs

        return get_cloudwatch_logs(log_group="/aws/lambda/test")

    return ToolFailureCase("cloudwatch_logs", patch, invoke, "get_cloudwatch_logs", "cloudwatch")


def _cloudwatch_batch_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.cloudwatch.tools import cloudwatch_batch_metrics_tool as mod

        mp.setattr(
            mod,
            "get_metric_statistics",
            MagicMock(side_effect=RuntimeError("aws")),
        )

    def invoke() -> dict[str, Any]:
        from integrations.cloudwatch.tools.cloudwatch_batch_metrics_tool import (
            get_cloudwatch_batch_metrics,
        )

        return get_cloudwatch_batch_metrics(job_queue="q", metric_type="cpu")

    return ToolFailureCase(
        "cloudwatch_batch_metrics",
        patch,
        invoke,
        "get_cloudwatch_batch_metrics",
        "cloudwatch",
    )


def _google_docs_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        import integrations.google_docs.tools as mod

        mp.setattr(
            mod,
            "GoogleDocsClient",
            MagicMock(side_effect=RuntimeError("google")),
        )

    def invoke() -> dict[str, Any]:
        import integrations.google_docs.tools as mod

        return mod.create_google_docs_incident_report(
            title="t",
            summary="s",
            root_cause="rc",
            severity="low",
            credentials_file="/tmp/missing.json",
            folder_id="f",
        )

    return ToolFailureCase(
        "google_docs_create_report",
        patch,
        invoke,
        "create_google_docs_incident_report",
        "google_docs",
    )


def _github_repository_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.github.client import GitHubApiError

        mp.setattr(
            "integrations.github.tools.repository.GitHubRestClient.request",
            MagicMock(
                side_effect=GitHubApiError("not found", status_code=404, path="/repos/o/r"),
            ),
        )

    def invoke() -> dict[str, Any]:
        from integrations.github.tools.repository import get_github_repository

        return get_github_repository(owner="o", repo="r", github_token="tok")

    return ToolFailureCase(
        "github_repository",
        patch,
        invoke,
        "get_github_repository",
        "github",
    )


def _github_star_history_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.github.client import GitHubApiError

        def request(_method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
            if path == "/repos/o/r":
                return {"stargazers_count": 1}
            raise GitHubApiError("forbidden", status_code=403, path="/repos/o/r/stargazers")

        mp.setattr(
            "integrations.github.tools.stargazers.GitHubRestClient.request",
            MagicMock(side_effect=request),
        )

    def invoke() -> dict[str, Any]:
        from integrations.github.tools.stargazers import get_github_star_history

        return get_github_star_history(owner="o", repo="r", github_token="tok")

    return ToolFailureCase(
        "github_star_history",
        patch,
        invoke,
        "get_github_star_history",
        "github",
    )


def _github_ci_analytics_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.github.client import GitHubApiError
        from integrations.github.tools.ci_analytics import analysis as mod

        mp.setattr(mod, "collect_runs", MagicMock(side_effect=GitHubApiError("boom")))

    def invoke() -> dict[str, Any]:
        from integrations.github.tools.ci_analytics.tool import analyze_github_ci_reliability

        return analyze_github_ci_reliability(owner="o", repo="r", github_token="tok")

    return ToolFailureCase(
        "github_ci_analytics",
        patch,
        invoke,
        "analyze_github_ci_reliability",
        "github",
    )


def _eks_list_clusters_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_list_clusters_tool as mod

        mp.setattr(mod, "EKSClient", MagicMock(side_effect=RuntimeError("eks")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_list_clusters_tool as mod

        return mod.list_eks_clusters(role_arn="arn:aws:iam::123:role/x")

    return ToolFailureCase("eks_list_clusters", patch, invoke, "list_eks_clusters", "eks")


def _eks_describe_cluster_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_describe_cluster_tool as mod

        mp.setattr(mod, "EKSClient", MagicMock(side_effect=RuntimeError("eks")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_describe_cluster_tool as mod

        return mod.describe_eks_cluster(cluster_name="c", role_arn="arn:aws:iam::123:role/x")

    return ToolFailureCase("eks_describe_cluster", patch, invoke, "describe_eks_cluster", "eks")


def _eks_nodegroup_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_nodegroup_health_tool as mod

        mp.setattr(mod, "EKSClient", MagicMock(side_effect=RuntimeError("eks")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_nodegroup_health_tool as mod

        return mod.get_eks_nodegroup_health(
            cluster_name="c",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_nodegroup_health", patch, invoke, "get_eks_nodegroup_health", "eks")


def _eks_addon_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_describe_addon_tool as mod

        mp.setattr(mod, "EKSClient", MagicMock(side_effect=RuntimeError("eks")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_describe_addon_tool as mod

        return mod.describe_eks_addon(
            cluster_name="c",
            addon_name="coredns",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_describe_addon", patch, invoke, "describe_eks_addon", "eks")


def _eks_events_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_events_tool as mod

        mp.setattr(mod, "build_k8s_clients", MagicMock(side_effect=RuntimeError("k8s")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_events_tool as mod

        return mod.get_eks_events(
            cluster_name="c",
            namespace="default",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_events", patch, invoke, "get_eks_events", "eks")


def _eks_node_health_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_node_health_tool as mod

        mp.setattr(mod, "build_k8s_clients", MagicMock(side_effect=RuntimeError("k8s")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_node_health_tool as mod

        return mod.get_eks_node_health(
            cluster_name="c",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_node_health", patch, invoke, "get_eks_node_health", "eks")


def _eks_list_namespaces_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_list_namespaces_tool as mod

        mp.setattr(mod, "build_k8s_clients", MagicMock(side_effect=RuntimeError("k8s")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_list_namespaces_tool as mod

        return mod.list_eks_namespaces(
            cluster_name="c",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_list_namespaces", patch, invoke, "list_eks_namespaces", "eks")


def _eks_list_deployments_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_list_deployments_tool as mod

        mp.setattr(mod, "build_k8s_clients", MagicMock(side_effect=RuntimeError("k8s")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_list_deployments_tool as mod

        return mod.list_eks_deployments(
            cluster_name="c",
            namespace="default",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_list_deployments", patch, invoke, "list_eks_deployments", "eks")


def _eks_list_pods_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_list_pods_tool as mod

        mp.setattr(mod, "build_k8s_clients", MagicMock(side_effect=RuntimeError("k8s")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_list_pods_tool as mod

        return mod.list_eks_pods(
            cluster_name="c",
            namespace="default",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_list_pods", patch, invoke, "list_eks_pods", "eks")


def _eks_pod_logs_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.eks.tools import eks_pod_logs_tool as mod

        mp.setattr(mod, "build_k8s_clients", MagicMock(side_effect=RuntimeError("k8s")))

    def invoke() -> dict[str, Any]:
        from integrations.eks.tools import eks_pod_logs_tool as mod

        return mod.get_eks_pod_logs(
            cluster_name="c",
            namespace="default",
            pod_name="p",
            role_arn="arn:aws:iam::123:role/x",
        )

    return ToolFailureCase("eks_pod_logs", patch, invoke, "get_eks_pod_logs", "eks")


def _patch_posthog_mcp_runtime(mp: pytest.MonkeyPatch) -> None:
    """Shared patches for PostHog MCP cases — bypass the config/runtime guards."""
    from integrations.posthog_mcp.tools import posthog_mcp_tool as mod

    mp.setattr(
        mod,
        "posthog_mcp_config_from_env",
        MagicMock(
            return_value=SimpleNamespace(
                mode="streamable-http",
                command="",
                url="https://mcp.posthog.com/mcp",
                auth_token="phx_secret",
                args=(),
                headers={},
                organization_id="",
                project_id="",
                features=(),
                read_only=True,
            )
        ),
    )
    mp.setattr(mod, "posthog_mcp_runtime_unavailable_reason", MagicMock(return_value=None))
    mp.setattr(mod, "describe_posthog_mcp_error", MagicMock(return_value="mocked error"))


def _posthog_mcp_list_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.posthog_mcp.tools import posthog_mcp_tool as mod

        _patch_posthog_mcp_runtime(mp)
        mp.setattr(mod, "list_posthog_mcp_tools", MagicMock(side_effect=RuntimeError("mcp")))

    def invoke() -> dict[str, Any]:
        from integrations.posthog_mcp.tools.posthog_mcp_tool import list_posthog_tools

        return list_posthog_tools()

    return ToolFailureCase(
        "posthog_mcp_list_tools",
        patch,
        invoke,
        "list_posthog_tools",
        "posthog_mcp",
    )


def _posthog_mcp_call_tool_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.posthog_mcp.tools import posthog_mcp_tool as mod

        _patch_posthog_mcp_runtime(mp)
        mp.setattr(mod, "call_posthog_mcp_tool", MagicMock(side_effect=RuntimeError("mcp")))

    def invoke() -> dict[str, Any]:
        from integrations.posthog_mcp.tools.posthog_mcp_tool import call_posthog_tool

        return call_posthog_tool(tool_name="query-run", arguments={})

    return ToolFailureCase(
        "posthog_mcp_call_tool",
        patch,
        invoke,
        "call_posthog_tool",
        "posthog_mcp",
    )


def _patch_sentry_mcp_runtime(mp: pytest.MonkeyPatch) -> None:
    """Shared patches for Sentry MCP cases — bypass the config/runtime guards."""
    from integrations.sentry_mcp.tools import sentry_mcp_tool as mod

    mp.setattr(
        mod,
        "sentry_mcp_config_from_env",
        MagicMock(
            return_value=SimpleNamespace(
                mode="streamable-http",
                command="",
                url="https://mcp.sentry.dev/mcp",
                auth_token="sntrytok_secret",
                args=(),
                headers={},
                host="",
                organization_slug="",
                project_slug="",
                skills=(),
            )
        ),
    )
    mp.setattr(mod, "sentry_mcp_runtime_unavailable_reason", MagicMock(return_value=None))
    mp.setattr(mod, "describe_sentry_mcp_error", MagicMock(return_value="mocked error"))


def _sentry_mcp_list_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.sentry_mcp.tools import sentry_mcp_tool as mod

        _patch_sentry_mcp_runtime(mp)
        mp.setattr(mod, "list_sentry_mcp_tools", MagicMock(side_effect=RuntimeError("mcp")))

    def invoke() -> dict[str, Any]:
        from integrations.sentry_mcp.tools.sentry_mcp_tool import list_sentry_tools

        return list_sentry_tools()

    return ToolFailureCase(
        "sentry_mcp_list_tools",
        patch,
        invoke,
        "list_sentry_tools",
        "sentry_mcp",
    )


def _sentry_mcp_call_tool_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.sentry_mcp.tools import sentry_mcp_tool as mod

        _patch_sentry_mcp_runtime(mp)
        mp.setattr(mod, "call_sentry_mcp_tool", MagicMock(side_effect=RuntimeError("mcp")))

    def invoke() -> dict[str, Any]:
        from integrations.sentry_mcp.tools.sentry_mcp_tool import call_sentry_tool

        return call_sentry_tool(tool_name="get_issue_details", arguments={})

    return ToolFailureCase(
        "sentry_mcp_call_tool",
        patch,
        invoke,
        "call_sentry_tool",
        "sentry_mcp",
    )


def _patch_x_mcp_runtime(mp: pytest.MonkeyPatch) -> None:
    """Shared patches for X MCP cases — bypass the config/runtime guards."""
    from integrations.x_mcp.tools import x_mcp_tool as mod

    mp.setattr(
        mod,
        "x_mcp_config_from_env",
        MagicMock(
            return_value=SimpleNamespace(
                mode="streamable-http",
                command="",
                url="http://127.0.0.1:8000/mcp",
                auth_token="",
                bearer_token="",
                args=(),
                headers={},
            )
        ),
    )
    mp.setattr(mod, "x_mcp_runtime_unavailable_reason", MagicMock(return_value=None))
    mp.setattr(mod, "describe_x_mcp_error", MagicMock(return_value="mocked error"))


def _x_mcp_list_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.x_mcp.tools import x_mcp_tool as mod

        _patch_x_mcp_runtime(mp)
        mp.setattr(mod, "list_x_mcp_server_tools", MagicMock(side_effect=RuntimeError("mcp")))

    def invoke() -> dict[str, Any]:
        from integrations.x_mcp.tools.x_mcp_tool import list_x_tools

        return list_x_tools()

    return ToolFailureCase(
        "x_mcp_list_tools",
        patch,
        invoke,
        "list_x_tools",
        "x_mcp",
    )


def _x_mcp_call_tool_case() -> ToolFailureCase:
    def patch(mp: pytest.MonkeyPatch) -> None:
        from integrations.x_mcp.tools import x_mcp_tool as mod

        _patch_x_mcp_runtime(mp)
        mp.setattr(mod, "invoke_x_mcp_tool", MagicMock(side_effect=RuntimeError("mcp")))

    def invoke() -> dict[str, Any]:
        from integrations.x_mcp.tools.x_mcp_tool import call_x_tool

        return call_x_tool(tool_name="search-tweets", arguments={})

    return ToolFailureCase(
        "x_mcp_call_tool",
        patch,
        invoke,
        "call_x_tool",
        "x_mcp",
    )


_TOOL_FAILURE_CASES: list[ToolFailureCase] = [
    _azure_case(),
    _openobserve_case(),
    _snowflake_case(),
    _cloudwatch_logs_case(),
    _cloudwatch_batch_case(),
    _google_docs_case(),
    _github_repository_case(),
    _github_star_history_case(),
    _github_ci_analytics_case(),
    _eks_list_clusters_case(),
    _eks_describe_cluster_case(),
    _eks_nodegroup_case(),
    _eks_addon_case(),
    _eks_events_case(),
    _eks_node_health_case(),
    _eks_list_namespaces_case(),
    _eks_list_deployments_case(),
    _eks_list_pods_case(),
    _eks_pod_logs_case(),
    _posthog_mcp_list_case(),
    _posthog_mcp_call_tool_case(),
    _sentry_mcp_list_case(),
    _sentry_mcp_call_tool_case(),
    _x_mcp_list_case(),
    _x_mcp_call_tool_case(),
]


@pytest.mark.parametrize(
    "case",
    _TOOL_FAILURE_CASES,
    ids=[case.id for case in _TOOL_FAILURE_CASES],
)
def test_tool_reports_exactly_one_sentry_event(
    case: ToolFailureCase,
    captured_sentry_events: list[CapturedSentryEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case.patch(monkeypatch)

    result = case.invoke()

    # Tools either expose ``available=False`` or fall back to ``success=False``
    # (GoogleDocs) / raw ``{"error": ...}`` (CloudWatchLogs) — all three are
    # the "silent today" shapes #1463 enumerates. We just need the negative
    # signal to be present so an accidental success doesn't pass the assertion.
    assert isinstance(result, dict)
    assert result.get("available") is False or result.get("success") is False or "error" in result

    assert len(captured_sentry_events) == 1, (
        f"{case.id} should report exactly one Sentry event when its client raises; "
        f"got {len(captured_sentry_events)}"
    )
    event = captured_sentry_events[0]
    assert isinstance(event.exc, RuntimeError)
    assert event.extras["tag.surface"] == "tool"
    assert event.extras["tag.tool_name"] == case.expected_tool_name
    assert event.extras["tag.source"] == case.expected_source

    # Guard against a future regression where a tool migrates to the helper
    # but passes a ``tool_name=`` / ``source=`` that no longer matches its
    # declared metadata.
    from tools.registry import get_registered_tool_map

    registered = get_registered_tool_map().get(case.expected_tool_name)
    if registered is not None:
        assert registered.source == case.expected_source


def test_eks_client_error_path_uses_warning_severity(
    captured_sentry_events: list[CapturedSentryEvent],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The EKS ``except ClientError`` branch must report at WARNING, not ERROR.

    The broad ``except Exception`` branch in every EKS tool reports at the
    default severity (``error``); the dedicated ``ClientError`` branch
    intentionally degrades to ``warning`` because a missing-permission or
    not-found response is operationally useful but not a code defect. The
    parameterised cases above patch ``EKSClient`` to raise plain
    ``RuntimeError``, which exercises only the ``Exception`` branch — this
    test fills the gap by raising a real ``botocore.exceptions.ClientError``.
    """
    from botocore.exceptions import ClientError

    from integrations.eks.tools import eks_list_clusters_tool as mod

    client_error = ClientError(
        error_response={
            "Error": {"Code": "ResourceNotFoundException", "Message": "cluster missing"},
        },
        operation_name="ListClusters",
    )

    instance = MagicMock()
    instance.list_clusters.side_effect = client_error
    monkeypatch.setattr(mod, "EKSClient", MagicMock(return_value=instance))

    with caplog.at_level(logging.WARNING, logger="tools"):
        result = mod.list_eks_clusters(role_arn="arn:aws:iam::123:role/x")

    assert result["available"] is False
    assert len(captured_sentry_events) == 1
    event = captured_sentry_events[0]
    assert isinstance(event.exc, ClientError)
    assert event.extras["tag.tool_name"] == "list_eks_clusters"

    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "list_eks_clusters" in r.getMessage()
    ]
    assert warning_records, (
        "EKS ClientError branch must log at WARNING via severity='warning'; "
        f"got levels {[r.levelname for r in caplog.records]}"
    )
    error_records_for_tool = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "list_eks_clusters" in r.getMessage()
    ]
    assert error_records_for_tool == [], "ClientError severity='warning' must not also log at ERROR"


def test_eks_nodegroup_health_tags_failing_nodegroup_during_iteration(
    captured_sentry_events: list[CapturedSentryEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-loop ``describe_nodegroup`` failure must tag the actual failing nodegroup.

    The tool loops through one nodegroup at a time. When the caller does not
    pass ``nodegroup_name`` the loop runs over the discovered list, and a
    failure on the second nodegroup should reach Sentry tagged with
    ``ng-broken``, not ``None`` or the first nodegroup.
    """
    from integrations.eks.tools import eks_nodegroup_health_tool as mod

    def _describe(_cluster: str, ng: str) -> dict[str, Any]:
        if ng == "ng-broken":
            raise RuntimeError("describe_nodegroup failed")
        return {"status": "ACTIVE"}

    instance = MagicMock()
    instance.list_nodegroups.return_value = ["ng-ok", "ng-broken"]
    instance.describe_nodegroup.side_effect = _describe
    monkeypatch.setattr(mod, "EKSClient", MagicMock(return_value=instance))

    result = mod.get_eks_nodegroup_health(cluster_name="c", role_arn="arn:aws:iam::123:role/x")

    assert result["available"] is False
    assert len(captured_sentry_events) == 1
    event = captured_sentry_events[0]
    assert event.extras["tag.tool_name"] == "get_eks_nodegroup_health"
    assert event.extras["nodegroup_name"] == "ng-broken", (
        "Mid-loop failure must tag the actual failing nodegroup, not the (None) "
        f"caller input. Got extras={event.extras!r}"
    )


# ---------------------------------------------------------------------------
# Registry-wide coverage
#
# Acceptance criterion 4 of #1463: "Tool registry tests confirm telemetry
# coverage for every registered tool (or explicitly-allowlisted exclusions)."
#
# Every registered tool must fall into exactly one bucket:
#
#   ``_MIGRATED_TOOL_NAMES``
#       The tool's body deliberately catches exceptions and returns a
#       structured error dict. It calls ``report_run_error`` directly so the
#       failure reaches Sentry. These are the tools migrated by #1463.
#
#   ``_TOOLS_WITHOUT_DELIBERATE_CATCH``
#       The tool either propagates exceptions (the global wrapper added in
#       #1476 catches them at ``BaseTool.__call__`` / ``RegisteredTool.__call__``
#       and reports with ``opensre.context="tool.<name>"``) or has no failure
#       mode that needs the helper. The allowlist is explicit so a new tool
#       added with a deliberate-catch pattern fails this test until it is
#       migrated.
#
# When a new tool is registered, this test will fail; the contributor must
# either add it to ``_MIGRATED_TOOL_NAMES`` (and migrate the body) or add it
# to ``_TOOLS_WITHOUT_DELIBERATE_CATCH`` (with a brief commit-message reason).
# ---------------------------------------------------------------------------


_MIGRATED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # HTTP / cloud sites from #1463
        "query_azure_monitor_logs",
        "query_openobserve_logs",
        "query_snowflake_history",
        "get_cloudwatch_logs",
        "get_cloudwatch_batch_metrics",
        "create_google_docs_incident_report",
        "get_github_repository",
        "get_github_star_history",
        "analyze_github_ci_reliability",
        # EKS — enumerated in #1463
        "list_eks_clusters",
        "describe_eks_cluster",
        "get_eks_nodegroup_health",
        "describe_eks_addon",
        "list_eks_pods",
        "get_eks_pod_logs",
        # EKS — same deliberate-catch pattern, migrated alongside #1463
        "get_eks_events",
        "get_eks_node_health",
        "list_eks_namespaces",
        "list_eks_deployments",
        # PostHog MCP — both swallow sites in PostHogMCPTool/__init__.py.
        "list_posthog_tools",
        "call_posthog_tool",
        # Sentry MCP — both swallow sites in SentryMCPTool/__init__.py.
        "list_sentry_tools",
        "call_sentry_tool",
        # X MCP — both swallow sites in x_mcp_tool/__init__.py.
        "list_x_tools",
        "call_x_tool",
    }
)


# Tools that do NOT need the helper because they either (a) let exceptions
# escape to the global ``BaseTool.__call__`` / ``RegisteredTool.__call__``
# wrapper from #1476, or (b) have no observed swallow pattern. Keep alphabetised.
_TOOLS_WITHOUT_DELIBERATE_CATCH: frozenset[str] = frozenset(
    {
        # CloudOpsBench replay tools (CheckNodeServiceStatus, GetResources, ...)
        # were removed from this list when the bench tool module moved out of
        # tools/ into tests/benchmarks/cloudopsbench/tools/k8s/. They live
        # there as an external registry package and are only loaded when the
        # bench is actively imported, so they don't appear in the production
        # registry that this test enumerates.
        "alertmanager_alerts",
        "alertmanager_silences",
        # scan_local_git_workspace shells out to git per repository and lets
        # anything unexpected reach the global wrapper.
        "scan_local_git_workspace",
        # schedule_ci_reliability_loop writes the local task store; only a bad
        # time is caught, anything else reaches the global wrapper.
        "schedule_ci_reliability_loop",
        # architecture_* catch only WorkspaceError / ReportPersistenceError for
        # known failure states; unexpected errors escape to the #1476 wrapper.
        "architecture_cleanup_repo",
        "architecture_clone_repo",
        "architecture_save_observations",
        "ask_user_choice",
        "argocd_application_diff",
        "argocd_application_status",
        "check_s3_marker",
        "cli_exec",
        "code_implement",
        "describe_rds_events",
        "describe_rds_instance",
        "ec2_instances_by_tag",
        "execute_aws_operation",
        "execute_github_issue_mutation",
        "execute_python_code",
        "execute_yc_operation",
        "fetch_failed_run",
        "find_yc_api",
        # fix_github_pr_ci catches only GitHubCiFixError for known states;
        # unexpected errors escape to the global #1476 wrapper.
        "fix_github_pr_ci",
        # fix_github_security_alert catches only GitHubSecurityFixError for
        # known states; unexpected errors escape to the global #1476 wrapper.
        "fix_github_security_alert",
        # fix_sentry_issue catches only its own FixIssueError for known states;
        # unexpected errors escape to the global #1476 wrapper.
        "fix_sentry_issue",
        # fix_sentry_issue_start is the interactive-shell action wrapper; it
        # dispatches to fix_sentry_issue and lets unexpected errors escape.
        "fix_sentry_issue_start",
        "generate_work_status_report",
        "github_cli",
        "get_airflow_dag_runs",
        "get_airflow_metrics",
        "get_airflow_task_instances",
        "get_azure_sql_current_queries",
        "get_azure_sql_resource_stats",
        "get_azure_sql_server_status",
        "get_azure_sql_slow_queries",
        "get_azure_sql_wait_stats",
        "get_batch_statistics",
        "get_bitbucket_file_contents",
        "get_clickhouse_query_activity",
        "get_clickhouse_system_health",
        "get_dagster_run_logs",
        "get_eks_deployment_status",
        "get_elb_target_health",
        "get_error_logs",
        "get_failed_jobs",
        "get_failed_tools",
        "get_git_deploy_timeline",
        "get_github_file_contents",
        "get_github_repository_tree",
        "get_gitlab_file",
        "get_groundcover_query_reference",
        "get_host_metrics",
        "get_jenkins_build_log",
        "get_jenkins_pipeline_stages",
        "get_kafka_consumer_group_lag",
        "get_kafka_topic_health",
        "get_lambda_configuration",
        "get_lambda_errors",
        "get_lambda_invocation_logs",
        "get_mariadb_global_status",
        "get_mariadb_innodb_status",
        "get_mariadb_process_list",
        "get_mariadb_replication_status",
        "get_mariadb_slow_queries",
        "get_mongodb_atlas_alerts",
        "get_mongodb_atlas_cluster_events",
        "get_mongodb_atlas_cluster_metrics",
        "get_mongodb_atlas_clusters",
        "get_mongodb_atlas_performance_advisor",
        "get_mongodb_collection_stats",
        "get_mongodb_current_ops",
        "get_mongodb_profiler_data",
        "get_mongodb_replica_status",
        "get_mongodb_server_status",
        "get_mysql_current_processes",
        "get_mysql_replication_status",
        "get_mysql_server_status",
        "get_mysql_slow_queries",
        "get_mysql_table_stats",
        "get_pods_on_node",
        "get_postgresql_current_queries",
        "get_postgresql_lock_status",
        "get_postgresql_replication_status",
        "get_postgresql_server_status",
        "get_postgresql_slow_queries",
        "get_postgresql_table_stats",
        "get_rabbitmq_broker_overview",
        "get_rabbitmq_connection_stats",
        "get_rabbitmq_consumer_health",
        "get_rabbitmq_node_health",
        "get_rabbitmq_queue_backlog",
        "get_recent_airflow_failures",
        "get_redis_client_list",
        "get_redis_latency_doctor",
        "get_redis_list_depth",
        "get_redis_replication",
        "get_redis_server_info",
        "get_redis_slowlog",
        "get_s3_object",
        "get_sentry_issue_details",
        "get_sentry_uptime_digest",
        "get_sre_guidance",
        "get_sqs_queue_attributes",
        "get_supabase_service_health",
        "get_supabase_storage_buckets",
        "get_tracer_run",
        "get_tracer_tasks",
        "get_yc_db_cluster",
        "get_yc_instance_diagnostics",
        "get_yc_lb_health",
        "helm_get_release_manifest",
        "helm_get_release_values",
        "helm_list_releases",
        "helm_release_history",
        "helm_release_status",
        "incident_io_incidents",
        "inspect_lambda_function",
        "inspect_s3_object",
        "inspect_railway_deployment",
        "jira_add_comment",
        "jira_create_issue",
        "jira_issue_detail",
        "jira_search_issues",
        "list_bitbucket_commits",
        "list_dagster_assets",
        "list_dagster_runs",
        "list_dagster_schedule_ticks",
        "list_dagster_sensor_ticks",
        "list_github_commits",
        "list_gitlab_commits",
        "list_gitlab_mrs",
        "list_gitlab_pipelines",
        "get_github_actions_step_log",
        "list_github_actions_active_runs",
        "list_github_actions_run_jobs",
        "list_github_actions_workflow_runs",
        "list_github_security_alerts",
        "list_github_work_items",
        "list_jenkins_builds",
        "list_jenkins_jobs",
        "list_jenkins_running_builds",
        "list_s3_objects",
        "list_sentry_issue_events",
        "list_sentry_uptime_alerts",
        "list_yc_db_clusters",
        "list_yc_log_groups",
        "list_yc_instances",
        "list_yc_metrics",
        "llm_set_provider",
        "lookup_cloudtrail_events",
        # Long-term memory tools: local-file CRUD over core/domain/memory;
        # expected failures return structured error dicts without catching,
        # unexpected exceptions escape to the global wrapper. The domain
        # store's OSError handling mirrors the misses store (stderr notice).
        "memory_forget",
        "memory_recall",
        "memory_remember",
        "opsgenie_alert_detail",
        "opsgenie_alerts",
        "pagerduty_incident_detail",
        "pagerduty_incidents",
        "pagerduty_oncall",
        "pagerduty_services",
        "pi_coding_task",
        "prefect_flow_runs",
        "prefect_worker_health",
        "propose_github_issue_mutation_from_slack",
        "query_betterstack_logs",
        "query_coralogix_logs",
        "query_datadog_all",
        "query_datadog_events",
        "query_datadog_logs",
        "query_datadog_metrics",
        "query_datadog_monitors",
        "query_groundcover_logs",
        "query_groundcover_traces",
        "query_elasticsearch_logs",
        "query_grafana_alert_rules",
        "query_grafana_annotations",
        "query_grafana_logs",
        "query_grafana_metrics",
        "query_grafana_service_names",
        "query_grafana_traces",
        "query_honeycomb_traces",
        "query_new_relic_alerts",
        "query_new_relic_metrics",
        "query_opensearch_analytics",
        "query_signoz_logs",
        "query_signoz_metrics",
        "query_signoz_traces",
        "query_splunk_logs",
        "query_tempo",
        "read_yc_db_logs",
        "read_yc_logs",
        "query_yc_metrics",
        "redeploy_railway_service",
        "replay_slack_thread_locally",
        "scan_redis_keys",
        "search_bitbucket_code",
        "search_github_code",
        "search_github_issues",
        "search_sentry_issues",
        "shell_run",
        "skill_view",
        "session_goal_set",
        "propose_scheduled_delivery",
        "slack_add_reaction",
        "slack_join_channel",
        "slack_list_team_members",
        "slack_read_list",
        "slack_read_messages",
        "slack_reply_message",
        "slack_search_messages",
        "slack_send_message",
        "slash_invoke",
        "summarize_community_followups",
        "summarize_github_pr_status",
        "task_cancel",
        # update_plan returns structured invalid-plan dicts; unexpected
        # exceptions escape to the global wrapper.
        "update_plan",
        "work_task_add",
        "work_task_complete",
        "work_task_list",
        "work_task_prioritize",
        "work_task_schedule_checkin",
        "work_task_update",
        # Temporal tools use try/finally only (to close the client); the client
        # returns structured error dicts for handled HTTP failures, and any
        # unexpected exception escapes to the #1476 global wrapper.
        "temporal_namespace_info",
        "temporal_task_queue",
        "temporal_workflow_history",
        "temporal_workflows",
        "telegram_send_message",
        "rocketchat_send_message",
        "buzz_send_message",
        "twilio_notify",
        "vercel_deployment_logs",
        "vercel_deployment_status",
        "victoria_logs_query",
        # Kubernetes tools: client methods catch exceptions internally via
        # capture_service_error and return structured error dicts; any unexpected
        # exception from run() escapes to the #1476 global wrapper.
        "kubernetes_describe_pod",
        "kubernetes_get_events",
        "kubernetes_get_pod_logs",
        "kubernetes_get_resource",
        "kubernetes_list_configmaps",
        "kubernetes_list_daemonsets",
        "kubernetes_list_deployments",
        "kubernetes_list_ingresses",
        "kubernetes_list_nodes",
        "kubernetes_list_pods",
        "kubernetes_list_services",
        "kubernetes_list_statefulsets",
    }
)


def test_every_registered_tool_is_migrated_or_allowlisted() -> None:
    """Acceptance criterion 4: every registered tool is accounted for.

    A new tool must be classified up front — either it deliberately catches
    its own exceptions (migrate it; add to ``_MIGRATED_TOOL_NAMES``) or it
    lets them escape and relies on #1476's global wrapper (allowlist it in
    ``_TOOLS_WITHOUT_DELIBERATE_CATCH``).
    """
    from tools.registry import INTEGRATION_TOOL_PACKAGES, get_registered_tool_map

    # Limit the audit to PRODUCTION tools — those defined in ``tools.*`` or in
    # the exact per-vendor packages the registry walks via
    # ``INTEGRATION_TOOL_PACKAGES``. External packages registered via
    # ``register_external_tool_package`` (e.g. bench-only tools that live under
    # ``tests/benchmarks/``) have their own classification expectations and
    # aren't part of this production-telemetry contract. Pinning the prefix
    # to the registry's own integration list (instead of a broad
    # ``"integrations."``) keeps the audit from sweeping in any future
    # caller that ships tools under an ``integrations.*`` namespace.
    _PRODUCTION_TOOL_PREFIXES = ("tools.", *INTEGRATION_TOOL_PACKAGES)
    registered = {
        name
        for name, tool in get_registered_tool_map().items()
        if tool.origin_module.startswith(_PRODUCTION_TOOL_PREFIXES)
    }
    classified = _MIGRATED_TOOL_NAMES | _TOOLS_WITHOUT_DELIBERATE_CATCH

    unclassified = registered - classified
    assert unclassified == set(), (
        "New tools must be classified for Sentry coverage in test_telemetry.py: "
        "either add them to _MIGRATED_TOOL_NAMES (and call report_run_error in "
        "their except block) or to _TOOLS_WITHOUT_DELIBERATE_CATCH (if they "
        f"let exceptions escape to the #1476 global wrapper). Unclassified: {sorted(unclassified)}"
    )

    stale = classified - registered
    assert stale == set(), (
        "These names appear in _MIGRATED_TOOL_NAMES or _TOOLS_WITHOUT_DELIBERATE_CATCH "
        f"but are no longer registered tools: {sorted(stale)}"
    )

    overlap = _MIGRATED_TOOL_NAMES & _TOOLS_WITHOUT_DELIBERATE_CATCH
    assert overlap == set(), (
        f"A tool cannot be both migrated and allowlisted; pick one: {sorted(overlap)}"
    )


def test_every_migrated_tool_has_a_parameterised_failure_case() -> None:
    """Each migrated tool must have a regression test in ``_TOOL_FAILURE_CASES``."""
    covered_by_parametrised = {case.expected_tool_name for case in _TOOL_FAILURE_CASES}
    missing = _MIGRATED_TOOL_NAMES - covered_by_parametrised
    assert missing == set(), (
        "Every name in _MIGRATED_TOOL_NAMES must have a parameterised "
        "failure case in _TOOL_FAILURE_CASES (unless it shares a code path "
        f"already covered by another case). Missing: {sorted(missing)}"
    )
