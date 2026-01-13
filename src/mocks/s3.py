"""Mock S3 client for the demo."""

from datetime import datetime
from typing import Optional
from fixtures.mock_data import (
    S3_RAW_BUCKET,
    S3_PROCESSED_BUCKET,
    S3_RAW_FILES,
    S3_PROCESSED_FILES,
)


class MockS3Client:
    """Mock S3 client that returns predefined data for the demo scenario."""

    def __init__(self):
        self._buckets = {
            S3_RAW_BUCKET: S3_RAW_FILES,
            S3_PROCESSED_BUCKET: S3_PROCESSED_FILES,
        }

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict]:
        """
        List objects in a bucket with optional prefix filter.
        
        Returns:
            List of objects with key, size, last_modified, etag
        """
        if bucket not in self._buckets:
            return []
        
        files = self._buckets[bucket]
        if prefix:
            files = [f for f in files if f["key"].startswith(prefix)]
        
        return [
            {
                "key": f["key"],
                "size": f["size"],
                "last_modified": f["last_modified"].isoformat(),
                "etag": f["etag"],
            }
            for f in files
        ]

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check if an object exists in a bucket."""
        if bucket not in self._buckets:
            return False
        return any(f["key"] == key for f in self._buckets[bucket])

    def get_object_metadata(self, bucket: str, key: str) -> Optional[dict]:
        """Get metadata for a specific object."""
        if bucket not in self._buckets:
            return None
        
        for f in self._buckets[bucket]:
            if f["key"] == key:
                return {
                    "key": f["key"],
                    "size": f["size"],
                    "last_modified": f["last_modified"].isoformat(),
                    "etag": f["etag"],
                }
        return None


# Singleton instance for the demo
_s3_client: Optional[MockS3Client] = None


def get_s3_client() -> MockS3Client:
    """Get the mock S3 client singleton."""
    global _s3_client
    if _s3_client is None:
        _s3_client = MockS3Client()
    return _s3_client

