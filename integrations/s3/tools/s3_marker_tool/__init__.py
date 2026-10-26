"""S3 marker and related S3 utilities."""

from __future__ import annotations

from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.aws.s3_client import check_s3_marker_presence


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
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
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
    result = check_s3_marker_presence(bucket, prefix)
    return {
        "marker_exists": result.marker_exists,
        "file_count": result.file_count,
        "files": result.files,
    }
