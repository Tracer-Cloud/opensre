"""S3 investigation tools."""

from __future__ import annotations

from app.tools.clients.s3_client import (
    compare_versions,
    get_full_object,
    get_object_metadata,
    get_object_sample,
    get_s3_client,
    head_object,
    list_object_versions,
    list_objects,
)
from app.tools.tool_actions.base import BaseTool


class S3MarkerTool(BaseTool):
    """Check if a _SUCCESS marker exists in S3 storage."""

    name = "check_s3_marker"
    source = "storage"
    description = "Check if a _SUCCESS marker exists in S3 storage to verify pipeline completion."
    use_cases = [
        "Verifying if a data pipeline run completed successfully",
        "Checking for presence of a _SUCCESS marker file",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "prefix": {"type": "string"},
        },
        "required": ["bucket", "prefix"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(
            (sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("prefix"))
            or sources.get("s3_processed", {}).get("bucket")
        )

    def extract_params(self, sources: dict) -> dict:
        if sources.get("s3_processed"):
            return {
                "bucket": sources["s3_processed"].get("bucket"),
                "prefix": sources["s3_processed"].get("prefix", ""),
            }
        return {
            "bucket": sources.get("s3", {}).get("bucket"),
            "prefix": sources.get("s3", {}).get("prefix"),
        }

    def run(self, bucket: str, prefix: str, **_kwargs) -> dict:
        client = get_s3_client()
        result = client.check_marker(bucket, prefix)
        return {
            "marker_exists": result.marker_exists,
            "file_count": result.file_count,
            "files": result.files,
        }


class S3InspectTool(BaseTool):
    """Inspect an S3 object's metadata and sample content."""

    name = "inspect_s3_object"
    source = "storage"
    description = "Inspect an S3 object's metadata and sample content."
    use_cases = [
        "Tracing data lineage upstream to find root cause",
        "Identifying schema changes in input data",
        "Finding audit trails for external vendor interactions",
        "Discovering which Lambda function produced the data",
    ]
    requires = ["bucket", "key"]
    input_schema = {
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "key": {"type": "string"},
        },
        "required": ["bucket", "key"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key"))

    def extract_params(self, sources: dict) -> dict:
        return {
            "bucket": sources.get("s3", {}).get("bucket"),
            "key": sources.get("s3", {}).get("key"),
        }

    def run(self, bucket: str, key: str, **_kwargs) -> dict:
        if not bucket or not key:
            return {"error": "bucket and key are required"}

        metadata_result = get_object_metadata(bucket, key)
        if not metadata_result.get("success"):
            return {"error": metadata_result.get("error", "Unknown error"), "bucket": bucket, "key": key}
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


class S3GetObjectTool(BaseTool):
    """Get full S3 object content (audit payloads, configs, lineage data)."""

    name = "get_s3_object"
    source = "storage"
    description = "Get full S3 object content — audit payloads, configs, lineage data."
    use_cases = [
        "Retrieving audit payloads when audit_key found in S3 metadata",
        "Tracing external vendor interactions that caused failures",
        "Reading configuration or manifest files",
        "Finding upstream data lineage details",
    ]
    requires = ["bucket", "key"]
    input_schema = {
        "type": "object",
        "properties": {
            "bucket": {"type": "string"},
            "key": {"type": "string"},
        },
        "required": ["bucket", "key"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(
            (sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key"))
            or (sources.get("s3_audit", {}).get("bucket") and sources.get("s3_audit", {}).get("key"))
        )

    def extract_params(self, sources: dict) -> dict:
        if sources.get("s3_audit"):
            return {
                "bucket": sources["s3_audit"].get("bucket"),
                "key": sources["s3_audit"].get("key"),
            }
        return {
            "bucket": sources.get("s3", {}).get("bucket"),
            "key": sources.get("s3", {}).get("key"),
        }

    def run(self, bucket: str, key: str, **_kwargs) -> dict:
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


# Additional S3 tools (not in investigation registry but kept for completeness)
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


# Backward-compatible aliases
check_s3_marker = S3MarkerTool()
inspect_s3_object = S3InspectTool()
list_s3_objects = S3ListTool()
get_s3_object = S3GetObjectTool()
