#!/usr/bin/env python3
"""CDK app for Airflow on ECS Fargate test case."""

import os

import aws_cdk as cdk
from stacks.ecs_airflow_stack import EcsAirflowStack

app = cdk.App()

account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
region = (
    os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
)

if not account:
    import subprocess

    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            account = result.stdout.strip()
    except Exception:
        pass

if not account:
    raise ValueError("AWS account ID not found. Set CDK_DEFAULT_ACCOUNT or configure AWS CLI.")

EcsAirflowStack(
    app,
    "TracerAirflowEcsFargate",
    env=cdk.Environment(
        account=account,
        region=region,
    ),
    description="Airflow on ECS Fargate for upstream/downstream pipeline test case",
)

app.synth()
