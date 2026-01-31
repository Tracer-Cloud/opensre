"""
S3 tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
"""

from app.agent.tools.clients.s3_client import (
    S3CheckResult,
    compare_versions,
    get_object_metadata,
    get_object_sample,
    get_s3_client,
    head_object,
    list_object_versions,
    list_objects,
)


def check_s3_marker(bucket: str, prefix: str) -> S3CheckResult:
    """
    Check if _SUCCESS marker exists in S3 storage.

    Use this tool to verify if a data pipeline run completed successfully by checking
    for the presence of a _SUCCESS marker file in the specified S3 location.

    Args:
        bucket: S3 bucket name
        prefix: S3 key prefix (path) where the marker should be located

    Returns:
        S3CheckResult with marker existence status and file count
    """
    client = get_s3_client()
    return client.check_marker(bucket, prefix)


def inspect_s3_object(bucket: str, key: str) -> dict:
    """
    Inspect an S3 object's metadata and sample content.

    Use this when investigating data issues to understand:
    - Object existence and size
    - Content type and format
    - Sample data for schema validation

    Useful for:
    - Verifying input data exists
    - Checking data format and structure
    - Identifying schema changes in data files

    Args:
        bucket: S3 bucket name
        key: S3 object key (full path)

    Returns:
        Dictionary with object metadata and content sample
    """
    if not bucket or not key:
        return {"error": "bucket and key are required"}

    # Get metadata first
    metadata_result = get_object_metadata(bucket, key)

    if not metadata_result.get("success"):
        return {
            "error": metadata_result.get("error", "Unknown error"),
            "bucket": bucket,
            "key": key,
        }

    if not metadata_result.get("exists"):
        return {
            "found": False,
            "bucket": bucket,
            "key": key,
            "message": "Object does not exist",
        }

    # Get sample content
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
        "is_text": sample_data.get("is_text", False),
        "sample": sample_data.get("sample"),
        "sample_bytes": sample_data.get("sample_bytes"),
    }


def list_s3_versions(bucket: str, key: str, max_versions: int = 10) -> dict:
    """
    List version history for an S3 object.

    Use this to investigate data changes over time and identify when
    schema or content changes occurred.

    Useful for:
    - Tracing when data changed
    - Identifying upstream schema changes
    - Comparing versions to find root cause

    Args:
        bucket: S3 bucket name
        key: S3 object key
        max_versions: Maximum versions to return (default 10)

    Returns:
        Dictionary with version history
    """
    if not bucket or not key:
        return {"error": "bucket and key are required"}

    result = list_object_versions(bucket, key, max_versions)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "bucket": bucket,
            "key": key,
        }

    data = result.get("data", {})

    return {
        "found": bool(data.get("versions")),
        "bucket": bucket,
        "key": key,
        "version_count": data.get("version_count", 0),
        "versions": data.get("versions", []),
        "delete_markers": data.get("delete_markers", []),
    }


def compare_s3_versions(
    bucket: str,
    key: str,
    version_id_1: str,
    version_id_2: str,
) -> dict:
    """
    Compare two versions of an S3 object to identify changes.

    Use this to identify specific differences between data versions,
    such as schema changes or content modifications.

    Useful for:
    - Identifying schema changes (missing/added fields)
    - Detecting data format changes
    - Tracing upstream data issues

    Args:
        bucket: S3 bucket name
        key: S3 object key
        version_id_1: First version ID (older)
        version_id_2: Second version ID (newer)

    Returns:
        Dictionary with comparison results
    """
    if not bucket or not key:
        return {"error": "bucket and key are required"}
    if not version_id_1 or not version_id_2:
        return {"error": "Both version_id_1 and version_id_2 are required"}

    result = compare_versions(bucket, key, version_id_1, version_id_2)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "bucket": bucket,
            "key": key,
        }

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
    """
    Check if an S3 object exists.

    Quick check for object existence without downloading content.

    Args:
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        Dictionary with existence status
    """
    if not bucket or not key:
        return {"error": "bucket and key are required"}

    result = head_object(bucket, key)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "bucket": bucket,
            "key": key,
        }

    return {
        "exists": result.get("exists", False),
        "bucket": bucket,
        "key": key,
        "size": result.get("data", {}).get("size") if result.get("exists") else None,
    }


def list_s3_objects(bucket: str, prefix: str = "", max_keys: int = 100) -> dict:
    """
    List objects in an S3 bucket with optional prefix filter.

    Use this to explore S3 bucket contents and find relevant data files.

    Args:
        bucket: S3 bucket name
        prefix: Key prefix filter (optional)
        max_keys: Maximum objects to return (default 100)

    Returns:
        Dictionary with object list
    """
    if not bucket:
        return {"error": "bucket is required"}

    result = list_objects(bucket, prefix, max_keys)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "bucket": bucket,
            "prefix": prefix,
        }

    data = result.get("data", {})

    return {
        "found": bool(data.get("objects")),
        "bucket": bucket,
        "prefix": prefix,
        "count": data.get("count", 0),
        "objects": data.get("objects", []),
        "is_truncated": data.get("is_truncated", False),
    }
