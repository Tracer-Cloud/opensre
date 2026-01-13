"""
S3 tools for the investigation agent.

Code #4: Context connectors
Functions that fetch context from S3, demonstrating the agent can see across systems.
"""

from langchain_core.tools import tool

from src.mocks.s3 import get_s3_client


@tool
def list_s3_files(bucket: str, prefix: str) -> dict:
    """
    List files in an S3 bucket with a given prefix.
    
    Use this to check if expected files exist in S3, such as:
    - Raw input files from upstream services
    - Processed output files from pipelines
    - Success markers (_SUCCESS files)
    
    Args:
        bucket: The S3 bucket name (e.g., 'tracer-raw-data', 'tracer-processed-data')
        prefix: The prefix to filter files (e.g., 'events/2026-01-13/')
    
    Returns:
        A dict with 'files' list containing file metadata, or 'error' if failed
    """
    client = get_s3_client()
    files = client.list_objects(bucket, prefix)
    
    if not files:
        return {
            "bucket": bucket,
            "prefix": prefix,
            "files": [],
            "count": 0,
            "message": f"No files found in s3://{bucket}/{prefix}"
        }
    
    return {
        "bucket": bucket,
        "prefix": prefix,
        "files": files,
        "count": len(files),
        "message": f"Found {len(files)} file(s) in s3://{bucket}/{prefix}"
    }


@tool
def check_success_marker(bucket: str, prefix: str) -> dict:
    """
    Check if a _SUCCESS marker exists in an S3 location.
    
    Many data pipelines write a _SUCCESS file to indicate that all output
    has been written successfully. Downstream services often wait for this
    marker before loading data.
    
    Args:
        bucket: The S3 bucket name
        prefix: The prefix/folder to check (e.g., 'events/2026-01-13/')
    
    Returns:
        A dict indicating if _SUCCESS exists and relevant details
    """
    client = get_s3_client()
    
    # Normalize prefix to ensure it ends with /
    if prefix and not prefix.endswith('/'):
        prefix = prefix + '/'
    
    success_key = f"{prefix}_SUCCESS"
    exists = client.object_exists(bucket, success_key)
    
    # Also check what files DO exist
    all_files = client.list_objects(bucket, prefix)
    
    return {
        "bucket": bucket,
        "prefix": prefix,
        "success_marker_path": f"s3://{bucket}/{success_key}",
        "success_marker_exists": exists,
        "files_in_location": [f["key"] for f in all_files],
        "file_count": len(all_files),
        "message": (
            f"_SUCCESS marker {'EXISTS' if exists else 'MISSING'} at s3://{bucket}/{success_key}. "
            f"Found {len(all_files)} other file(s) in location."
        )
    }

