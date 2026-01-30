#!/usr/bin/env python3
"""CDK app for ECS Fargate Airflow test case infrastructure."""

import aws_cdk as cdk
from stacks.ecs_airflow_stack import EcsAirflowTestCaseStack

app = cdk.App()

EcsAirflowTestCaseStack(
    app,
    "TracerEcsAirflowTestCase",
    env=cdk.Environment(
        account=cdk.Aws.ACCOUNT_ID,
        region=cdk.Aws.REGION,
    ),
    description="ECS Fargate Airflow test case for Tracer agent upstream/downstream failure detection",
)

app.synth()
