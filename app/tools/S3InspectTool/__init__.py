"""Inspect S3 object metadata and sample content."""

from __future__ import annotations

from app.services.s3_client import get_object_metadata, get_object_sample
from app.tools.tool_decorator import tool


def _inspect_s3_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key"))


def _extract_inspect_s3_params(sources: dict[str, dict]) -> dict:
    return {
        "bucket": sources.get("s3", {}).get("bucket"),
        "key": sources.get("s3", {}).get("key"),
    }


@tool(
    name="inspect_s3_object",
    source="storage",
    description="Inspect an S3 object's metadata and sample content.",
    use_cases=[
        "Tracing data lineage upstream to find root cause",
        "Identifying schema changes in input data",
        "Finding audit trails for external vendor interactions",
        "Discovering which Lambda function produced the data",
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
    is_available=_inspect_s3_available,
    extract_params=_extract_inspect_s3_params,
)
def inspect_s3_object(bucket: str, key: str) -> dict:
    """Inspect an S3 object's metadata and sample content."""
    if not bucket or not key:
        return {"error": "bucket and key are required"}

    metadata_result = get_object_metadata(bucket, key)
    if not metadata_result.get("success"):
        return {
            "error": metadata_result.get("error", "Unknown error"),
            "bucket": bucket,
            "key": key,
        }
    if not metadata_result.get("exists"):
        return {"found": False, "bucket": bucket, "key": key, "message": "Object does not exist"}

    sample_result = get_object_sample(bucket, key, max_bytes=4096)
    metadata = metadata_result.get("data", {})
    sample_data = sample_result.get("data", {}) if sample_result.get("success") else {}

    return {
        "found": True,
        "bucket": bucket,
        "key": key,
        "size": metadata.get("size"),
        "last_modified": str(metadata.get("last_modified")),
        "content_type": metadata.get("content_type"),
        "etag": metadata.get("etag"),
        "version_id": metadata.get("version_id"),
        "metadata": metadata.get("metadata", {}),
        "is_text": sample_data.get("is_text", False),
        "sample": sample_data.get("sample"),
        "sample_bytes": sample_data.get("sample_bytes"),
    }
