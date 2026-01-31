#!/usr/bin/env python3
"""CDK app for Apache Flink ECS test case."""

import os

import aws_cdk as cdk
import boto3
from stacks.ecs_flink_stack import EcsFlinkStack

app = cdk.App()

# Get account from environment or current AWS credentials
account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
if not account:
    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]

EcsFlinkStack(
    app,
    "TracerFlinkEcs",
    env=cdk.Environment(account=account, region="us-east-1"),
)

app.synth()
