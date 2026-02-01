from aws_cdk import Duration
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class MockExternalApi(Construct):
    """Mock external API Lambda + API Gateway."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        code_path: str,
        handler: str = "handler.lambda_handler",
        runtime: lambda_.Runtime = lambda_.Runtime.PYTHON_3_11,
        timeout: Duration = Duration.seconds(30),
        memory_size: int = 128,
        function_name: str | None = None,
        rest_api_name: str | None = None,
        description: str | None = None,
        stage_name: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        self.lambda_function = lambda_.Function(
            self,
            "MockApiLambda",
            runtime=runtime,
            handler=handler,
            code=lambda_.Code.from_asset(code_path),
            timeout=timeout,
            memory_size=memory_size,
            function_name=function_name,
            environment=environment,
        )

        deploy_options = apigw.StageOptions(stage_name=stage_name) if stage_name else None
        self.api = apigw.LambdaRestApi(
            self,
            "MockExternalApi",
            handler=self.lambda_function,
            rest_api_name=rest_api_name,
            description=description,
            deploy_options=deploy_options,
        )
