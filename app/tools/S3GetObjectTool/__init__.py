"""Get full S3 object content."""

from __future__ import annotations

from app.services.s3_client import get_full_object
from app.tools.tool_decorator import tool


def _get_s3_object_available(sources: dict[str, dict]) -> bool:
    return bool(
        (sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key"))
        or (sources.get("s3_audit", {}).get("bucket") and sources.get("s3_audit", {}).get("key"))
    )


def _extract_get_s3_object_params(sources: dict[str, dict]) -> dict:
    if sources.get("s3_audit"):
        return {
            "bucket": sources["s3_audit"].get("bucket"),
            "key": sources["s3_audit"].get("key"),
        }
    return {
        "bucket": sources.get("s3", {}).get("bucket"),
        "key": sources.get("s3", {}).get("key"),
    }


@tool(
    name="get_s3_object",
    display_name="S3 audit",
    source="storage",
    description="Get full S3 object content — audit payloads, configs, lineage data.",
    use_cases=[
        "Retrieving audit payloads when audit_key found in S3 metadata",
        "Tracing external vendor interactions that caused failures",
        "Reading configuration or manifest files",
        "Finding upstream data lineage details",
    ],
    requires=["bucket", "key"],
    input_schema={
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "key": {"type": "string"},
        },
        "required": ["bucket", "key"],
    },
    is_available=_get_s3_object_available,
    extract_params=_extract_get_s3_object_params,
)
def get_s3_object(bucket: str, key: str) -> dict:
    """Get full S3 object content (audit payloads, configs, lineage data)."""
    if not bucket or not key:
        return {"error": "bucket and key are required"}

    result = get_full_object(bucket, key, max_size=1048576)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "bucket": bucket, "key": key}
    if not result.get("exists", True):
        return {"found": False, "bucket": bucket, "key": key, "message": "Object does not exist"}

    data = result.get("data", {})
    return {
        "found": True,
        "bucket": bucket,
        "key": key,
        "size": data.get("size"),
        "content_type": data.get("content_type"),
        "is_text": data.get("is_text", False),
        "content": data.get("content"),
        "metadata": data.get("metadata", {}),
    }
