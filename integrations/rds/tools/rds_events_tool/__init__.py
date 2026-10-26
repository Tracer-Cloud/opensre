"""RDS events tool — recent failover, maintenance, and configuration events."""

from __future__ import annotations

import logging
from typing import Any, cast

from core.domain.types.evidence import record_evidence_entry
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.rds import (
    DEFAULT_RDS_REGION,
    rds_extract_params,
    rds_is_available,
)

logger = logging.getLogger(__name__)

DEFAULT_DURATION_MINUTES = 60


def _map_describe_rds_events(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite recent RDS events (failovers, maintenance, backups) as report evidence.

    ``execute_aws_sdk_call`` makes one unpaginated ``describe_events`` call, so
    ``events`` can be a partial page on a busy instance with a long lookback
    window. The summary says how many were *reported*, not how many occurred,
    so it never implies a completeness the underlying call doesn't guarantee.
    """
    if not output.get("available"):
        return
    events = output.get("events") or []
    duration_minutes = output.get("duration_minutes")
    if events:
        window = f" in the last {duration_minutes} min" if duration_minutes else ""
        record_evidence_entry(
            evidence,
            source="describe_rds_events",
            label="RDS Events",
            summary=f"{len(events)} event(s) reported{window}",
        )


@tool(
    name="describe_rds_events",
    source="rds",
    description=(
        "Describe recent AWS RDS events for a DB instance — failovers, "
        "maintenance windows, parameter changes, and backup events."
    ),
    use_cases=[
        "Investigating Multi-AZ failover events",
        "Checking recent maintenance or parameter group changes",
        "Tracing backup or recovery events around an incident",
    ],
    requires=["db_instance_identifier"],
    input_schema={
        "type": "object",
        "properties": {
            "db_instance_identifier": {"type": "string"},
            "region": {"type": "string", "default": DEFAULT_RDS_REGION},
            "duration_minutes": {
                "type": "integer",
                "default": DEFAULT_DURATION_MINUTES,
                "minimum": 1,
                "maximum": 20160,
            },
        },
        "required": ["db_instance_identifier"],
    },
    is_available=rds_is_available,
    extract_params=rds_extract_params,
    evidence_mapper=_map_describe_rds_events,
)
def describe_rds_events(
    db_instance_identifier: str,
    region: str = DEFAULT_RDS_REGION,
    duration_minutes: int = DEFAULT_DURATION_MINUTES,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe recent RDS events for a DB instance.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests)
    the call short-circuits to the backend so we never leak boto3 calls to
    real AWS during scenario runs. Otherwise calls boto3 rds via
    ``execute_aws_sdk_call`` using the default boto3 credential chain.
    """
    logger.info(
        "[rds] describe_rds_events db=%s region=%s duration=%s",
        db_instance_identifier,
        region,
        duration_minutes,
    )

    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.describe_db_events(
                db_instance_identifier=db_instance_identifier,
                duration_minutes=duration_minutes,
                region=region,
            ),
        )

    result = execute_aws_sdk_call(
        service_name="rds",
        operation_name="describe_events",
        parameters={
            "SourceIdentifier": db_instance_identifier,
            "SourceType": "db-instance",
            "Duration": duration_minutes,
        },
        region=region,
    )

    if not result.get("success"):
        logger.error(
            "[rds] describe_events failed for db=%s region=%s: %s",
            db_instance_identifier,
            region,
            result.get("error"),
        )
        return tool_unavailable(
            "rds",
            "Failed to describe RDS events. Check server logs for details.",
            db_instance_identifier=db_instance_identifier,
        )

    raw_events = (result.get("data") or {}).get("Events") or []
    events = [
        {
            "date": event.get("Date"),
            "message": event.get("Message"),
            "categories": event.get("EventCategories", []),
            "source_type": event.get("SourceType"),
        }
        for event in raw_events
    ]

    return {
        "source": "rds",
        "available": True,
        "db_instance_identifier": db_instance_identifier,
        "duration_minutes": duration_minutes,
        "total_events": len(events),
        "events": events,
        "error": None,
    }
