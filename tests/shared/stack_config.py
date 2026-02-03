"""Dynamic stack configuration loader.

Fetches configuration from CloudFormation stack outputs.
No more hardcoded URLs/IPs - single source of truth.
"""

import boto3


def get_stack_outputs(stack_name: str, region: str = "us-east-1") -> dict:
    """Fetch all outputs from a CloudFormation stack."""
    cf = boto3.client("cloudformation", region_name=region)
    response = cf.describe_stacks(StackName=stack_name)
    outputs = {}
    for output in response["Stacks"][0].get("Outputs", []):
        outputs[output["OutputKey"]] = output["OutputValue"]
    return outputs


def get_ecs_task_public_ip(cluster_name: str, region: str = "us-east-1") -> str | None:
    """Fetch public IP of a running ECS task (for services without load balancer)."""
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

    # Prefect server needs dynamic IP lookup
    prefect_ip = get_ecs_task_public_ip(cluster_name) if cluster_name else None
    prefect_api_url = f"http://{prefect_ip}:4200/api" if prefect_ip else None

    return {
        "prefect_api_url": prefect_api_url,
        "trigger_api_url": outputs.get("TriggerApiUrl"),
        "mock_api_url": outputs.get("MockApiUrl"),
        "log_group": outputs.get("LogGroupName"),
        "ecs_cluster": cluster_name,
        "s3_bucket": outputs.get("LandingBucketName"),
    }
