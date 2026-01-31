"""S3 client for data inspection and version tracking."""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception


@dataclass(frozen=True)
class S3CheckResult:
    """Result of S3 marker check."""

    marker_exists: bool
    file_count: int
    files: list[str]


@dataclass(frozen=True)
class S3ObjectMetadata:
    """Metadata for an S3 object."""

    bucket: str
    key: str
    exists: bool
    size: int | None = None
    last_modified: datetime | None = None
    content_type: str | None = None
    etag: str | None = None
    version_id: str | None = None
    storage_class: str | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class S3ObjectVersion:
    """Version information for an S3 object."""

    version_id: str
    last_modified: datetime
    size: int
    etag: str
    is_latest: bool
    is_delete_marker: bool = False


def _get_s3_client():
    """Get or create S3 client."""
    if boto3 is None:
        return None
    return boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


def head_object(bucket: str, key: str) -> dict[str, Any]:
    """
    Check if an S3 object exists and get its metadata.

    Args:
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        dict with exists flag and metadata
    """
    client = _get_s3_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.head_object(Bucket=bucket, Key=key)
        return {
            "success": True,
            "exists": True,
            "data": {
                "bucket": bucket,
                "key": key,
                "size": response.get("ContentLength"),
                "last_modified": response.get("LastModified"),
                "content_type": response.get("ContentType"),
                "etag": response.get("ETag", "").strip('"'),
                "version_id": response.get("VersionId"),
                "storage_class": response.get("StorageClass"),
                "metadata": response.get("Metadata", {}),
            },
        }
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "404" or error_code == "NoSuchKey":
            return {
                "success": True,
                "exists": False,
                "data": {"bucket": bucket, "key": key},
            }
        return {"success": False, "error": str(e)}


def get_object_metadata(bucket: str, key: str) -> dict[str, Any]:
    """
    Get detailed metadata for an S3 object.

    Alias for head_object with additional processing.

    Args:
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        dict with object metadata
    """
    return head_object(bucket, key)


def get_object_sample(
    bucket: str,
    key: str,
    max_bytes: int = 4096,
) -> dict[str, Any]:
    """
    Get a sample of an S3 object's contents.

    Useful for schema inference without downloading entire file.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        max_bytes: Maximum bytes to read (default 4KB)

    Returns:
        dict with sample content and metadata
    """
    client = _get_s3_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.get_object(
            Bucket=bucket,
            Key=key,
            Range=f"bytes=0-{max_bytes - 1}",
        )

        body = response["Body"].read()

        try:
            sample_text = body.decode("utf-8")
            is_text = True
        except UnicodeDecodeError:
            sample_text = None
            is_text = False

        return {
            "success": True,
            "data": {
                "bucket": bucket,
                "key": key,
                "content_type": response.get("ContentType"),
                "sample_bytes": len(body),
                "total_size": response.get("ContentLength"),
                "is_text": is_text,
                "sample": sample_text,
                "sample_raw_hex": body[:256].hex() if not is_text else None,
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def list_object_versions(
    bucket: str,
    key: str,
    max_versions: int = 10,
) -> dict[str, Any]:
    """
    List version history for an S3 object.

    Requires versioning enabled on the bucket.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        max_versions: Maximum versions to return

    Returns:
        dict with version list
    """
    client = _get_s3_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.list_object_versions(
            Bucket=bucket,
            Prefix=key,
            MaxKeys=max_versions,
        )

        versions = []
        for v in response.get("Versions", []):
            if v.get("Key") == key:
                versions.append(
                    {
                        "version_id": v.get("VersionId"),
                        "last_modified": v.get("LastModified"),
                        "size": v.get("Size"),
                        "etag": v.get("ETag", "").strip('"'),
                        "is_latest": v.get("IsLatest", False),
                        "storage_class": v.get("StorageClass"),
                    }
                )

        delete_markers = []
        for dm in response.get("DeleteMarkers", []):
            if dm.get("Key") == key:
                delete_markers.append(
                    {
                        "version_id": dm.get("VersionId"),
                        "last_modified": dm.get("LastModified"),
                        "is_latest": dm.get("IsLatest", False),
                    }
                )

        return {
            "success": True,
            "data": {
                "bucket": bucket,
                "key": key,
                "versions": versions,
                "delete_markers": delete_markers,
                "version_count": len(versions),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def compare_versions(
    bucket: str,
    key: str,
    version_id_1: str,
    version_id_2: str,
    max_bytes: int = 4096,
) -> dict[str, Any]:
    """
    Compare two versions of an S3 object.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        version_id_1: First version ID
        version_id_2: Second version ID
        max_bytes: Maximum bytes to compare

    Returns:
        dict with comparison results
    """
    client = _get_s3_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        # Get both versions
        resp1 = client.get_object(
            Bucket=bucket,
            Key=key,
            VersionId=version_id_1,
            Range=f"bytes=0-{max_bytes - 1}",
        )
        resp2 = client.get_object(
            Bucket=bucket,
            Key=key,
            VersionId=version_id_2,
            Range=f"bytes=0-{max_bytes - 1}",
        )

        body1 = resp1["Body"].read()
        body2 = resp2["Body"].read()

        try:
            text1 = body1.decode("utf-8")
            text2 = body2.decode("utf-8")
            is_text = True
        except UnicodeDecodeError:
            text1 = None
            text2 = None
            is_text = False

        return {
            "success": True,
            "data": {
                "bucket": bucket,
                "key": key,
                "version_1": {
                    "version_id": version_id_1,
                    "size": resp1.get("ContentLength"),
                    "content_type": resp1.get("ContentType"),
                    "sample": text1,
                },
                "version_2": {
                    "version_id": version_id_2,
                    "size": resp2.get("ContentLength"),
                    "content_type": resp2.get("ContentType"),
                    "sample": text2,
                },
                "are_identical": body1 == body2,
                "size_diff": (resp2.get("ContentLength") or 0) - (resp1.get("ContentLength") or 0),
                "is_text": is_text,
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def list_objects(
    bucket: str,
    prefix: str = "",
    max_keys: int = 100,
) -> dict[str, Any]:
    """
    List objects in an S3 bucket with optional prefix.

    Args:
        bucket: S3 bucket name
        prefix: Key prefix filter
        max_keys: Maximum objects to return

    Returns:
        dict with object list
    """
    client = _get_s3_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            MaxKeys=max_keys,
        )

        objects = []
        for obj in response.get("Contents", []):
            objects.append(
                {
                    "key": obj.get("Key"),
                    "size": obj.get("Size"),
                    "last_modified": obj.get("LastModified"),
                    "etag": obj.get("ETag", "").strip('"'),
                    "storage_class": obj.get("StorageClass"),
                }
            )

        return {
            "success": True,
            "data": {
                "bucket": bucket,
                "prefix": prefix,
                "objects": objects,
                "count": len(objects),
                "is_truncated": response.get("IsTruncated", False),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


# Legacy class interface for backwards compatibility
class S3Client:
    """S3 client wrapper class."""

    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult:
        """Check for S3 marker file."""
        result = list_objects(bucket, prefix, max_keys=100)
        if not result.get("success"):
            return S3CheckResult(marker_exists=False, file_count=0, files=[])

        data = result.get("data", {})
        objects = data.get("objects", [])
        files = [obj["key"] for obj in objects]
        marker_exists = any("_SUCCESS" in f or "marker" in f.lower() for f in files)

        return S3CheckResult(
            marker_exists=marker_exists,
            file_count=len(files),
            files=files,
        )


def get_s3_client() -> S3Client:
    """Get S3 client instance."""
    return S3Client()
