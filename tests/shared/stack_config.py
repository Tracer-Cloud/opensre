"""Dynamic stack configuration loader.

Fetches configuration from CloudFormation stack outputs.
No more hardcoded URLs/IPs - single source of truth.
"""

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


def get_stack_outputs(stack_name: str, region: str = "us-east-1") -> dict:
    """Fetch all outputs from a CloudFormation stack.

    Returns empty dict if AWS credentials are unavailable (allows test collection).
    """
    try:
        cf = boto3.client("cloudformation", region_name=region)
        response = cf.describe_stacks(StackName=stack_name)
        outputs = {}
        for output in response["Stacks"][0].get("Outputs", []):
            outputs[output["OutputKey"]] = output["OutputValue"]
        return outputs
    except (NoCredentialsError, BotoCoreError, ClientError):
        # Return empty dict - tests will be skipped when config values are missing
        return {}


def get_ecs_task_public_ip(cluster_name: str, region: str = "us-east-1") -> str | None:
    """Fetch public IP of a running ECS task (for services without load balancer).

    Returns None if AWS credentials are unavailable.
    """
    try:
        ecs = boto3.client("ecs", region_name=region)
        ec2 = boto3.client("ec2", region_name=region)

        tasks = ecs.list_tasks(cluster=cluster_name, desiredStatus="RUNNING")
        if not tasks.get("taskArns"):
            return None

        task_details = ecs.describe_tasks(cluster=cluster_name, tasks=tasks["taskArns"])
        for task in task_details.get("tasks", []):
            for attachment in task.get("attachments", []):
                for detail in attachment.get("details", []):
                    if detail.get("name") == "networkInterfaceId":
                        eni_id = detail.get("value")
                        eni = ec2.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
                        public_ip = eni["NetworkInterfaces"][0].get("Association", {}).get("PublicIp")
                        if public_ip:
                            return public_ip
        return None
    except (NoCredentialsError, BotoCoreError, ClientError):
        return None


# Stack configurations
STACKS = {
    "flink": "TracerFlinkEcs",
    "prefect": "TracerPrefectEcsFargate",
    "lambda": "TracerUpstreamLambda",
}


def get_flink_config() -> dict:
    """Get Flink test configuration from stack outputs."""
    outputs = get_stack_outputs(STACKS["flink"])
    return {
        "trigger_api_url": outputs.get("TriggerApiUrl"),
        "mock_api_url": outputs.get("MockApiUrl"),
        "log_group": outputs.get("LogGroupName"),
        "ecs_cluster": outputs.get("EcsClusterName"),
        "landing_bucket": outputs.get("LandingBucketName"),
        "processed_bucket": outputs.get("ProcessedBucketName"),
    }


def get_prefect_config() -> dict:
    """Get Prefect test configuration from stack outputs."""
    outputs = get_stack_outputs(STACKS["prefect"])
    cluster_name = outputs.get("EcsClusterName")

    return {
        "trigger_api_url": outputs.get("TriggerApiUrl"),
        "mock_api_url": outputs.get("MockApiUrl"),
        "log_group": outputs.get("LogGroupName"),
        "ecs_cluster": cluster_name,
        "s3_bucket": outputs.get("LandingBucketName"),
        "processed_bucket": outputs.get("ProcessedBucketName"),
        "flow_task_definition": outputs.get("FlowTaskDefinitionArn"),
        "security_group_id": outputs.get("SecurityGroupId"),
        "subnet_ids": outputs.get("SubnetIds"),
    }
