"""Built-in alert-source → tool-source routing and keyword-alias catalog.

Core (``core/domain/alerts/alert_source.py``) owns only the routing/alias
registries and the resolver functions; this module holds the actual table —
which alert source routes to which tool sources for planning/seeding, and
which keywords alias to which tool source for deterministic relevance
matching. Registered once, at startup, from
``integrations/harness_adapters.py`` via :func:`register_all_alert_source_routing`.

This is intentionally one catalog module for every alert source rather than
one file per vendor: the table is small, cross-referenced (e.g. EKS routing
mentions ``cloudtrail``, DB aliases share :data:`DB_KEYWORDS`), and easiest to
audit as a single list.
"""

from __future__ import annotations

from core.domain.alerts.alert_source import (
    DB_KEYWORDS,
    AlertSourceRouting,
    register_alert_source_routing,
    register_source_aliases,
    routing,
)

# Single table — relevance and seed lists are intentionally different per entry.
_ROUTING_TABLE: dict[str, AlertSourceRouting] = {
    "grafana": routing(("grafana",), ("grafana",)),
    "datadog": routing(("datadog",), ("datadog",)),
    # ec2/rds/cloudtrail stay relevance-only — context-dependent pre-LLM seeding.
    "cloudwatch": routing(("cloudwatch", "ec2", "rds", "cloudtrail"), ("cloudwatch",)),
    # ec2/cloudtrail stay relevance-only — seed eks + kubernetes.
    "eks": routing(("eks", "ec2", "cloudtrail", "kubernetes"), ("eks", "kubernetes")),
    # eks/cloudtrail stay relevance-only — seed grafana + cloudwatch dashboards/logs.
    "alertmanager": routing(
        ("eks", "cloudwatch", "grafana", "cloudtrail", "kubernetes"),
        ("grafana", "cloudwatch"),
    ),
    "kubernetes": routing(("kubernetes",), ("kubernetes",)),
    "sentry": routing(("sentry",), ("sentry",)),
    "honeycomb": routing(("honeycomb",), ("honeycomb",)),
    "coralogix": routing(("coralogix",), ("coralogix",)),
    # tracer_web stays relevance-only — secondary web context, not pre-LLM seed.
    "airflow": routing(("airflow", "tracer_web"), ("airflow",)),
    "kafka": routing(("kafka",), ("kafka",)),
    "postgresql": routing(("postgresql",), ("postgresql",)),
    "mysql": routing(("mysql",), ("mysql",)),
    "mariadb": routing(("mariadb",), ("mariadb",)),
    "mongodb": routing(("mongodb", "mongodb_atlas"), ("mongodb", "mongodb_atlas")),
    "redis": routing(("redis",), ("redis",)),
    "snowflake": routing(("snowflake",), ("snowflake",)),
    "clickhouse": routing(("clickhouse",), ("clickhouse",)),
    "dagster": routing(("dagster",), ("dagster",)),
    "rabbitmq": routing(("rabbitmq",), ("rabbitmq",)),
    "supabase": routing(("supabase",), ("supabase",)),
    "opensearch": routing(("opensearch",), ("opensearch",)),
    "openobserve": routing(("openobserve",), ("openobserve",)),
    "betterstack": routing(("betterstack",), ("betterstack",)),
    "azure": routing(("azure", "azure_sql"), ("azure", "azure_sql")),
    "github": routing(("github",), ("github",)),
    "gitlab": routing(("gitlab",), ("gitlab",)),
    "bitbucket": routing(("bitbucket",), ("bitbucket",)),
    # ArgoCD deploys to any Kubernetes cluster, not just EKS — seed both.
    "argocd": routing(("eks", "kubernetes"), ("eks", "kubernetes")),
    "splunk": routing(("splunk",), ("splunk",)),
    "signoz": routing(("signoz",), ("signoz",)),
    "jenkins": routing(("jenkins",), ("jenkins",)),
    "tempo": routing(("tempo",), ("tempo",)),
    "temporal": routing(("temporal",), ("temporal",)),
    "new_relic": routing(("new_relic",), ("new_relic",)),
}

_ALIASES_TABLE: dict[str, tuple[str, ...]] = {
    "datadog": ("datadog", "datadoghq", "dd monitor"),
    "sentry": ("sentry", "exception", "stack trace", "stacktrace", "error tracking"),
    "vercel": ("vercel", "deploy", "deployment", "build failed"),
    "github": ("github", "commit", "pull request", "merge"),
    "gitlab": ("gitlab", "merge request"),
    "grafana": ("grafana", "loki", "mimir", "prometheus"),
    "honeycomb": ("honeycomb", "span", "trace latency"),
    "coralogix": ("coralogix",),
    "splunk": ("splunk",),
    "cloudwatch": ("cloudwatch", "lambda", "log group"),
    # Generic k8s/pod/kubectl terms live under "kubernetes" below — keeping them
    # here too would make eks look "relevant" for any Kubernetes alert even
    # when no EKS cluster is configured.
    "eks": ("eks",),
    "kubernetes": (
        "kubernetes",
        "k8s",
        "kubectl",
        "pod",
        "crashloopbackoff",
        "oomkilled",
        "kubepod",
    ),
    "ec2": ("ec2", "instance"),
    "rds": ("rds", "aurora", *DB_KEYWORDS),
    "postgresql": ("postgres", "postgresql", "psql", *DB_KEYWORDS),
    "mysql": ("mysql", *DB_KEYWORDS),
    "mariadb": ("mariadb", *DB_KEYWORDS),
    "mongodb": ("mongodb", "mongo", *DB_KEYWORDS),
    "redis": ("redis", "cache"),
    "snowflake": ("snowflake",),
    "clickhouse": ("clickhouse",),
    "dagster": ("dagster",),
    "airflow": ("airflow", "dag"),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbitmq", "amqp"),
    "supabase": ("supabase",),
    "opensearch": ("opensearch", "elasticsearch"),
    "openobserve": ("openobserve",),
    "betterstack": ("betterstack", "better stack"),
    "azure": ("azure",),
    "signoz": ("signoz",),
    "jenkins": ("jenkins",),
    "tempo": ("tempo",),
    "temporal": ("temporal", "temporal workflow", "task queue"),
    "new_relic": ("new relic", "newrelic", "nrql", "nr alert"),
}


def register_all_alert_source_routing() -> None:
    """Register every built-in alert-source routing entry and keyword-alias set."""
    for source, entry in _ROUTING_TABLE.items():
        register_alert_source_routing(source, entry)
    for source, aliases in _ALIASES_TABLE.items():
        register_source_aliases(source, aliases)


__all__ = ["register_all_alert_source_routing"]
