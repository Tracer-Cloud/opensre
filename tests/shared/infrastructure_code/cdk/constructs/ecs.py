from aws_cdk import aws_ecs as ecs
from constructs import Construct


def create_ecs_cluster(
    scope: Construct,
    construct_id: str,
    *,
    vpc,
    cluster_name: str,
    enable_fargate_capacity_providers: bool = True,
) -> ecs.Cluster:
    """Create an ECS cluster with consistent defaults."""
    return ecs.Cluster(
        scope,
        construct_id,
        vpc=vpc,
        cluster_name=cluster_name,
        enable_fargate_capacity_providers=enable_fargate_capacity_providers,
    )
