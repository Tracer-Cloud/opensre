from aws_cdk import RemovalPolicy
from aws_cdk import aws_s3 as s3
from constructs import Construct


class LandingProcessedBuckets(Construct):
    """Construct for landing + processed S3 buckets."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        versioned: bool = False,
        removal_policy: RemovalPolicy = RemovalPolicy.DESTROY,
        auto_delete_objects: bool = True,
        block_public_access: s3.BlockPublicAccess | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        bucket_kwargs = {
            "versioned": versioned,
            "removal_policy": removal_policy,
            "auto_delete_objects": auto_delete_objects,
        }
        if block_public_access is not None:
            bucket_kwargs["block_public_access"] = block_public_access

        self.landing_bucket = s3.Bucket(self, "LandingBucket", **bucket_kwargs)
        self.processed_bucket = s3.Bucket(self, "ProcessedBucket", **bucket_kwargs)
