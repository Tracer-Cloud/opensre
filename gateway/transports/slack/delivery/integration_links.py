"""Link buttons that open an integration's page in the OpenSRE app.

A Slack reply cannot run ``/integrations setup``. When someone asks to connect
an integration, or the answer says one is missing, the reply carries a button
that opens that integration on the webapp (``/integrations/{slug}``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
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

_MAX_BUTTONS = 5
_BUTTON_TEXT_MAX = 75

_MENTION = re.compile(r"<[@!][^>]+>")
_SETUP_VERB = re.compile(
    r"\b(?:configure|config|connect|set[\s-]?up|setup|install|integrate|hook[\s-]?up|enable)\b",
    re.IGNORECASE,
)
_ADD_TARGET = re.compile(r"\badd\s+(?:the\s+|my\s+|our\s+)?(.{0,40})", re.IGNORECASE)
_ASKS_FOR_INTEGRATION = re.compile(r"\bintegrations?\b", re.IGNORECASE)
_MISSING_CLAUSE = re.compile(
    r"(?:isn['’]t|is not|aren['’]t|are not|not yet)\s+(?:connected|configured|set up)",
    re.IGNORECASE,
)
_CONNECT_ASK = re.compile(
    r"\b(?:connect|configure|set[\s-]?up|setup)\s+(?:the\s+|your\s+|a\s+)?([^\n.]{0,48})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Integration:
    id: str
    name: str
    phrases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SetupLink:
    """One Configure button: label, https URL, and a stable action id."""

    label: str
    url: str
    action_id: str


def _phrases(integration_id: str, name: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    raw = {
        integration_id.replace("_", " "),
        integration_id.replace("_", "-"),
        name.lower(),
        *(alias.lower() for alias in aliases),
    }
    cleaned = {phrase.strip() for phrase in raw if phrase.strip()}
    return tuple(sorted(cleaned, key=len, reverse=True))


_INTEGRATIONS: tuple[_Integration, ...] = tuple(
    _Integration(integration_id, name, _phrases(integration_id, name, aliases))
    for integration_id, name, aliases in _CATALOG
)
_BY_ID = {integration.id: integration for integration in _INTEGRATIONS}


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


def integration_setup_links(
    user_text: str,
    assistant_text: str,
    *,
    configured: set[str] | None = None,
) -> list[SetupLink]:
    """Buttons for integrations this reply should send the user to configure.

    A direct ask ("configure Linear") always links that integration. An answer
    that says one is not connected links it too, unless it is already
    configured. With no named integration, a general "connect an integration"
    ask links the catalog.
    """
    origin = webapp_origin()
    if origin is None:
        return []
    connected = {item.strip().lower() for item in (configured or set()) if item.strip()}
    user = _plain(user_text)
    assistant = _plain(assistant_text)
    ids: list[str] = []
    if _SETUP_VERB.search(user):
        ids.extend(_ids_in(user))
    ids.extend(_added_integration(user))
    ids.extend(_ids_missing(assistant, connected))
    links = [_link(origin, integration_id) for integration_id in _unique(ids)]
    if not links and _SETUP_VERB.search(user) and _ASKS_FOR_INTEGRATION.search(user):
        links.append(
            SetupLink(
                label="Open integrations",
                url=f"{origin}/integrations",
                action_id="configure_integration_catalog",
            )
        )
    return links[:_MAX_BUTTONS]


def setup_actions_block(links: Sequence[SetupLink]) -> dict[str, Any] | None:
    """A Slack ``actions`` block of link buttons, or None when there is nothing to open."""
    if not links:
        return None
    return {
        "type": "actions",
        "block_id": "opensre_configure_integration",
        "elements": [_button(link) for link in links[:_MAX_BUTTONS]],
    }


def configured_service_ids(session: object) -> set[str]:
    """Service ids the session already hydrated from the integrations store."""
    state = getattr(session, "integrations", None)
    names = getattr(state, "configured", ()) or ()
    return {str(name).strip().lower() for name in names if str(name).strip()}


def _link(origin: str, integration_id: str) -> SetupLink:
    integration = _BY_ID[integration_id]
    slug = integration_id.replace("_", "-")
    return SetupLink(
        label=f"Configure {integration.name}",
        url=f"{origin}/integrations/{slug}",
        action_id=f"configure_integration_{integration_id}",
    )


def _button(link: SetupLink) -> dict[str, Any]:
    text = link.label if len(link.label) <= _BUTTON_TEXT_MAX else link.label[:_BUTTON_TEXT_MAX]
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "url": link.url,
        "action_id": link.action_id,
    }


def _plain(text: str) -> str:
    return _MENTION.sub(" ", text or "")


def _unique(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for integration_id in ids:
        if integration_id in seen or integration_id not in _BY_ID:
            continue
        seen.add(integration_id)
        ordered.append(integration_id)
    return ordered


def _added_integration(text: str) -> list[str]:
    """The integration in \"add Linear\", not a later mention in \"add a note about Linear\"."""
    match = _ADD_TARGET.search(text)
    if match is None:
        return []
    tail = match.group(1).lstrip().lower()
    for integration_id in _ids_in(tail):
        if any(tail.startswith(phrase) for phrase in _BY_ID[integration_id].phrases):
            return [integration_id]
    return []


def _ids_in(text: str) -> list[str]:
    """Catalog ids named in ``text``, longest phrase winning when they overlap."""
    lowered = text.lower()
    hits: list[tuple[int, int, str]] = []
    for integration in _INTEGRATIONS:
        for phrase in integration.phrases:
            for match in re.finditer(
                rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
                lowered,
            ):
                hits.append((match.start(), match.end(), integration.id))
    hits.sort(key=lambda hit: hit[1] - hit[0], reverse=True)
    kept: list[tuple[int, int, str]] = []
    for start, end, integration_id in hits:
        if any(start >= kept_start and end <= kept_end for kept_start, kept_end, _ in kept):
            continue
        kept.append((start, end, integration_id))
    kept.sort(key=lambda hit: hit[0])
    return _unique([integration_id for _, _, integration_id in kept])


_SETUP_FILLER = frozenset(
    {
        "the",
        "your",
        "a",
        "an",
        "my",
        "our",
        "integration",
        "integrations",
        "now",
        "please",
        "or",
        "and",
    }
)


def _ids_missing(text: str, configured: set[str]) -> list[str]:
    """Integrations the answer says are not connected, skipping ones already configured."""
    found: list[str] = []
    for match in _MISSING_CLAUSE.finditer(text):
        window_start = max(0, match.start() - 48)
        window = text[window_start : match.start()]
        for separator in (". ", "! ", "? ", "\n"):
            split_at = window.rfind(separator)
            if split_at != -1:
                window = window[split_at + len(separator) :]
        found.extend(
            integration_id for integration_id in _ids_in(window) if integration_id not in configured
        )
    for match in _CONNECT_ASK.finditer(text):
        tail = match.group(1)
        if not _tail_names_only_integrations(tail):
            continue
        found.extend(
            integration_id for integration_id in _ids_in(tail) if integration_id not in configured
        )
    return found


def _tail_names_only_integrations(tail: str) -> bool:
    """True when ``tail`` is just integration names ("Linear", "Linear or Discord")."""
    named = _ids_in(tail)
    if not named:
        return False
    lowered = tail.lower()
    for integration_id in named:
        for phrase in _BY_ID[integration_id].phrases:
            lowered = re.sub(
                rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
                " ",
                lowered,
            )
    return all(word in _SETUP_FILLER for word in re.findall(r"[a-z0-9]+", lowered))
