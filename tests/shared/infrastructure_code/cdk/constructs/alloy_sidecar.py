from __future__ import annotations

from pathlib import Path

from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_logs as logs
from constructs import Construct

from .grafana_secrets import GrafanaCloudSecrets


class AlloySidecar(Construct):
    """Grafana Alloy sidecar for OTLP collection."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        task_definition: ecs.FargateTaskDefinition,
        log_group: logs.ILogGroup,
        grafana_secrets: GrafanaCloudSecrets,
        container_name: str = "AlloySidecar",
    ) -> None:
        super().__init__(scope, construct_id)

        config_dir = Path(__file__).resolve().parents[2] / "alloy_config"

        self.container = task_definition.add_container(
            container_name,
            image=ecs.ContainerImage.from_asset(str(config_dir)),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="alloy",
                log_group=log_group,
            ),
            secrets={
                "GCLOUD_HOSTED_METRICS_URL": grafana_secrets.ecs_secret(
                    "GCLOUD_HOSTED_METRICS_URL"
                ),
                "GCLOUD_HOSTED_METRICS_ID": grafana_secrets.ecs_secret(
                    "GCLOUD_HOSTED_METRICS_ID"
                ),
                "GCLOUD_HOSTED_LOGS_URL": grafana_secrets.ecs_secret(
                    "GCLOUD_HOSTED_LOGS_URL"
                ),
                "GCLOUD_HOSTED_LOGS_ID": grafana_secrets.ecs_secret("GCLOUD_HOSTED_LOGS_ID"),
                "GCLOUD_RW_API_KEY": grafana_secrets.ecs_secret("GCLOUD_RW_API_KEY"),
                "GCLOUD_OTLP_ENDPOINT": grafana_secrets.ecs_secret("GCLOUD_OTLP_ENDPOINT"),
                "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.ecs_secret(
                    "GCLOUD_OTLP_AUTH_HEADER"
                ),
            },
        )

        self.container.add_port_mappings(
            ecs.PortMapping(container_port=4317, protocol=ecs.Protocol.TCP),
            ecs.PortMapping(container_port=4318, protocol=ecs.Protocol.TCP),
            ecs.PortMapping(container_port=12345, protocol=ecs.Protocol.TCP),
        )
