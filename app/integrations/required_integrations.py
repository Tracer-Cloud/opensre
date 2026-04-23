"""Shared validation helpers for required vendor integrations."""

from __future__ import annotations

from typing import Any

from app.integrations.catalog import get_service_family, normalize_service_name


class MissingIntegrationError(RuntimeError):
    """Raised when an investigation requires an integration that is not configured."""


def required_integration_for_source(raw_alert: dict[str, Any] | str | None) -> str | None:
    """Return the required integration family for the raw alert, if any."""
    if not isinstance(raw_alert, dict):
        return None

    alert_source = str(raw_alert.get("alert_source", "") or "").strip().lower()
    if alert_source:
        return get_service_family(normalize_service_name(alert_source))

    if _has_grafana_hint(raw_alert):
        return "grafana"
    if _has_honeycomb_hint(raw_alert):
        return "honeycomb"
    if _has_coralogix_hint(raw_alert):
        return "coralogix"
    if _has_aws_hint(raw_alert):
        return "aws"

    return None


def validate_required_integrations(
    raw_alert: dict[str, Any] | str | None,
    resolved_integrations: dict[str, Any] | None,
) -> None:
    """Validate that required integrations are present in resolved integrations."""
    required = required_integration_for_source(raw_alert)
    if required is None:
        return

    if not isinstance(resolved_integrations, dict):
        raise MissingIntegrationError(
            f"This alert appears to require the {required} integration, but no integrations were loaded."
        )

    required_family = get_service_family(normalize_service_name(required))
    if _has_service_family(resolved_integrations, required_family):
        return

    raise MissingIntegrationError(
        f"This alert appears to require the {required_family} integration, but no active {required_family} integration was found."
    )


def _has_service_family(resolved_integrations: dict[str, Any], required_family: str) -> bool:
    for key in resolved_integrations:
        if not isinstance(key, str):
            continue
        if key.startswith("_all_"):
            continue
        if get_service_family(normalize_service_name(key)) == required_family:
            return True
    return False


def _has_grafana_hint(raw_alert: dict[str, Any]) -> bool:
    return _any_url_contains(raw_alert, "grafana")


def _has_honeycomb_hint(raw_alert: dict[str, Any]) -> bool:
    return _any_url_contains(raw_alert, "honeycomb")


def _has_coralogix_hint(raw_alert: dict[str, Any]) -> bool:
    return _any_url_contains(raw_alert, "coralogix")


def _has_aws_hint(raw_alert: dict[str, Any]) -> bool:
    aws_keys = {
        "cloudwatch_log_group",
        "cloudwatchLogGroup",
        "log_group",
        "lambda_function",
        "lambda_arn",
    }
    return any(bool(raw_alert.get(key)) for key in aws_keys)


def _any_url_contains(raw_alert: dict[str, Any], substring: str) -> bool:
    checked = []
    if raw_alert.get("externalURL"):
        checked.append(str(raw_alert["externalURL"]))
    if raw_alert.get("generatorURL"):
        checked.append(str(raw_alert["generatorURL"]))
    alerts = raw_alert.get("alerts")
    if isinstance(alerts, list) and alerts:
        first = alerts[0]
        if isinstance(first, dict) and first.get("generatorURL"):
            checked.append(str(first["generatorURL"]))
    return any(substring in candidate.lower() for candidate in checked if candidate)
