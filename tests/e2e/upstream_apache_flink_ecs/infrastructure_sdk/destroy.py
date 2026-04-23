#!/usr/bin/env python3
"""Destroy Flink ECS infrastructure.

Uses tag-based cleanup to find and delete all resources created by the SDK deployment.
"""

import time
from contextlib import suppress

from tests.shared.infrastructure_sdk import destroy_stack, load_outputs
from tests.shared.infrastructure_sdk.config import delete_outputs
from tests.shared.infrastructure_sdk.deployer import get_boto3_client
from tests.shared.infrastructure_sdk.resources import ecr, ecs, iam, lambda_, logs, s3, vpc

STACK_NAME = "tracer-flink-ecs"
REGION = "us-east-1"


def destroy() -> dict:
    """Destroy all infrastructure resources."""
    start_time = time.time()
    print(f"Destroying stack: {STACK_NAME}")
    print("=" * 60)

    results = {"deleted": [], "failed": [], "not_found": []}

    # Try to load outputs for explicit resource deletion
    try:
        outputs = load_outputs(STACK_NAME)
        print("Found outputs file, performing explicit deletion...")
        _explicit_delete(outputs, results)
    except FileNotFoundError:
        print("No outputs file found, using tag-based discovery...")

    # Also run tag-based cleanup to catch any resources we might have missed
    print("\n[Tag-Based Cleanup]")
    tag_results = destroy_stack(STACK_NAME, REGION)

    # Merge results
    results["deleted"].extend(tag_results.get("deleted", []))
    results["failed"].extend(tag_results.get("failed", []))
    results["not_found"].extend(tag_results.get("not_found", []))

    # Delete outputs file
    with suppress(Exception):
        delete_outputs(STACK_NAME)

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"Destruction completed in {elapsed:.1f}s")
    print("=" * 60)
    print(f"\nDeleted: {len(results['deleted'])} resources")
    print(f"Failed: {len(results['failed'])} resources")
    print(f"Not Found: {len(results['not_found'])} resources")

    if results["failed"]:
        print("\nFailed resources:")
        for item in results["failed"]:
            if isinstance(item, dict):
                print(f"  - {item.get('arn', 'unknown')}: {item.get('error', 'unknown error')}")
            else:
                print(f"  - {item}")

    return results


def _explicit_delete(outputs: dict, results: dict) -> None:
    """Delete resources explicitly using outputs."""

    # Order: APIs -> Lambdas -> Task Definitions -> Clusters -> IAM -> S3 -> ECR -> Logs -> Security Groups

    # 1. Delete API Gateways
    print("\n[1/9] Deleting API Gateways...")
    for api_key in ["TriggerApiUrl", "MockApiUrl"]:
        url = outputs.get(api_key, "")
        if url:
            # Extract API ID from URL: https://xxx.execute-api.region.amazonaws.com/prod
            try:
                api_id = url.split("//")[1].split(".")[0]
                print(f"  - Deleting API: {api_id}")
                api_client = get_boto3_client("apigateway", REGION)
                api_client.delete_rest_api(restApiId=api_id)
                results["deleted"].append(f"apigateway:{api_id}")
            except Exception as e:
                if "NotFoundException" in str(e):
                    results["not_found"].append(f"apigateway:{api_id}")
                else:
                    results["failed"].append({"arn": f"apigateway:{api_id}", "error": str(e)})

    # 2. Delete Lambda functions
    print("\n[2/9] Deleting Lambda functions...")
    lambda_names = [
        outputs.get("TriggerLambdaName"),
        outputs.get("MockApiLambdaName"),
    ]
    for name in lambda_names:
        if name:
            try:
                print(f"  - Deleting Lambda: {name}")
                lambda_.delete_function(name, REGION)
                results["deleted"].append(f"lambda:{name}")
            except Exception as e:
                if "ResourceNotFoundException" in str(e):
                    results["not_found"].append(f"lambda:{name}")
                else:
                    results["failed"].append({"arn": f"lambda:{name}", "error": str(e)})

    # 3. Deregister task definitions
    print("\n[3/9] Deregistering task definitions...")
    task_def_arn = outputs.get("TaskDefinitionArn")
    if task_def_arn:
        try:
            print(f"  - Deregistering: {task_def_arn}")
            ecs.deregister_task_definition(task_def_arn, REGION)
            results["deleted"].append(task_def_arn)
        except Exception as e:
            results["failed"].append({"arn": task_def_arn, "error": str(e)})

    # 4. Delete ECS cluster
    print("\n[4/9] Deleting ECS cluster...")
    cluster_name = outputs.get("EcsClusterName")
    if cluster_name:
        try:
            print(f"  - Deleting cluster: {cluster_name}")
            ecs.delete_cluster(cluster_name, REGION)
            results["deleted"].append(f"ecs:cluster/{cluster_name}")
        except Exception as e:
            if "ClusterNotFoundException" in str(e):
                results["not_found"].append(f"ecs:cluster/{cluster_name}")
            else:
                results["failed"].append({"arn": f"ecs:cluster/{cluster_name}", "error": str(e)})

    # 5. Delete IAM roles
    print("\n[5/9] Deleting IAM roles...")
    role_names = [
        f"{STACK_NAME}-task-role",
        f"{STACK_NAME}-execution-role",
        f"{STACK_NAME}-trigger-role",
        f"{STACK_NAME}-mock-api-role",
    ]
    for role_name in role_names:
        try:
            print(f"  - Deleting role: {role_name}")
            iam.delete_role(role_name, REGION)
            results["deleted"].append(f"iam:role/{role_name}")
        except Exception as e:
            if "NoSuchEntity" in str(e):
                results["not_found"].append(f"iam:role/{role_name}")
            else:
                results["failed"].append({"arn": f"iam:role/{role_name}", "error": str(e)})

    # 6. Delete S3 buckets
    print("\n[6/9] Deleting S3 buckets...")
    bucket_names = [
        outputs.get("LandingBucketName"),
        outputs.get("ProcessedBucketName"),
    ]
    for bucket_name in bucket_names:
        if bucket_name:
            try:
                print(f"  - Deleting bucket: {bucket_name}")
                s3.delete_bucket(bucket_name, REGION)
                results["deleted"].append(f"s3:{bucket_name}")
            except Exception as e:
                if "NoSuchBucket" in str(e):
                    results["not_found"].append(f"s3:{bucket_name}")
                else:
                    results["failed"].append({"arn": f"s3:{bucket_name}", "error": str(e)})

    # 7. Delete ECR repository
    print("\n[7/9] Deleting ECR repository...")
    ecr_uri = outputs.get("EcrRepositoryUri")
    if ecr_uri:
        # Extract repo name from URI: account.dkr.ecr.region.amazonaws.com/repo-name
        repo_name = ecr_uri.split("/")[-1]
        try:
            print(f"  - Deleting repository: {repo_name}")
            ecr.delete_repository(repo_name, REGION)
            results["deleted"].append(f"ecr:{repo_name}")
        except Exception as e:
            if "RepositoryNotFoundException" in str(e):
                results["not_found"].append(f"ecr:{repo_name}")
            else:
                results["failed"].append({"arn": f"ecr:{repo_name}", "error": str(e)})

    # 8. Delete CloudWatch log group
    print("\n[8/9] Deleting CloudWatch log group...")
    log_group_name = outputs.get("LogGroupName")
    if log_group_name:
        try:
            print(f"  - Deleting log group: {log_group_name}")
            logs.delete_log_group(log_group_name, REGION)
            results["deleted"].append(f"logs:{log_group_name}")
        except Exception as e:
            if "ResourceNotFoundException" in str(e):
                results["not_found"].append(f"logs:{log_group_name}")
            else:
                results["failed"].append({"arn": f"logs:{log_group_name}", "error": str(e)})

    # 9. Delete security group
    print("\n[9/9] Deleting security group...")
    sg_id = outputs.get("SecurityGroupId")
    if sg_id:
        # Wait a bit for ENIs to detach
        time.sleep(5)
        try:
            print(f"  - Deleting security group: {sg_id}")
            vpc.delete_security_group(sg_id, REGION)
            results["deleted"].append(f"ec2:security-group/{sg_id}")
        except Exception as e:
            if "InvalidGroup.NotFound" in str(e):
                results["not_found"].append(f"ec2:security-group/{sg_id}")
            elif "DependencyViolation" in str(e):
                print("    Warning: Security group has dependencies, may need manual cleanup")
                results["failed"].append(
                    {
                        "arn": f"ec2:security-group/{sg_id}",
                        "error": "DependencyViolation - ENIs still attached",
                    }
                )
            else:
                results["failed"].append({"arn": f"ec2:security-group/{sg_id}", "error": str(e)})


if __name__ == "__main__":
    destroy()
