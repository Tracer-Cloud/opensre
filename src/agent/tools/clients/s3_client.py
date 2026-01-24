"""S3 client (not implemented)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class S3CheckResult:
    marker_exists: bool
    file_count: int
    files: list[str]


class S3Client:
    """S3 client (not implemented)."""

    def check_marker(self, bucket: str, prefix: str) -> S3CheckResult:  # noqa: ARG002
        """Check for S3 marker (not implemented)."""
        return S3CheckResult(
            marker_exists=False,
            file_count=0,
            files=[],
        )


def get_s3_client() -> S3Client:
    """Get S3 client (not implemented)."""
    return S3Client()
