"""S3 marker and related S3 utilities."""

from __future__ import annotations

from app.services.s3_client import (
    compare_versions,
    get_s3_client,
    head_object,
    list_object_versions,
)
from app.tools.tool_decorator import tool


def _check_s3_marker_available(sources: dict[str, dict]) -> bool:
    return bool(
        (sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("prefix"))
        or sources.get("s3_processed", {}).get("bucket")
    )


def _extract_check_s3_marker_params(sources: dict[str, dict]) -> dict:
    if sources.get("s3_processed"):
        return {
            "bucket": sources["s3_processed"].get("bucket"),
            "prefix": sources["s3_processed"].get("prefix", ""),
        }
    return {
        "bucket": sources.get("s3", {}).get("bucket"),
        "prefix": sources.get("s3", {}).get("prefix"),
    }


@tool(
    name="check_s3_marker",
    source="storage",
    description="Check if a _SUCCESS marker exists in S3 storage to verify pipeline completion.",
    use_cases=[
        "Verifying if a data pipeline run completed successfully",
        "Checking for presence of a _SUCCESS marker file",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "prefix": {"type": "string"},
        },
        "required": ["bucket", "prefix"],
    },
    is_available=_check_s3_marker_available,
    extract_params=_extract_check_s3_marker_params,
)
def check_s3_marker(bucket: str, prefix: str) -> dict:
    """Check if a _SUCCESS marker exists in S3 storage."""
    client = get_s3_client()
    result = client.check_marker(bucket, prefix)
    return {
        "marker_exists": result.marker_exists,
        "file_count": result.file_count,
        "files": result.files,
    }


def list_s3_versions(bucket: str, key: str, max_versions: int = 10) -> dict:
    """List version history for an S3 object."""
    if not bucket or not key:
        return {"error": "bucket and key are required"}
    result = list_object_versions(bucket, key, max_versions)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "bucket": bucket, "key": key}
    data = result.get("data", {})
    return {
        "found": bool(data.get("versions")),
        "bucket": bucket,
        "key": key,
        "version_count": data.get("version_count", 0),
        "versions": data.get("versions", []),
        "delete_markers": data.get("delete_markers", []),
    }


def compare_s3_versions(bucket: str, key: str, version_id_1: str, version_id_2: str) -> dict:
    """Compare two versions of an S3 object to identify changes."""
    if not bucket or not key:
        return {"error": "bucket and key are required"}
    if not version_id_1 or not version_id_2:
        return {"error": "Both version_id_1 and version_id_2 are required"}
    result = compare_versions(bucket, key, version_id_1, version_id_2)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "bucket": bucket, "key": key}
    data = result.get("data", {})
    return {
        "bucket": bucket,
        "key": key,
        "version_1": data.get("version_1"),
        "version_2": data.get("version_2"),
        "are_identical": data.get("are_identical", False),
        "size_diff": data.get("size_diff", 0),
        "is_text": data.get("is_text", False),
    }


def check_s3_object_exists(bucket: str, key: str) -> dict:
    """Check if an S3 object exists."""
    if not bucket or not key:
        return {"error": "bucket and key are required"}
    result = head_object(bucket, key)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "bucket": bucket, "key": key}
    return {
        "exists": result.get("exists", False),
        "bucket": bucket,
        "key": key,
        "size": result.get("data", {}).get("size") if result.get("exists") else None,
    }
