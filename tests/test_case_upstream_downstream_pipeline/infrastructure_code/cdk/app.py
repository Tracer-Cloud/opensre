#!/usr/bin/env python3
"""CDK app for minimal Lambda-only upstream/downstream test case."""

import aws_cdk as cdk
from stacks.minimal_lambda_stack import MinimalLambdaTestCaseStack

app = cdk.App()

MinimalLambdaTestCaseStack(
    app,
    "TracerUpstreamDownstreamTest",
    env=cdk.Environment(
        account=cdk.Aws.ACCOUNT_ID,
        region=cdk.Aws.REGION,
    ),
    description="Minimal Lambda-only test case for upstream/downstream failure tracing",
)

app.synth()
