"""Pages in the OpenSRE app where an integration is connected.

Chat surfaces cannot run the interactive setup wizard. They point at the
same page the webapp uses (``/integrations/{slug}``). The interactive shell
still runs the setup steps itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from config.constants.billing import WEBAPP_URL_ENV

# Catalog ids that have a page under /integrations. Display names match the
# webapp catalog. Extra aliases are how people name them in Slack.
_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("datadog", "Datadog", ()),
    ("grafana", "Grafana", ()),
    ("sentry", "Sentry", ()),
    ("honeycomb", "Honeycomb", ()),
    ("coralogix", "Coralogix", ()),
    ("groundcover", "Groundcover", ()),
    ("signoz", "SigNoz", ()),
    ("openobserve", "OpenObserve", ()),
    ("splunk", "Splunk", ()),
    ("victoria_logs", "VictoriaLogs", ("victoria logs",)),
    ("tempo", "Grafana Tempo", ()),
    ("posthog", "PostHog", ()),
    ("alertmanager", "Alertmanager", ()),
    ("betterstack", "Better Stack", ()),
    ("cloudwatch", "Amazon CloudWatch", ("cloudwatch", "cloud watch")),
    ("azure_monitor", "Azure Monitor", ()),
    ("hermes", "Hermes", ()),
    ("pagerduty", "PagerDuty", ("pager duty",)),
    ("opsgenie", "Opsgenie", ()),
    ("incident_io", "incident.io", ("incidentio", "incident io")),
    ("aws", "AWS", ()),
    ("aws_lambda", "AWS Lambda", ("lambda",)),
    ("ec2", "Amazon EC2", ("ec2",)),
    ("eks", "Amazon EKS", ("eks",)),
    ("elb", "Elastic Load Balancing", ()),
    ("s3", "Amazon S3", ("s3",)),
    ("cloudtrail", "AWS CloudTrail", ("cloudtrail", "cloud trail")),
    ("azure", "Microsoft Azure", ("azure",)),
    ("vercel", "Vercel", ()),
    ("supabase", "Supabase", ()),
    ("helm", "Helm", ()),
    ("argocd", "Argo CD", ("argocd", "argo cd")),
    ("kubernetes", "Kubernetes", ("k8s", "kube")),
    ("gcp", "Google Cloud", ("gcp", "gcloud")),
    ("postgresql", "PostgreSQL", ("postgres", "psql")),
    ("rds", "Amazon RDS", ("rds",)),
    ("azure_sql", "Azure SQL", ()),
    ("mysql", "MySQL", ()),
    ("mariadb", "MariaDB", ()),
    ("mongodb", "MongoDB", ("mongo",)),
    ("mongodb_atlas", "MongoDB Atlas", ()),
    ("redis", "Redis", ()),
    ("clickhouse", "ClickHouse", ()),
    ("snowflake", "Snowflake", ()),
    ("elasticsearch", "Elasticsearch", ("elastic",)),
    ("opensearch", "OpenSearch", ()),
    ("slack", "Slack", ()),
    ("discord", "Discord", ()),
    ("telegram", "Telegram", ()),
    ("whatsapp", "WhatsApp", ()),
    ("twilio", "Twilio", ()),
    ("smtp", "Email (SMTP)", ("smtp", "email")),
    ("github", "GitHub", ("gh",)),
    ("gitlab", "GitLab", ()),
    ("bitbucket", "Bitbucket", ()),
    ("git", "Git", ()),
    ("jenkins", "Jenkins", ()),
    ("jira", "Jira", ()),
    ("linear", "Linear", ()),
    ("trello", "Trello", ()),
    ("notion", "Notion", ()),
    ("google_docs", "Google Docs", ("gdocs",)),
    ("google_drive", "Google Drive", ("gdrive",)),
    ("google_sheets", "Google Sheets", ("gsheets",)),
    ("google_calendar", "Google Calendar", ("gcal",)),
    ("gmail", "Gmail", ()),
    ("airflow", "Apache Airflow", ("airflow",)),
    ("dagster", "Dagster", ()),
    ("prefect", "Prefect", ()),
    ("temporal", "Temporal", ()),
    ("kafka", "Apache Kafka", ("kafka",)),
    ("rabbitmq", "RabbitMQ", ()),
    ("spark", "Apache Spark", ("spark",)),
    ("ecs", "Amazon ECS", ("ecs",)),
    ("railway", "Railway", ()),
    ("mcp", "MCP", ()),
    ("acp", "ACP", ()),
    ("openclaw", "OpenClaw", ()),
)


@dataclass(frozen=True, slots=True)
class CatalogIntegration:
    id: str
    name: str
    phrases: tuple[str, ...]


def _phrases(integration_id: str, name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    raw = {
        integration_id.replace("_", " "),
        integration_id.replace("_", "-"),
        name.lower(),
        *(alias.lower() for alias in aliases),
    }
    cleaned = {phrase.strip() for phrase in raw if phrase.strip()}
    return tuple(sorted(cleaned, key=len, reverse=True))


_INTEGRATIONS: tuple[CatalogIntegration, ...] = tuple(
    CatalogIntegration(integration_id, name, _phrases(integration_id, name, aliases))
    for integration_id, name, aliases in _CATALOG
)
_BY_ID = {integration.id: integration for integration in _INTEGRATIONS}


def integration_display_name(service: str) -> str:
    """Catalog name for ``service``, or the id itself when it has no page."""
    integration_id = service.strip().lower().replace("-", "_")
    known = _BY_ID.get(integration_id)
    if known is not None:
        return known.name
    return service.strip() or "That integration"


def integration_page_url(service: str) -> str | None:
    """Webapp page for ``service``, or None when this process has no webapp."""
    origin = webapp_origin()
    if origin is None:
        return None
    integration_id = service.strip().lower().replace("-", "_")
    known = _BY_ID.get(integration_id)
    slug = (known.id if known is not None else integration_id).replace("_", "-")
    if not slug:
        return f"{origin}/integrations"
    return f"{origin}/integrations/{slug}"


def headless_setup_message(service: str) -> str:
    """What Slack (and other chat) should say instead of a server setup command.

    The interactive shell runs the setup steps itself. Chat cannot, so the
    reply points at the Configure button and the same page in the app.
    """
    name = integration_display_name(service)
    url = integration_page_url(service)
    if url:
        return (
            f"Connect {name} in the OpenSRE app. "
            f"Use the Configure {name} button under this reply, or open {url}. "
            "In the interactive shell, the setup steps run there instead."
        )
    return (
        f"Connect {name} from the integrations page in the OpenSRE app, "
        "or walk through the setup steps in the interactive shell."
    )


def webapp_origin() -> str | None:
    """https origin of the OpenSRE app, or None when this process has no webapp."""
    raw = (os.getenv(WEBAPP_URL_ENV) or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{parsed.hostname}{port}"


catalog_integrations = _INTEGRATIONS
catalog_by_id = _BY_ID
