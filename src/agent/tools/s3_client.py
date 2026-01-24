"""S3 client (mock for demo)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class S3CheckResult:
    marker_exists: bool
    file_count: int
    files: list[str]


class MockS3Client:
    """S3 client backed by mock data."""

    def __init__(self):
        from src.mocks.s3 import get_s3_client
        self._mock = get_s3_client()

    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult:
        files = self._mock.list_objects(bucket, prefix)
        marker_exists = self._mock.object_exists(bucket, f"{prefix}_SUCCESS")
        return S3CheckResult(
            marker_exists=marker_exists,
            file_count=len(files),
            files=[f["key"] for f in files],
        )


def get_s3_client() -> MockS3Client:
    """Get S3 client (mock for demo)."""
    return MockS3Client()

