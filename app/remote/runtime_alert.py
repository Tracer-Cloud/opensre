"""Build a raw_alert payload for a deployed service runtime investigation.

Gathers service status, recent logs, and live health from the configured
remote ops provider, then packages them into a ``raw_alert`` dict that the
existing investigation pipeline can consume.
"""

from __future__ import annotations

from typing import Any

from app.cli.errors import OpenSREError
from app.cli.wizard.store import load_named_remotes, load_remote_ops_config
from app.deployment.health import poll_deployment_health
from app.remote.ops import (
    RemoteOpsError,
    RemoteServiceScope,
    ServiceStatus,
    resolve_remote_ops_provider,
)
from app.remote.slack_context import fetch_slack_thread, parse_slack_thread_ref

_DEFAULT_LOG_LINES = 100
_HEALTH_MAX_ATTEMPTS = 2
_HEALTH_INTERVAL_SECONDS = 2.0


def _service_dict(status: ServiceStatus) -> dict[str, Any]:
    return {
        "provider": status.provider,
        "project": status.project,
        "service": status.service,
        "url": status.url,
        "deployment_id": status.deployment_id,
        "deployment_status": status.deployment_status,
        "environment": status.environment,
        "health": status.health,
        "metadata": dict(status.metadata),
    }


def _probe_health(url: str | None) -> dict[str, Any]:
    if not url:
        return {"error": "no service URL available"}
    try:
        result = poll_deployment_health(
            url,
            max_attempts=_HEALTH_MAX_ATTEMPTS,
            interval_seconds=_HEALTH_INTERVAL_SECONDS,
        )
    except TimeoutError as exc:
        return {"error": str(exc)}
    return {
        "url": result.url,
        "status_code": result.status_code,
        "attempts": result.attempts,
        "elapsed_seconds": result.elapsed_seconds,
    }


def build_runtime_alert_payload(
    service_name: str,
    *,
    slack_thread_ref: str | None = None,
    slack_bot_token: str | None = None,
) -> dict[str, Any]:
    """Return a ``raw_alert`` dict for a deployed service runtime investigation.

    Resolves the service from the named-remote registry and stored ops config,
    fetches deployment status + recent logs + health probe (best-effort), and
    returns a dict suitable for ``run_investigation_cli(raw_alert=...)``.

    If ``slack_thread_ref`` is provided (format ``CHANNEL/TS``), the thread's
    messages are also pulled via Slack's ``conversations.replies`` API using
    ``slack_bot_token`` and included under the ``slack_thread`` key. Failures
    are captured in that key rather than raised, so the investigation can
    still proceed without Slack context.
    """
    name = (service_name or "").strip()
    if not name:
        raise OpenSREError(
            "Service name is required.",
            suggestion="Pass --service <name> with a configured remote name.",
        )

    named = load_named_remotes()
    if name not in named:
        available = ", ".join(sorted(named)) or "(none configured)"
        raise OpenSREError(
            f"No remote named '{name}' is configured.",
            suggestion=(
                f"Configured remotes: {available}. "
                f"Deploy with 'opensre deploy' or add one with 'opensre remote'."
            ),
        )

    stored = load_remote_ops_config()
    provider_name = (stored.get("provider") or "railway").strip().lower()
    project = stored.get("project")
    service = stored.get("service") or name

    try:
        provider = resolve_remote_ops_provider(provider_name)
    except RemoteOpsError as exc:
        raise OpenSREError(
            f"Unsupported remote ops provider '{provider_name}': {exc}",
            suggestion="Run 'opensre remote ops status' to reconfigure the provider.",
        ) from exc
    scope = RemoteServiceScope(provider=provider_name, project=project, service=service)

    try:
        status = provider.status(scope)
    except RemoteOpsError as exc:
        raise OpenSREError(
            f"Failed to fetch deployment status for '{name}': {exc}",
            suggestion=(
                "Verify the remote ops provider is configured correctly "
                "('opensre remote ops status' to check)."
            ),
        ) from exc

    try:
        recent_logs = provider.fetch_logs(scope, lines=_DEFAULT_LOG_LINES)
    except RemoteOpsError as exc:
        recent_logs = f"(logs unavailable: {exc})"

    health_probe = _probe_health(status.url)

    slack_thread: dict[str, Any] | None = None
    if slack_thread_ref:
        try:
            channel, ts = parse_slack_thread_ref(slack_thread_ref)
        except ValueError as exc:
            slack_thread = {"error": str(exc)}
        else:
            slack_thread = fetch_slack_thread(channel, ts, slack_bot_token or "")

    payload: dict[str, Any] = {
        "alert_name": f"Remote runtime investigation: {name}",
        "pipeline_name": service or name,
        "severity": "warning",
        # Note: alert_source is intentionally left empty. detect_sources.py
        # filters integrations by alert_source (e.g. suppresses Grafana when
        # alert_source not in ("grafana", "")). Leaving it empty keeps all
        # integrations available; the LLM in extract_alert may re-infer an
        # alert_source from log text, which is expected and correct.
        "investigation_origin": "remote_runtime",
        "message": (
            f"Manual runtime investigation for deployed service '{name}'. "
            f"Health={status.health}, deployment_status={status.deployment_status}."
        ),
        "service": _service_dict(status),
        "recent_logs": recent_logs,
        "health_probe": health_probe,
    }
    if slack_thread is not None:
        payload["slack_thread"] = slack_thread
    return payload
