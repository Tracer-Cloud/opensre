"""List objects in an S3 bucket."""

from __future__ import annotations

from app.integrations.clients.s3_client import list_objects
from app.tools.base import BaseTool


class S3ListTool(BaseTool):
    """List objects in an S3 bucket with optional prefix filter."""

    name = "list_s3_objects"
    source = "storage"
    description = "List objects in an S3 bucket with optional prefix filter."
    use_cases = [
        "Exploring S3 bucket contents and finding relevant data files",
        "Verifying which files are present in a pipeline output location",
    ]
    requires = ["bucket"]
    input_schema = {
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "prefix": {"type": "string", "default": ""},
            "max_keys": {"type": "integer", "default": 100},
        },
        "required": ["bucket"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("s3", {}).get("bucket"))

    def extract_params(self, sources: dict) -> dict:
        return {
            "bucket": sources.get("s3", {}).get("bucket"),
            "prefix": sources.get("s3", {}).get("prefix", ""),
            "max_keys": 100,
        }

    def run(self, bucket: str, prefix: str = "", max_keys: int = 100, **_kwargs) -> dict:
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


list_s3_objects = S3ListTool()
