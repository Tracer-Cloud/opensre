"""List objects in an S3 bucket."""

from __future__ import annotations

from app.services.s3_client import list_objects
from app.tools.tool_decorator import tool


def _list_s3_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("s3", {}).get("bucket"))


def _extract_list_s3_params(sources: dict[str, dict]) -> dict:
    return {
        "bucket": sources.get("s3", {}).get("bucket"),
        "prefix": sources.get("s3", {}).get("prefix", ""),
        "max_keys": 100,
    }


@tool(
    name="list_s3_objects",
    source="storage",
    description="List objects in an S3 bucket with optional prefix filter.",
    use_cases=[
        "Exploring S3 bucket contents and finding relevant data files",
        "Verifying which files are present in a pipeline output location",
    ],
    requires=["bucket"],
    input_schema={
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "prefix": {"type": "string", "default": ""},
            "max_keys": {"type": "integer", "default": 100},
        },
        "required": ["bucket"],
    },
    is_available=_list_s3_available,
    extract_params=_extract_list_s3_params,
)
def list_s3_objects(bucket: str, prefix: str = "", max_keys: int = 100) -> dict:
    """List objects in an S3 bucket with optional prefix filter."""
    if not bucket:
        return {"error": "bucket is required"}

    result = list_objects(bucket, prefix, max_keys)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "bucket": bucket, "prefix": prefix}

    data = result.get("data", {})
    return {
        "found": bool(data.get("objects")),
        "bucket": bucket,
        "prefix": prefix,
        "count": data.get("count", 0),
        "objects": data.get("objects", []),
        "is_truncated": data.get("is_truncated", False),
    }
