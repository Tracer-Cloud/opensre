"""Mock services for demo - S3, Nextflow (fallback)."""

from src.mocks.s3 import MockS3Client
from src.mocks.nextflow import MockNextflowClient

__all__ = [
    "MockS3Client",
    "MockNextflowClient",
]

