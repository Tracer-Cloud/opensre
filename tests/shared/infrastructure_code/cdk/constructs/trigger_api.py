from aws_cdk import BundlingOptions, Duration
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class TriggerApiLambda(Construct):
    """Trigger Lambda with API Gateway wiring."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        code_path: str,
        handler: str,
        runtime: lambda_.Runtime,
        role: iam.IRole,
        environment: dict[str, str] | None = None,
        timeout: Duration = Duration.seconds(60),
        memory_size: int = 256,
        bundling: BundlingOptions | None = None,
        rest_api_name: str | None = None,
        description: str | None = None,
        vpc=None,
        vpc_subnets=None,
        security_groups=None,
    ) -> None:
        super().__init__(scope, construct_id)

        code = (
            lambda_.Code.from_asset(code_path, bundling=bundling)
            if bundling
            else lambda_.Code.from_asset(code_path)
        )

        self.lambda_function = lambda_.Function(
            self,
            "TriggerLambda",
            runtime=runtime,
            handler=handler,
            code=code,
            role=role,
            timeout=timeout,
            memory_size=memory_size,
            environment=environment,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            security_groups=security_groups,
        )

        self.api = apigw.LambdaRestApi(
            self,
            "TriggerApi",
            handler=self.lambda_function,
            rest_api_name=rest_api_name,
            description=description,
        )
