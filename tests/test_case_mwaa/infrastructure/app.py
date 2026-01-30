#!/usr/bin/env python3
"""CDK app for MWAA test case infrastructure."""

import aws_cdk as cdk

from stacks.mwaa_stack import MwaaTestCaseStack

app = cdk.App()

MwaaTestCaseStack(
    app,
    "TracerMwaaTestCase",
    env=cdk.Environment(
        account=cdk.Aws.ACCOUNT_ID,
        region=cdk.Aws.REGION,
    ),
    description="MWAA test case for Tracer agent upstream/downstream failure detection",
)

app.synth()
