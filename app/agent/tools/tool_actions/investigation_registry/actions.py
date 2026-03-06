"""Registry of all available investigation actions."""

from app.agent.tools.tool_actions.investigation_registry.action_builder import build_action
from app.agent.tools.tool_actions.investigation_registry.models import InvestigationAction


def get_available_actions() -> list[InvestigationAction]:
    """Get all available investigation actions with rich metadata."""
    from app.agent.tools.tool_actions.aws.aws_sdk_actions import execute_aws_operation
    from app.agent.tools.tool_actions.aws.cloudwatch_actions import get_cloudwatch_logs
    from app.agent.tools.tool_actions.aws.lambda_actions import (
        get_lambda_configuration,
        get_lambda_errors,
        get_lambda_invocation_logs,
        inspect_lambda_function,
    )
    from app.agent.tools.tool_actions.aws.s3_actions import (
        check_s3_marker,
        get_s3_object,
        inspect_s3_object,
        list_s3_objects,
    )
    from app.agent.tools.tool_actions.datadog.datadog_actions import (
        query_datadog_events,
        query_datadog_logs,
        query_datadog_monitors,
    )
    from app.agent.tools.tool_actions.datadog.datadog_investigate import fetch_datadog_context
    from app.agent.tools.tool_actions.grafana.grafana_actions import (
        query_grafana_alert_rules,
        query_grafana_logs,
        query_grafana_metrics,
        query_grafana_service_names,
        query_grafana_traces,
    )
    from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_actions import (
        get_sre_guidance,
    )
    from app.agent.tools.tool_actions.tracer.tracer_jobs import (
        get_failed_jobs,
        get_failed_tools,
    )
    from app.agent.tools.tool_actions.tracer.tracer_logs import get_error_logs
    from app.agent.tools.tool_actions.tracer.tracer_metrics import get_host_metrics

    return [
        # Tracer actions
        build_action(
            name="get_failed_jobs",
            func=get_failed_jobs,
            source="batch",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id")
            },
        ),
        build_action(
            name="get_failed_tools",
            func=get_failed_tools,
            source="tracer_web",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id")
            },
        ),
        build_action(
            name="get_error_logs",
            func=get_error_logs,
            source="tracer_web",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id"),
                "size": 500,
                "error_only": True,
            },
        ),
        build_action(
            name="get_host_metrics",
            func=get_host_metrics,
            source="cloudwatch",
            requires=["trace_id"],
            availability_check=lambda sources: bool(sources.get("tracer_web", {}).get("trace_id")),
            parameter_extractor=lambda sources: {
                "trace_id": sources.get("tracer_web", {}).get("trace_id")
            },
        ),
        # CloudWatch actions
        build_action(
            name="get_cloudwatch_logs",
            func=get_cloudwatch_logs,
            source="cloudwatch",
            requires=[],
            availability_check=lambda sources: bool(sources.get("cloudwatch", {}).get("log_group")),
            parameter_extractor=lambda sources: {
                "log_group": sources.get("cloudwatch", {}).get("log_group"),
                "log_stream": sources.get("cloudwatch", {}).get("log_stream"),
                "filter_pattern": sources.get("cloudwatch", {}).get("correlation_id"),
                "limit": 100,
            },
        ),
        # S3 actions
        build_action(
            name="check_s3_marker",
            func=check_s3_marker,
            source="storage",
            requires=[],
            availability_check=lambda sources: bool(
                (sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("prefix"))
                or sources.get("s3_processed", {}).get("bucket")
            ),
            parameter_extractor=lambda sources: (
                {
                    "bucket": sources.get("s3_processed", {}).get("bucket"),
                    "prefix": sources.get("s3_processed", {}).get("prefix", ""),
                }
                if sources.get("s3_processed")
                else {
                    "bucket": sources.get("s3", {}).get("bucket"),
                    "prefix": sources.get("s3", {}).get("prefix"),
                }
            ),
        ),
        build_action(
            name="inspect_s3_object",
            func=inspect_s3_object,
            source="storage",
            requires=["bucket", "key"],
            availability_check=lambda sources: bool(
                sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key")
            ),
            parameter_extractor=lambda sources: {
                "bucket": sources.get("s3", {}).get("bucket"),
                "key": sources.get("s3", {}).get("key"),
            },
        ),
        build_action(
            name="list_s3_objects",
            func=list_s3_objects,
            source="storage",
            requires=["bucket"],
            availability_check=lambda sources: bool(sources.get("s3", {}).get("bucket")),
            parameter_extractor=lambda sources: {
                "bucket": sources.get("s3", {}).get("bucket"),
                "prefix": sources.get("s3", {}).get("prefix", ""),
                "max_keys": 100,
            },
        ),
        build_action(
            name="get_s3_object",
            func=get_s3_object,
            source="storage",
            requires=["bucket", "key"],
            availability_check=lambda sources: bool(
                (sources.get("s3", {}).get("bucket") and sources.get("s3", {}).get("key"))
                or (
                    sources.get("s3_audit", {}).get("bucket")
                    and sources.get("s3_audit", {}).get("key")
                )
            ),
            parameter_extractor=lambda sources: (
                {
                    "bucket": sources.get("s3_audit", {}).get("bucket"),
                    "key": sources.get("s3_audit", {}).get("key"),
                }
                if sources.get("s3_audit")
                else {
                    "bucket": sources.get("s3", {}).get("bucket"),
                    "key": sources.get("s3", {}).get("key"),
                }
            ),
        ),
        # Lambda actions
        build_action(
            name="get_lambda_invocation_logs",
            func=get_lambda_invocation_logs,
            source="cloudwatch",
            requires=["function_name"],
            availability_check=lambda sources: bool(sources.get("lambda", {}).get("function_name")),
            parameter_extractor=lambda sources: {
                "function_name": sources.get("lambda", {}).get("function_name"),
                "filter_errors": False,
                "limit": 50,
            },
        ),
        build_action(
            name="get_lambda_errors",
            func=get_lambda_errors,
            source="cloudwatch",
            requires=["function_name"],
            availability_check=lambda sources: bool(sources.get("lambda", {}).get("function_name")),
            parameter_extractor=lambda sources: {
                "function_name": sources.get("lambda", {}).get("function_name"),
                "limit": 50,
            },
        ),
        build_action(
            name="inspect_lambda_function",
            func=inspect_lambda_function,
            source="cloudwatch",
            requires=["function_name"],
            availability_check=lambda sources: bool(sources.get("lambda", {}).get("function_name")),
            parameter_extractor=lambda sources: {
                "function_name": sources.get("lambda", {}).get("function_name"),
                "include_code": True,
            },
        ),
        build_action(
            name="get_lambda_configuration",
            func=get_lambda_configuration,
            source="cloudwatch",
            requires=["function_name"],
            availability_check=lambda sources: bool(sources.get("lambda", {}).get("function_name")),
            parameter_extractor=lambda sources: {
                "function_name": sources.get("lambda", {}).get("function_name"),
            },
        ),
        # AWS SDK action
        build_action(
            name="execute_aws_operation",
            func=execute_aws_operation,
            source="aws_sdk",
            requires=["service", "operation"],
            availability_check=lambda sources: bool(sources.get("aws_metadata")),
            parameter_extractor=None,
        ),
        # Knowledge action
        build_action(
            name="get_sre_guidance",
            func=get_sre_guidance,
            source="knowledge",
            requires=[],
            availability_check=lambda _sources: True,
            parameter_extractor=lambda sources: {
                "keywords": sources.get("problem_keywords", []),
            },
        ),
        # Grafana actions
        build_action(
            name="query_grafana_logs",
            func=query_grafana_logs,
            source="grafana",
            requires=["service_name"],
            availability_check=lambda sources: bool(
                sources.get("grafana", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "service_name": sources["grafana"]["service_name"],
                "execution_run_id": sources["grafana"].get("execution_run_id"),
                "time_range_minutes": sources["grafana"].get("time_range_minutes", 60),
                "limit": 100,
                "grafana_endpoint": sources["grafana"].get("grafana_endpoint"),
                "grafana_api_key": sources["grafana"].get("grafana_api_key"),
            },
        ),
        build_action(
            name="query_grafana_traces",
            func=query_grafana_traces,
            source="grafana",
            requires=["service_name"],
            availability_check=lambda sources: bool(
                sources.get("grafana", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "service_name": sources["grafana"]["service_name"],
                "execution_run_id": sources["grafana"].get("execution_run_id"),
                "limit": 20,
                "grafana_endpoint": sources["grafana"].get("grafana_endpoint"),
                "grafana_api_key": sources["grafana"].get("grafana_api_key"),
            },
        ),
        build_action(
            name="query_grafana_metrics",
            func=query_grafana_metrics,
            source="grafana",
            requires=["metric_name"],
            availability_check=lambda sources: bool(
                sources.get("grafana", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "metric_name": "pipeline_runs_total",
                "service_name": sources.get("grafana", {}).get("service_name"),
                "grafana_endpoint": sources.get("grafana", {}).get("grafana_endpoint"),
                "grafana_api_key": sources.get("grafana", {}).get("grafana_api_key"),
            },
        ),
        build_action(
            name="query_grafana_alert_rules",
            func=query_grafana_alert_rules,
            source="grafana",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("grafana", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "folder": sources.get("grafana", {}).get("pipeline_name"),
                "grafana_endpoint": sources.get("grafana", {}).get("grafana_endpoint"),
                "grafana_api_key": sources.get("grafana", {}).get("grafana_api_key"),
            },
        ),
        build_action(
            name="query_grafana_service_names",
            func=query_grafana_service_names,
            source="grafana",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("grafana", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "grafana_endpoint": sources.get("grafana", {}).get("grafana_endpoint"),
                "grafana_api_key": sources.get("grafana", {}).get("grafana_api_key"),
            },
        ),
        # Datadog actions
        build_action(
            name="query_datadog_all",
            func=fetch_datadog_context,
            source="datadog",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("datadog", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "query": sources.get("datadog", {}).get("default_query", ""),
                "time_range_minutes": sources.get("datadog", {}).get("time_range_minutes", 60),
                "limit": 75,
                "monitor_query": sources.get("datadog", {}).get("monitor_query"),
                "kube_namespace": (sources.get("datadog", {}).get("kubernetes_context") or {}).get("namespace"),
                "api_key": sources["datadog"].get("api_key"),
                "app_key": sources["datadog"].get("app_key"),
                "site": sources["datadog"].get("site", "datadoghq.com"),
            },
        ),
        build_action(
            name="query_datadog_logs",
            func=query_datadog_logs,
            source="datadog",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("datadog", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "query": sources.get("datadog", {}).get("default_query", ""),
                "time_range_minutes": sources.get("datadog", {}).get("time_range_minutes", 60),
                "limit": 50,
                "api_key": sources["datadog"].get("api_key"),
                "app_key": sources["datadog"].get("app_key"),
                "site": sources["datadog"].get("site", "datadoghq.com"),
            },
        ),
        build_action(
            name="query_datadog_monitors",
            func=query_datadog_monitors,
            source="datadog",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("datadog", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "query": sources.get("datadog", {}).get("monitor_query"),
                "api_key": sources["datadog"].get("api_key"),
                "app_key": sources["datadog"].get("app_key"),
                "site": sources["datadog"].get("site", "datadoghq.com"),
            },
        ),
        build_action(
            name="query_datadog_events",
            func=query_datadog_events,
            source="datadog",
            requires=[],
            availability_check=lambda sources: bool(
                sources.get("datadog", {}).get("connection_verified")
            ),
            parameter_extractor=lambda sources: {
                "query": sources.get("datadog", {}).get("default_query"),
                "time_range_minutes": sources.get("datadog", {}).get("time_range_minutes", 60),
                "api_key": sources["datadog"].get("api_key"),
                "app_key": sources["datadog"].get("app_key"),
                "site": sources["datadog"].get("site", "datadoghq.com"),
            },
        ),
    ]
