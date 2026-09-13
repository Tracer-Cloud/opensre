"""Path → pytest target mapping for branch-scoped test runs (CI.md §2).

This module is the single source of truth for ``make test-scope``. Edit rules
here only — do not duplicate the mapping table in CI.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from git_changes import is_documentation


@dataclass(frozen=True, slots=True)
class PathRule:
    """Map changed paths under ``path_prefix`` to pytest targets."""

    path_prefix: str
    test_targets: tuple[str, ...]


# Matched in list order — more specific prefixes must appear before parents.
RULES: tuple[PathRule, ...] = (
    # User-facing quickstart surface
    PathRule("docs/quickstart.mdx", ("tests/cli/test_quickstart.py",)),
    # Installer surfaces (curl/bash, PowerShell, docs, Homebrew sync)
    PathRule(
        "install.sh",
        (
            "tests/cli/test_install_matrix.py",
            "tests/cli/test_install_sh_path.py",
            "tests/cli/test_install_sh_resolution.py",
        ),
    ),
    PathRule(
        "install.ps1",
        ("tests/cli/test_install_matrix.py", "tests/cli/test_install_ps1_progress.py"),
    ),
    PathRule("docs/install.mdx", ("tests/cli/test_install_matrix.py",)),
    PathRule("docs/install-local.mdx", ("tests/cli/test_install_matrix.py",)),
    PathRule(
        ".github/scripts/sync-homebrew-tap-formula.sh",
        ("tests/cli/test_install_matrix.py",),
    ),
    # Shared core
    PathRule("core/domain/", ("tests/core/domain/",)),
    PathRule("core/agent_harness/session/", ("tests/core/agent_harness/session/",)),
    PathRule(
        "core/agent_harness/prompts/skills/",
        (
            "core/agent_harness/prompts/skills/",
            "tests/core/agent_harness/prompts/",
            "tests/core/agent/prompts/",
        ),
    ),
    PathRule("core/", ("tests/core/",)),
    PathRule("utils/", ("tests/utils/",)),
    # Specific sub-packages before their parent
    PathRule("integrations/llm_cli/", ("tests/integrations/llm_cli/",)),
    PathRule(
        "integrations/alertmanager/",
        ("tests/integrations/alertmanager/",),
    ),
    PathRule(
        "integrations/dagster/",
        ("tests/integrations/test_dagster.py",),
    ),
    PathRule(
        "integrations/eks/",
        (
            "tests/integrations/eks/",
            "tests/tools/test_eks_deployment_status_tool.py",
            "tests/tools/test_eks_describe_addon_tool.py",
            "tests/tools/test_eks_describe_cluster_tool.py",
            "tests/tools/test_eks_events_tool.py",
            "tests/tools/test_eks_list_clusters_tool.py",
            "tests/tools/test_eks_list_deployments_tool.py",
            "tests/tools/test_eks_list_namespaces_tool.py",
            "tests/tools/test_eks_list_pods_tool.py",
            "tests/tools/test_eks_node_health_tool.py",
            "tests/tools/test_eks_nodegroup_health_tool.py",
            "tests/tools/test_eks_pod_logs_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/elasticsearch/",
        (
            "tests/integrations/elasticsearch/",
            "tests/tools/test_elasticsearch_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/google_docs/",
        (
            "tests/integrations/google_docs/",
            "tests/tools/test_google_docs_create_report_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/groundcover/",
        ("tests/integrations/groundcover/", "tests/tools/test_groundcover_tools.py"),
    ),
    PathRule(
        "integrations/helm/",
        ("tests/integrations/helm/", "tests/tools/test_helm_tools.py"),
    ),
    PathRule(
        "integrations/incident_io/",
        ("tests/integrations/incident_io/", "tests/tools/test_incident_io_tool.py"),
    ),
    PathRule(
        "integrations/jira/",
        (
            "tests/integrations/test_jira_client.py",
            "tests/integrations/test_jira_search_and_factory.py",
            "tests/tools/test_jira_add_comment_tool.py",
            "tests/tools/test_jira_create_issue_tool.py",
            "tests/tools/test_jira_issue_detail_tool.py",
            "tests/tools/test_jira_search_issues_tool.py",
        ),
    ),
    PathRule(
        "integrations/clickhouse/",
        (
            "tests/integrations/test_clickhouse.py",
            "tests/tools/test_clickhouse_query_activity_tool.py",
            "tests/tools/test_clickhouse_system_health_tool.py",
        ),
    ),
    PathRule(
        "integrations/mariadb/",
        (
            "tests/integrations/test_mariadb_integration.py",
            "tests/tools/test_mariadb_innodb_status_tool.py",
            "tests/tools/test_mariadb_process_list_tool.py",
            "tests/tools/test_mariadb_replication_tool.py",
            "tests/tools/test_mariadb_slow_queries_tool.py",
            "tests/tools/test_mariadb_status_tool.py",
        ),
    ),
    PathRule(
        "integrations/mongodb_atlas/",
        (
            "tests/integrations/test_mongodb_atlas_integration.py",
            "tests/tools/test_mongodb_atlas_alerts_tool.py",
            "tests/tools/test_mongodb_atlas_clusters_tool.py",
            "tests/tools/test_mongodb_atlas_events_tool.py",
            "tests/tools/test_mongodb_atlas_metrics_tool.py",
            "tests/tools/test_mongodb_atlas_performance_advisor_tool.py",
        ),
    ),
    PathRule(
        "integrations/mongodb/",
        (
            "tests/integrations/test_mongodb_integration.py",
            "tests/tools/test_mongodb_collection_stats_tool.py",
            "tests/tools/test_mongodb_current_ops_tool.py",
            "tests/tools/test_mongodb_profiler_tool.py",
            "tests/tools/test_mongodb_replica_status_tool.py",
            "tests/tools/test_mongodb_server_status_tool.py",
        ),
    ),
    PathRule(
        "integrations/mysql/",
        (
            "tests/integrations/test_mysql.py",
            "tests/tools/test_mysql_current_processes_tool.py",
            "tests/tools/test_mysql_replication_status_tool.py",
            "tests/tools/test_mysql_server_status_tool.py",
            "tests/tools/test_mysql_slow_queries_tool.py",
            "tests/tools/test_mysql_table_stats_tool.py",
        ),
    ),
    PathRule(
        "integrations/postgresql/",
        (
            "tests/integrations/test_postgresql.py",
            "tests/tools/test_postgresql_current_queries_tool.py",
            "tests/tools/test_postgresql_locks_tool.py",
            "tests/tools/test_postgresql_replication_status_tool.py",
            "tests/tools/test_postgresql_server_status_tool.py",
            "tests/tools/test_postgresql_slow_queries_tool.py",
            "tests/tools/test_postgresql_table_stats_tool.py",
        ),
    ),
    PathRule(
        "integrations/redis/",
        (
            "tests/integrations/test_redis.py",
            "tests/tools/test_redis_client_list_tool.py",
            "tests/tools/test_redis_key_scan_tool.py",
            "tests/tools/test_redis_latency_doctor_tool.py",
            "tests/tools/test_redis_list_depth_tool.py",
            "tests/tools/test_redis_replication_tool.py",
            "tests/tools/test_redis_server_info_tool.py",
            "tests/tools/test_redis_slowlog_tool.py",
        ),
    ),
    PathRule(
        "integrations/snowflake/",
        (
            "tests/tools/test_snowflake_query_history_evidence.py",
            "tests/tools/test_snowflake_query_history_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/azure/",
        (
            "tests/tools/test_azure_monitor_logs_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/azure_sql/",
        (
            "tests/integrations/test_azure_sql.py",
            "tests/tools/test_azure_sql_current_queries_tool.py",
            "tests/tools/test_azure_sql_resource_stats_tool.py",
            "tests/tools/test_azure_sql_server_status_tool.py",
            "tests/tools/test_azure_sql_slow_queries_tool.py",
            "tests/tools/test_azure_sql_wait_stats_tool.py",
        ),
    ),
    PathRule(
        "integrations/betterstack/",
        (
            "tests/integrations/test_betterstack.py",
            "tests/tools/test_betterstack_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/kafka/",
        (
            "tests/integrations/test_kafka.py",
            "tests/tools/test_kafka_consumer_group_tool.py",
            "tests/tools/test_kafka_topic_health_tool.py",
        ),
    ),
    PathRule(
        "integrations/openobserve/",
        (
            "tests/tools/test_openobserve_logs_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/opensearch/",
        (
            "tests/integrations/test_opensearch_catalog.py",
            "tests/tools/test_opensearch_analytics_tool.py",
        ),
    ),
    PathRule(
        "integrations/posthog_mcp/",
        (
            "tests/integrations/test_posthog_mcp.py",
            "tests/tools/test_posthog_mcp_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/rabbitmq/",
        (
            "tests/integrations/test_rabbitmq.py",
            "tests/tools/test_rabbitmq_broker_overview_tool.py",
            "tests/tools/test_rabbitmq_connection_stats_tool.py",
            "tests/tools/test_rabbitmq_consumer_health_tool.py",
            "tests/tools/test_rabbitmq_node_health_tool.py",
            "tests/tools/test_rabbitmq_queue_backlog_tool.py",
        ),
    ),
    PathRule(
        "integrations/sentry_mcp/",
        (
            "tests/integrations/test_sentry_mcp.py",
            "tests/tools/test_sentry_mcp_tool.py",
            "tests/tools/test_telemetry.py",
        ),
    ),
    PathRule(
        "integrations/sentry/",
        (
            "tests/tools/test_sentry_issue_details_tool.py",
            "tests/tools/test_sentry_issue_events_tool.py",
            "tests/tools/test_sentry_search_issues_tool.py",
        ),
    ),
    PathRule(
        "integrations/supabase/",
        ("tests/integrations/test_supabase.py",),
    ),
    PathRule(
        "integrations/bitbucket/",
        (
            "tests/integrations/test_bitbucket.py",
            "tests/tools/test_bitbucket_commits_tool.py",
            "tests/tools/test_bitbucket_file_contents_tool.py",
            "tests/tools/test_bitbucket_search_code_tool.py",
        ),
    ),
    PathRule(
        "integrations/telegram/tools/",
        ("tests/tools/test_telegram_send_message_tool.py",),
    ),
    PathRule(
        "integrations/tracer/tools/",
        (
            "tests/tools/test_tracer_airflow_metrics_tool.py",
            "tests/tools/test_tracer_batch_statistics_tool.py",
            "tests/tools/test_tracer_error_logs_tool.py",
            "tests/tools/test_tracer_failed_jobs_tool.py",
            "tests/tools/test_tracer_failed_run_tool.py",
            "tests/tools/test_tracer_failed_tools_tool.py",
            "tests/tools/test_tracer_host_metrics_tool.py",
            "tests/tools/test_tracer_run_tool.py",
            "tests/tools/test_tracer_tasks_tool.py",
        ),
    ),
    PathRule(
        "integrations/twilio/",
        (
            "tests/integrations/test_twilio.py",
            "tests/tools/test_twilio_notify_tool.py",
        ),
    ),
    PathRule(
        "integrations/github/tools/ci_repair_loop/",
        (
            "tests/integrations/github/test_ci_repair_loop.py",
            "tests/tools/test_ci_repair_loop.py",
        ),
    ),
    PathRule(
        "integrations/github/tools/ci_fix/",
        (
            "tests/tools/test_github_ci_fix.py",
            "tests/tools/test_github_ci_fix_verification.py",
            "tests/tools/test_github_ci_fix_base_merge.py",
            "tests/integrations/github/test_ci_fix_ledger.py",
        ),
    ),
    PathRule(
        "integrations/github/tools/security_fix/", ("tests/tools/test_github_security_fix.py",)
    ),
    PathRule(
        "integrations/github/tools/",
        (
            "tests/tools/test_github_actions_tool.py",
            "tests/tools/test_github_commits_tool.py",
            "tests/tools/test_github_file_contents_tool.py",
            "tests/tools/test_github_helpers.py",
            "tests/tools/test_github_issues_tool.py",
            "tests/tools/test_github_repo_scope.py",
            "tests/tools/test_github_repository_tool.py",
            "tests/tools/test_github_repository_tree_tool.py",
            "tests/tools/test_github_search_code_tool.py",
            "tests/tools/test_github_workflow_tools.py",
        ),
    ),
    PathRule(
        "integrations/gitlab/",
        (
            "tests/integrations/test_gitlab.py",
            "tests/tools/test_gitlab_commits_tool.py",
            "tests/tools/test_gitlab_file_tool.py",
            "tests/tools/test_gitlab_mrs_tool.py",
            "tests/tools/test_gitlab_pipelines_tool.py",
        ),
    ),
    PathRule(
        "integrations/aws/tools/",
        ("tests/tools/test_aws_operation_tool.py",),
    ),
    PathRule(
        "integrations/aws_lambda/",
        (
            "tests/integrations/aws/test_lambda_client.py",
            "tests/tools/test_lambda_config_tool.py",
            "tests/tools/test_lambda_errors_tool.py",
            "tests/tools/test_lambda_inspect_tool.py",
            "tests/tools/test_lambda_invocation_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/cloudtrail/",
        ("tests/tools/test_cloudtrail_events.py",),
    ),
    PathRule(
        "integrations/cloudwatch/",
        (
            "tests/integrations/aws/test_cloudwatch_client.py",
            "tests/tools/test_cloudwatch_batch_metrics_tool.py",
            "tests/tools/test_cloudwatch_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/ec2/",
        ("tests/tools/test_ec2_instances_by_tag_tool.py",),
    ),
    PathRule(
        "integrations/elb/",
        ("tests/tools/test_elb_target_health_tool.py",),
    ),
    PathRule(
        "integrations/rds/",
        (
            "tests/integrations/test_rds.py",
            "tests/tools/test_rds_tools.py",
        ),
    ),
    PathRule(
        "integrations/s3/",
        (
            "tests/integrations/aws/test_s3_client.py",
            "tests/tools/test_s3_get_object_tool.py",
            "tests/tools/test_s3_inspect_tool.py",
            "tests/tools/test_s3_list_tool.py",
            "tests/tools/test_s3_marker_tool.py",
        ),
    ),
    PathRule(
        "integrations/opsgenie/",
        (
            "tests/integrations/opsgenie/",
            "tests/tools/test_opsgenie_alert_detail_tool.py",
            "tests/tools/test_opsgenie_alerts_tool.py",
        ),
    ),
    PathRule(
        "integrations/pagerduty/",
        (
            "tests/integrations/pagerduty/",
            "tests/tools/test_pagerduty_incident_detail_tool.py",
            "tests/tools/test_pagerduty_incidents_tool.py",
            "tests/tools/test_pagerduty_oncall_tool.py",
            "tests/tools/test_pagerduty_services_tool.py",
        ),
    ),
    PathRule(
        "integrations/prefect/",
        (
            "tests/integrations/test_prefect_catalog.py",
            "tests/tools/test_prefect_flow_runs_tool.py",
            "tests/tools/test_prefect_worker_health_tool.py",
        ),
    ),
    PathRule(
        "integrations/signoz/",
        (
            "tests/integrations/signoz/",
            "tests/tools/test_signoz_tools.py",
        ),
    ),
    PathRule(
        "integrations/splunk/",
        ("tests/integrations/splunk/", "tests/tools/test_splunk_search_tool.py"),
    ),
    PathRule(
        "integrations/tempo/",
        (
            "tests/integrations/tempo/",
            "tests/tools/test_tempo_tools.py",
        ),
    ),
    PathRule(
        "integrations/temporal/",
        (
            "tests/integrations/temporal/",
            "tests/integrations/test_temporal_catalog.py",
            "tests/tools/test_temporal_namespace_info_tool.py",
            "tests/tools/test_temporal_task_queue_tool.py",
            "tests/tools/test_temporal_workflow_history_tool.py",
            "tests/tools/test_temporal_workflows_tool.py",
        ),
    ),
    PathRule(
        "integrations/vercel/",
        (
            "tests/integrations/vercel/",
            "tests/tools/test_vercel_deployment_status_tool.py",
            "tests/tools/test_vercel_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/victoria_logs/",
        (
            "tests/integrations/victoria_logs/",
            "tests/tools/test_victoria_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/x_mcp/",
        (
            "tests/integrations/test_x_mcp.py",
            "tests/tools/test_x_mcp_tool.py",
        ),
    ),
    PathRule(
        "integrations/argocd/",
        (
            "tests/integrations/argocd/",
            "tests/tools/test_argocd_tools.py",
        ),
    ),
    PathRule(
        "integrations/coralogix/",
        (
            "tests/integrations/coralogix/",
            "tests/tools/test_coralogix_logs_tool.py",
        ),
    ),
    PathRule(
        "integrations/honeycomb/",
        (
            "tests/integrations/honeycomb/",
            "tests/tools/test_honeycomb_traces_tool.py",
        ),
    ),
    PathRule(
        "integrations/jenkins/",
        ("tests/integrations/test_jenkins.py",),
    ),
    PathRule(
        "integrations/datadog/",
        (
            "tests/integrations/datadog/",
            "tests/tools/test_datadog_context_tool.py",
            "tests/tools/test_datadog_events_tool.py",
            "tests/tools/test_datadog_logs_tool.py",
            "tests/tools/test_datadog_metrics_tool.py",
            "tests/tools/test_datadog_monitors_tool.py",
            "tests/tools/test_datadog_node_pods_tool.py",
        ),
    ),
    PathRule(
        "integrations/grafana/",
        (
            "tests/integrations/grafana/",
            "tests/tools/test_grafana_alert_rules_tool.py",
            "tests/tools/test_grafana_annotations_tool.py",
            "tests/tools/test_grafana_logs_tool.py",
            "tests/tools/test_grafana_metrics_tool.py",
            "tests/tools/test_grafana_service_names_tool.py",
            "tests/tools/test_grafana_traces_tool.py",
            "tests/e2e/grafana_validation/",
        ),
    ),
    PathRule("integrations/", ("tests/integrations/",)),
    PathRule("tools/system/fleet_monitoring/", ("tests/core/agent/", "tests/fleet_monitoring/")),
    PathRule("surfaces/cli/", ("tests/cli/",)),
    PathRule("surfaces/interactive_shell/", ("tests/interactive_shell/",)),
    PathRule("gateway/", ("gateway/tests/",)),
    PathRule("tools/", ("tests/tools/",)),
    PathRule("infrastructure/analytics/", ("tests/analytics/",)),
    # Without this rule a change under infrastructure/filestorage/ matches nothing,
    # and the credential deny-list tests only run via the no-targets fallback —
    # which a diff that also touches any test file silently defeats.
    PathRule(
        "infrastructure/filestorage/",
        ("tests/filestorage/", "tests/surfaces/test_remote_sync_surface_contract.py"),
    ),
    PathRule("infrastructure/safety/guardrails/", ("tests/infrastructure/safety/guardrails/",)),
    PathRule("infrastructure/safety/masking/", ("tests/masking/",)),
    PathRule("opensre.spec", ("tests/packaging/",)),
    PathRule("infrastructure/deployment/packaging/", ("tests/packaging/",)),
    PathRule("infrastructure/safety/sandbox/", ("tests/sandbox/",)),
    PathRule(
        "infrastructure/deployment/ec2/",
        ("tests/infrastructure/deployment/ec2/",),
    ),
    PathRule("infrastructure/safety/auth/", ("tests/infrastructure/safety/auth/",)),
    PathRule("gateway/web/webapp.py", ("gateway/tests/web/test_webapp.py",)),
    PathRule("infrastructure/scheduling/", ("tests/scheduler/",)),
    PathRule("infrastructure/", ("tests/infrastructure/",)),
    PathRule("config/", ("tests/config/",)),
    PathRule("bootstrap/", ("tests/bootstrap/",)),
    PathRule("surfaces/", ("tests/surfaces/",)),
    # Repository tooling and broad configuration changes still run focused contracts.
    PathRule("pyproject.toml", ("tests/packaging/", "tests/config/")),
    PathRule("uv.lock", ("tests/packaging/", "tests/config/")),
    PathRule("pytest.ini", ("tests/github_ci/",)),
    PathRule("Makefile", ("tests/github_ci/",)),
    PathRule(".github/", ("tests/github_ci/",)),
    PathRule(".githooks/", ("tests/github_ci/",)),
    PathRule(".pre-commit-config.yaml", ("tests/github_ci/",)),
    PathRule("mypy.ini", ("tests/github_ci/",)),
    PathRule(".importlinter", ("tests/shared/", "tests/github_ci/")),
    PathRule(".importlinter.strict", ("tests/shared/", "tests/github_ci/")),
    PathRule(".gitignore", ("tests/github_ci/",)),
)


@dataclass(frozen=True)
class TestSelection:
    """Selected pytest targets and gaps that must block local validation."""

    targets: tuple[str, ...]
    errors: tuple[str, ...]


def select_tests(changed: list[str], *, root: Path) -> TestSelection:
    """Resolve every source path without silently discarding unmapped changes."""
    targets: set[str] = set()
    errors: set[str] = set()
    for path in changed:
        if is_documentation(path):
            continue
        item = Path(path)
        if path.startswith(("tests/", "gateway/tests/")):
            if not (root / path).exists():
                continue
            if item.name.startswith("test_") and item.suffix == ".py":
                targets.add(path)
            else:
                targets.add(item.parent.as_posix() + "/")
            continue
        rule = next(
            (
                rule
                for rule in RULES
                if (
                    path.startswith(rule.path_prefix)
                    if rule.path_prefix.endswith("/")
                    else path == rule.path_prefix
                )
            ),
            None,
        )
        if rule is None:
            errors.add(f"No test rule for {path}")
            continue
        for target in rule.test_targets:
            if (root / target).exists():
                targets.add(target)
            else:
                errors.add(f"Missing configured test target {target} for {path}")
    # Avoid collecting a test twice when a broader directory is already selected.
    directories = {target.rstrip("/") for target in targets if (root / target).is_dir()}
    selected = tuple(
        sorted(
            target
            for target in targets
            if not any(parent.as_posix() in directories for parent in Path(target).parents)
        )
    )
    return TestSelection(selected, tuple(sorted(errors)))
