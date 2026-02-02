from __future__ import annotations

from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class GrafanaCloudSecrets(Construct):
    """Access Grafana Cloud secrets for ECS and Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        secret_name: str = "tracer/grafana-cloud",
    ) -> None:
        super().__init__(scope, construct_id)
        self.secret = secretsmanager.Secret.from_secret_name_v2(
            self, "GrafanaCloudSecret", secret_name
        )

    def ecs_secret(self, field: str) -> ecs.Secret:
        return ecs.Secret.from_secrets_manager(self.secret, field)

    def lambda_env(self, field: str) -> str:
        return self.secret.secret_value_from_json(field).to_string()
