"""SQS queue attributes tool — backlog depth, stuck consumers, and DLQ wiring.

Wraps the read-only ``list_queues`` + ``get_queue_attributes`` APIs so the
planner can distinguish a normal backlog (visible high, in-flight low) from
stuck consumers (visible near zero, in-flight pinned at the consumer count,
no DLQ) — a shape that leaves no trace in logs, because a consumer hanging
past its visibility timeout never raises.

``list_queues`` returns URLs only, so attributes are fetched per queue; the
``max_queues`` cap bounds that fan-out.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from core.domain.types.evidence import CATALOG_ENTRIES_KEY, record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.sqs import (
    DEFAULT_SQS_MAX_QUEUES,
    DEFAULT_SQS_REGION,
    MAX_SQS_MAX_QUEUES,
    coerce_sqs_max_queues,
    sqs_extract_params,
    sqs_is_available,
)

logger = logging.getLogger(__name__)

_BACKLOG_ALERT_USE_CASE = (
    "Investigating a queue-age or backlog alert — returns depth, in-flight "
    "count, and DLQ configuration"
)


def _queue_name_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _parse_attributes(raw_attrs: dict[str, str]) -> dict[str, Any]:
    """Normalize SQS's all-strings attribute map into typed fields.

    A missing or unparseable numeric becomes ``None``, not ``0`` — an absent
    measurement and a genuinely empty queue are different findings.
    """

    def _int(key: str) -> int | None:
        val = raw_attrs.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    # Keep the raw text when RedrivePolicy does not parse, so a malformed policy
    # still reads as "a DLQ is configured" rather than as no DLQ at all.
    redrive_raw = raw_attrs.get("RedrivePolicy")
    redrive_policy: dict[str, Any] | None = None
    if redrive_raw:
        try:
            parsed = json.loads(redrive_raw)
        except (ValueError, TypeError):
            parsed = None
        redrive_policy = parsed if isinstance(parsed, dict) else {"raw": redrive_raw}

    return {
        "visible_count": _int("ApproximateNumberOfMessages"),
        "in_flight_count": _int("ApproximateNumberOfMessagesNotVisible"),
        "visibility_timeout_seconds": _int("VisibilityTimeout"),
        "has_dlq": redrive_policy is not None,
        "redrive_policy": redrive_policy,
        "is_fifo": str(raw_attrs.get("FifoQueue", "")).strip().lower() == "true",
    }


def _sqs_summary(queues: list[dict[str, Any]]) -> str:
    measured = [
        q
        for q in queues
        if "attributes_error" not in q
        and isinstance(q.get("visible_count"), int)
        and isinstance(q.get("in_flight_count"), int)
    ]
    parts = [f"{len(queues)} queues"]
    if measured:
        visible = sum(q["visible_count"] for q in measured)
        in_flight = sum(q["in_flight_count"] for q in measured)
        with_dlq = sum(1 for q in measured if q.get("has_dlq"))
        parts.append(f"{visible} visible, {in_flight} in-flight, {with_dlq} with DLQ")
        if len(measured) < len(queues):
            parts[-1] += f" across {len(measured)} measured"
    unreadable = len(queues) - len(measured)
    if unreadable:
        parts.append(f"{unreadable} unreadable")
    return ", ".join(parts)


def _map_get_sqs_queue_attributes(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    queues = output.get("queues") or []
    if not isinstance(queues, list):
        return
    existing = evidence.get("sqs_queues")
    merged: dict[str, dict[str, Any]] = {}
    for q in [*(existing if isinstance(existing, list) else []), *queues]:
        if isinstance(q, dict):
            merged[str(q.get("url") or q.get("name") or id(q))] = q
    if not merged:
        return
    evidence["sqs_queues"] = list(merged.values())
    summary = _sqs_summary(list(merged.values()))
    entries = evidence.get(CATALOG_ENTRIES_KEY)
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("source") == "get_sqs_queue_attributes":
                entry["summary"] = summary
                return
    record_evidence_entry(
        evidence,
        source="get_sqs_queue_attributes",
        label="SQS Queues",
        summary=summary,
    )


@tool(
    name="get_sqs_queue_attributes",
    display_name="SQS queues",
    source="sqs",
    description=(
        "List AWS SQS queues by name prefix and return per-queue attributes — "
        "visible message depth, in-flight count, visibility timeout, "
        "dead-letter queue wiring, and FIFO flag. Use this to tell a normal "
        "backlog (visible high, in-flight low) apart from stuck consumers "
        "(visible near zero, in-flight pinned at the consumer count, no DLQ)."
    ),
    use_cases=[
        "Diagnosing stuck consumers: in-flight count equals the consumer/pod count",
        "Identifying a poison-pill message cycling due to a short VisibilityTimeout",
        "Checking whether a queue has a dead-letter queue configured at all",
        _BACKLOG_ALERT_USE_CASE,
        "Confirming whether a queue is draining after a consumer deploy or scale-up",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "queue_name_prefix": {
                "type": "string",
                "default": "",
                "description": (
                    "Only inspect queues whose name starts with this prefix "
                    "(e.g. 'payments-'). Omit to inspect every queue."
                ),
            },
            "max_queues": {
                "type": "integer",
                "default": DEFAULT_SQS_MAX_QUEUES,
                "minimum": 1,
                "maximum": MAX_SQS_MAX_QUEUES,
                "description": "Maximum number of queues to inspect.",
            },
            "region": {"type": "string", "default": DEFAULT_SQS_REGION},
        },
        "required": [],
    },
    injected_params=("aws_backend",),
    is_available=sqs_is_available,
    extract_params=sqs_extract_params,
    surfaces=(ToolSurface.CHAT,),
    evidence_mapper=_map_get_sqs_queue_attributes,
)
def get_sqs_queue_attributes(
    queue_name_prefix: str = "",
    max_queues: int = DEFAULT_SQS_MAX_QUEUES,
    region: str = DEFAULT_SQS_REGION,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return per-queue SQS attributes for queues matching the given prefix.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests) the
    call short-circuits to the backend so scenario runs never leak boto3 calls
    to real AWS.
    """
    max_queues = coerce_sqs_max_queues(max_queues)

    logger.info(
        "[sqs] get_sqs_queue_attributes prefix=%r max_queues=%s region=%s",
        queue_name_prefix,
        max_queues,
        region,
    )

    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.get_sqs_queue_attributes(
                queue_name_prefix=queue_name_prefix,
                max_queues=max_queues,
                region=region,
            ),
        )

    list_params: dict[str, Any] = {"MaxResults": max_queues}
    if queue_name_prefix:
        list_params["QueueNamePrefix"] = queue_name_prefix

    list_result = execute_aws_sdk_call(
        service_name="sqs",
        operation_name="list_queues",
        parameters=list_params,
        region=region,
    )

    if not list_result.get("success"):
        logger.error(
            "[sqs] list_queues failed prefix=%r region=%s: %s",
            queue_name_prefix,
            region,
            list_result.get("error"),
        )
        return tool_unavailable(
            "sqs",
            "Failed to list SQS queues. Check server logs for details.",
            queues=[],
        )

    list_data = list_result.get("data") or {}
    # ListQueues already honors MaxResults, so more results exist iff AWS
    # returned a NextToken — not iff the URL count exceeds max_queues (that
    # can't happen, since we asked for at most max_queues in the first place).
    truncated = bool(list_data.get("NextToken"))
    # The transport sanitizer replaces the tail of an oversized list with a
    # "... (N more items truncated)" marker string, which is not a queue URL.
    raw_urls = list_data.get("QueueUrls") or []
    queue_urls = [url for url in raw_urls if isinstance(url, str) and url.startswith("http")]
    queue_urls = queue_urls[:max_queues]

    queues: list[dict[str, Any]] = []
    for url in queue_urls:
        name = _queue_name_from_url(url)
        attr_result = execute_aws_sdk_call(
            service_name="sqs",
            operation_name="get_queue_attributes",
            parameters={"QueueUrl": url, "AttributeNames": ["All"]},
            region=region,
        )

        if not attr_result.get("success"):
            # A per-queue policy can deny GetQueueAttributes on one queue; record
            # the gap and keep inspecting the rest.
            logger.warning(
                "[sqs] get_queue_attributes failed queue=%s region=%s: %s",
                name,
                region,
                attr_result.get("error"),
            )
            queues.append(
                {
                    "name": name,
                    "url": url,
                    "attributes_error": (
                        "Failed to retrieve attributes. Check server logs for details."
                    ),
                }
            )
            continue

        raw_attrs = (attr_result.get("data") or {}).get("Attributes") or {}
        queues.append({"name": name, "url": url, **_parse_attributes(raw_attrs)})

    # No "error" key on success: the runtime tool loop flags failure on a truthy
    # "error" (core.tool.execution._normalize_result).
    return {
        "source": "sqs",
        "available": True,
        "region": region,
        "queue_name_prefix": queue_name_prefix,
        "total_queues": len(queues),
        "truncated": truncated,
        "queues": queues,
    }
