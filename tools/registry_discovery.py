"""Tool discovery — walk tool packages and extract RegisteredTool objects.

The canonical ``tools/`` tree and each per-vendor ``integrations/<vendor>/tools``
package are walked here; the registry facade (:mod:`tools.registry`) caches the
result. Kept separate so both the facade and the static descriptor index
(:mod:`tools.registry_index`) share ``INTEGRATION_TOOL_PACKAGES`` without a
circular import.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import replace
from types import ModuleType

from core.tool import REGISTERED_TOOL_ATTR, BaseTool, RegisteredTool

logger = logging.getLogger(__name__)

# Per-vendor tool packages — when a vendor consolidates its tool code under
# ``integrations/<vendor>/tools/``, list the dotted package path here so the
# registry walks it alongside the canonical ``tools/`` package. New vendors get
# one entry each as they migrate.
INTEGRATION_TOOL_PACKAGES: tuple[str, ...] = (
    "integrations.alertmanager.tools",
    "integrations.argocd.tools",
    "integrations.aws.tools",
    "integrations.aws_lambda.tools",
    "integrations.azure.tools",
    "integrations.azure_sql.tools",
    "integrations.betterstack.tools",
    "integrations.bitbucket.tools",
    "integrations.buzz.tools",
    "integrations.clickhouse.tools",
    "integrations.cloudtrail.tools",
    "integrations.cloudwatch.tools",
    "integrations.coralogix.tools",
    "integrations.dagster.tools",
    "integrations.datadog.tools",
    "integrations.ec2.tools",
    "integrations.eks.tools",
    "integrations.elasticsearch.tools",
    "integrations.elb.tools",
    "integrations.github.tools",
    "integrations.gitlab.tools",
    "integrations.google_docs.tools",
    "integrations.grafana.tools",
    "integrations.groundcover.tools",
    "integrations.helm.tools",
    "integrations.honeycomb.tools",
    "integrations.hosted_gateway.tools",
    "integrations.incident_io.tools",
    "integrations.jenkins.tools",
    "integrations.jira.tools",
    "integrations.kafka.tools",
    "integrations.kubernetes.tools",
    "integrations.mariadb.tools",
    "integrations.mongodb.tools",
    "integrations.mongodb_atlas.tools",
    "integrations.mysql.tools",
    "integrations.new_relic.tools",
    "integrations.openobserve.tools",
    "integrations.opensearch.tools",
    "integrations.opsgenie.tools",
    "integrations.pagerduty.tools",
    "integrations.pi.tools",
    "integrations.posthog_mcp.tools",
    "integrations.postgresql.tools",
    "integrations.prefect.tools",
    "integrations.rabbitmq.tools",
    "integrations.rds.tools",
    "integrations.redis.tools",
    "integrations.railway.tools",
    "integrations.rocketchat.tools",
    "integrations.s3.tools",
    "integrations.sentry.tools",
    "integrations.sentry_mcp.tools",
    "integrations.signoz.tools",
    "integrations.slack.tools",
    "integrations.snowflake.tools",
    "integrations.splunk.tools",
    "integrations.sqs.tools",
    "integrations.supabase.tools",
    "integrations.telegram.tools",
    "integrations.tempo.tools",
    "integrations.temporal.tools",
    "integrations.tracer.tools",
    "integrations.twilio.tools",
    "integrations.vercel.tools",
    "integrations.victoria_logs.tools",
    "integrations.x_mcp.tools",
    "integrations.yandex_cloud.tools",
)

_SKIP_MODULE_NAMES = {
    "__pycache__",
    "registry",
}
_TOOL_MODULES_ATTR = "TOOL_MODULES"


def _iter_tool_module_names(package: ModuleType) -> list[str]:
    module_names: list[str] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in _SKIP_MODULE_NAMES:
            continue
        if module_info.name.startswith("_") or module_info.name.endswith("_test"):
            continue
        module_names.append(module_info.name)
    return sorted(module_names)


def _import_tool_module(package: ModuleType, module_name: str) -> ModuleType:
    return importlib.import_module(_qualify_tool_module_name(package, module_name))


def _qualify_tool_module_name(package: ModuleType, module_name: str) -> str:
    if module_name == package.__name__ or module_name.startswith(f"{package.__name__}."):
        return module_name
    return f"{package.__name__}.{module_name}"


def _iter_manifest_tool_module_names(module: ModuleType) -> tuple[str, ...]:
    manifest = getattr(module, _TOOL_MODULES_ATTR, ())
    if manifest is None:
        return ()
    if isinstance(manifest, str):
        logger.warning(
            "[tools] Ignoring %s.%s because it must be an iterable of module names, not a string",
            module.__name__,
            _TOOL_MODULES_ATTR,
        )
        return ()

    try:
        module_names = tuple(manifest)
    except TypeError:
        logger.warning(
            "[tools] Ignoring %s.%s because it is not iterable",
            module.__name__,
            _TOOL_MODULES_ATTR,
        )
        return ()

    valid_module_names: list[str] = []
    for module_name in module_names:
        if not isinstance(module_name, str) or not module_name:
            logger.warning(
                "[tools] Ignoring invalid %s entry on %s: %r",
                _TOOL_MODULES_ATTR,
                module.__name__,
                module_name,
            )
            continue
        valid_module_names.append(module_name)
    return tuple(valid_module_names)


def _import_tool_module_or_none(package: ModuleType, module_name: str) -> ModuleType | None:
    full_module_name = _qualify_tool_module_name(package, module_name)
    try:
        return _import_tool_module(package, module_name)
    except ModuleNotFoundError as exc:
        logger.warning("[tools] Skipping %s: %s", full_module_name, exc)
        return None
    except Exception as exc:
        logger.warning(
            "[tools] Skipping %s due to import failure: %s",
            full_module_name,
            exc,
            exc_info=True,
        )
        return None


def iter_discovered_tool_modules(package: ModuleType) -> list[ModuleType]:
    modules: list[ModuleType] = []
    for module_name in _iter_tool_module_names(package):
        module = _import_tool_module_or_none(package, module_name)
        if module is None:
            continue
        modules.append(module)

        for manifest_module_name in _iter_manifest_tool_module_names(module):
            manifest_module = _import_tool_module_or_none(module, manifest_module_name)
            if manifest_module is not None:
                modules.append(manifest_module)

    return modules


def _candidate_belongs_to_module(candidate: object, module_name: str) -> bool:
    if isinstance(candidate, RegisteredTool):
        return (candidate.origin_module or getattr(candidate.run, "__module__", "")) == module_name
    if isinstance(candidate, BaseTool):
        return candidate.__class__.__module__ == module_name
    return getattr(candidate, "__module__", None) == module_name


def _registered_tool_from_candidate(candidate: object) -> RegisteredTool | None:
    if isinstance(candidate, RegisteredTool):
        if not candidate.origin_module or not candidate.origin_name:
            return replace(
                candidate,
                origin_module=candidate.origin_module or getattr(candidate.run, "__module__", ""),
                origin_name=candidate.origin_name or getattr(candidate.run, "__name__", ""),
            )
        return candidate

    registered = getattr(candidate, REGISTERED_TOOL_ATTR, None)
    if isinstance(registered, RegisteredTool):
        return registered

    if isinstance(candidate, BaseTool):
        return RegisteredTool.from_base_tool(candidate)

    return None


def collect_registered_tools_from_module(module: ModuleType) -> list[RegisteredTool]:
    tools_by_name: dict[str, RegisteredTool] = {}
    seen_candidate_ids: set[int] = set()

    for _, candidate in inspect.getmembers(module):
        if not _candidate_belongs_to_module(candidate, module.__name__):
            continue
        candidate_id = id(candidate)
        if candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate_id)
        registered = _registered_tool_from_candidate(candidate)
        if registered is None:
            continue
        if registered.name in tools_by_name:
            logger.warning(
                "[tools] Duplicate tool name '%s' in module %s; keeping first definition",
                registered.name,
                module.__name__,
            )
            continue
        tools_by_name[registered.name] = registered

    return sorted(tools_by_name.values(), key=lambda tool: tool.name)
