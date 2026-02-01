from aws_cdk import RemovalPolicy
from aws_cdk import aws_logs as logs
from constructs import Construct


def create_log_group(
    scope: Construct,
    construct_id: str,
    *,
    log_group_name: str,
    retention: logs.RetentionDays = logs.RetentionDays.ONE_WEEK,
    removal_policy: RemovalPolicy = RemovalPolicy.DESTROY,
) -> logs.LogGroup:
    """Create a CloudWatch log group with consistent defaults."""
    return logs.LogGroup(
        scope,
        construct_id,
        log_group_name=log_group_name,
        retention=retention,
        removal_policy=removal_policy,
    )
