"""AST checks: every migrated broad-except in integrations calls report_validation_failure.

Issue #1470 acceptance criterion: every listed validator must invoke
``report_validation_failure`` on its broad ``except Exception`` block, with
tags carrying ``integration`` and ``method`` so Sentry events are filterable
by vendor.

This test parses each integration module and asserts, for every listed
``(function, line)`` pair, that:

1. The function's broad ``except Exception`` handler contains a call to
   ``report_validation_failure``.
2. The ``integration=`` and ``method=`` keyword arguments are string literals
   matching the expected tag values.

Two intentional exceptions are excluded — both are inner-loop ``except`` blocks
that ``continue`` or ``raise``; their failures already reach Sentry via the
outer except (or are deliberate version-detection fallbacks):

- ``mysql.get_replication_status`` inner ``stmt_err`` (MySQL <8.0.22 fallback)
- ``mariadb.get_replication_status`` inner ``stmt_err`` (analogous)

Some vendors no longer inline the broad-except: relational diagnostics can
delegate the whole connect/query/error lifecycle to the shared
``read_only_query`` wrapper in ``integrations/_relational.py``. Those cases set
``decorated_with`` and are checked for the decorator instead; the wrapper's own
reporting is asserted once in
:func:`test_shared_read_only_wrapper_reports_failures`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MigrationCase:
    module_path: str  # repo-relative file path
    function: str  # outer function name (may include "."-separated nested suffix)
    integration: str  # expected tag.integration
    method: str  # expected tag.method
    # When set, the function delegates its broad-except to this decorator (the
    # shared `read_only_query` binding) instead of inlining one.
    decorated_with: str | None = None


CASES: tuple[MigrationCase, ...] = (
    # trello
    MigrationCase(
        "integrations/trello/verifier.py",
        "validate_trello_config",
        "trello",
        "validate_trello_config",
    ),
    # kafka
    MigrationCase(
        "integrations/kafka.py", "validate_kafka_config", "kafka", "validate_kafka_config"
    ),
    MigrationCase("integrations/kafka.py", "get_topic_health", "kafka", "get_topic_health"),
    MigrationCase(
        "integrations/kafka.py", "get_consumer_group_lag", "kafka", "get_consumer_group_lag"
    ),
    # clickhouse
    MigrationCase(
        "integrations/clickhouse.py",
        "validate_clickhouse_config",
        "clickhouse",
        "validate_clickhouse_config",
    ),
    MigrationCase(
        "integrations/clickhouse.py", "get_query_activity", "clickhouse", "get_query_activity"
    ),
    MigrationCase(
        "integrations/clickhouse.py", "get_system_health", "clickhouse", "get_system_health"
    ),
    MigrationCase("integrations/clickhouse.py", "get_table_stats", "clickhouse", "get_table_stats"),
    # github_mcp
    MigrationCase(
        "integrations/github/mcp.py",
        "validate_github_mcp_config",
        "github_mcp",
        "validate_github_mcp_config",
    ),
    # airflow (validate + loop site)
    MigrationCase(
        "integrations/airflow/config.py",
        "validate_airflow_config",
        "airflow",
        "validate_airflow_config",
    ),
    MigrationCase(
        "integrations/airflow/config.py",
        "get_recent_airflow_failures",
        "airflow",
        "get_recent_airflow_failures.task_instances",
    ),
    # posthog
    MigrationCase(
        "integrations/posthog/verifier.py",
        "validate_posthog_config",
        "posthog",
        "validate_posthog_config",
    ),
    # azure_sql
    MigrationCase(
        "integrations/azure_sql.py",
        "validate_azure_sql_config",
        "azure_sql",
        "validate_azure_sql_config",
    ),
    MigrationCase(
        "integrations/azure_sql.py", "get_server_status", "azure_sql", "get_server_status"
    ),
    MigrationCase(
        "integrations/azure_sql.py", "get_current_queries", "azure_sql", "get_current_queries"
    ),
    MigrationCase(
        "integrations/azure_sql.py", "get_resource_stats", "azure_sql", "get_resource_stats"
    ),
    MigrationCase("integrations/azure_sql.py", "get_slow_queries", "azure_sql", "get_slow_queries"),
    MigrationCase("integrations/azure_sql.py", "get_wait_stats", "azure_sql", "get_wait_stats"),
    # betterstack
    MigrationCase(
        "integrations/betterstack.py",
        "validate_betterstack_config",
        "betterstack",
        "validate_betterstack_config",
    ),
    MigrationCase("integrations/betterstack.py", "query_logs", "betterstack", "query_logs"),
    # gitlab
    MigrationCase(
        "integrations/gitlab/client.py",
        "validate_gitlab_config",
        "gitlab",
        "validate_gitlab_config",
    ),
    # bitbucket
    MigrationCase(
        "integrations/bitbucket/client.py",
        "validate_bitbucket_config",
        "bitbucket",
        "validate_bitbucket_config",
    ),
    MigrationCase("integrations/bitbucket/client.py", "list_commits", "bitbucket", "list_commits"),
    MigrationCase(
        "integrations/bitbucket/client.py", "get_file_contents", "bitbucket", "get_file_contents"
    ),
    MigrationCase("integrations/bitbucket/client.py", "search_code", "bitbucket", "search_code"),
    # mongodb
    MigrationCase(
        "integrations/mongodb.py",
        "validate_mongodb_config",
        "mongodb",
        "validate_mongodb_config",
    ),
    MigrationCase("integrations/mongodb.py", "get_server_status", "mongodb", "get_server_status"),
    MigrationCase("integrations/mongodb.py", "get_current_ops", "mongodb", "get_current_ops"),
    MigrationCase("integrations/mongodb.py", "get_rs_status", "mongodb", "get_rs_status"),
    MigrationCase("integrations/mongodb.py", "get_profiler_data", "mongodb", "get_profiler_data"),
    MigrationCase(
        "integrations/mongodb.py", "get_collection_stats", "mongodb", "get_collection_stats"
    ),
    # postgresql
    MigrationCase(
        "integrations/postgresql.py",
        "validate_postgresql_config",
        "postgresql",
        "validate_postgresql_config",
    ),
    MigrationCase(
        "integrations/postgresql.py", "get_server_status", "postgresql", "get_server_status"
    ),
    MigrationCase(
        "integrations/postgresql.py", "get_current_queries", "postgresql", "get_current_queries"
    ),
    MigrationCase(
        "integrations/postgresql.py",
        "get_replication_status",
        "postgresql",
        "get_replication_status",
    ),
    MigrationCase(
        "integrations/postgresql.py", "get_slow_queries", "postgresql", "get_slow_queries"
    ),
    MigrationCase("integrations/postgresql.py", "get_table_stats", "postgresql", "get_table_stats"),
    # mysql
    MigrationCase(
        "integrations/mysql.py", "validate_mysql_config", "mysql", "validate_mysql_config"
    ),
    # MySQL diagnostics delegate the connect/query/error lifecycle to the shared
    # `read_only_query` wrapper, so they carry the decorator instead of an
    # inline broad-except.
    MigrationCase(
        "integrations/mysql.py",
        "get_server_status",
        "mysql",
        "get_server_status",
        decorated_with="_read_only",
    ),
    MigrationCase(
        "integrations/mysql.py",
        "get_current_processes",
        "mysql",
        "get_current_processes",
        decorated_with="_read_only",
    ),
    MigrationCase(
        "integrations/mysql.py",
        "get_replication_status",
        "mysql",
        "get_replication_status",
        decorated_with="_read_only",
    ),
    MigrationCase(
        "integrations/mysql.py",
        "get_slow_queries",
        "mysql",
        "get_slow_queries",
        decorated_with="_read_only",
    ),
    MigrationCase(
        "integrations/mysql.py",
        "get_table_stats",
        "mysql",
        "get_table_stats",
        decorated_with="_read_only",
    ),
    # mariadb
    MigrationCase(
        "integrations/mariadb.py",
        "validate_mariadb_config",
        "mariadb",
        "validate_mariadb_config",
    ),
    MigrationCase("integrations/mariadb.py", "get_process_list", "mariadb", "get_process_list"),
    MigrationCase("integrations/mariadb.py", "get_global_status", "mariadb", "get_global_status"),
    MigrationCase("integrations/mariadb.py", "get_innodb_status", "mariadb", "get_innodb_status"),
    MigrationCase("integrations/mariadb.py", "get_slow_queries", "mariadb", "get_slow_queries"),
    MigrationCase(
        "integrations/mariadb.py", "get_replication_status", "mariadb", "get_replication_status"
    ),
    # rabbitmq
    MigrationCase(
        "integrations/rabbitmq.py",
        "validate_rabbitmq_config",
        "rabbitmq",
        "validate_rabbitmq_config",
    ),
    MigrationCase("integrations/rabbitmq.py", "get_queue_backlog", "rabbitmq", "get_queue_backlog"),
    MigrationCase(
        "integrations/rabbitmq.py", "get_consumer_health", "rabbitmq", "get_consumer_health"
    ),
    MigrationCase(
        "integrations/rabbitmq.py", "get_broker_overview", "rabbitmq", "get_broker_overview"
    ),
    MigrationCase("integrations/rabbitmq.py", "get_node_health", "rabbitmq", "get_node_health"),
    MigrationCase(
        "integrations/rabbitmq.py", "get_connection_stats", "rabbitmq", "get_connection_stats"
    ),
    # mongodb_atlas
    MigrationCase(
        "integrations/mongodb_atlas.py",
        "validate_mongodb_atlas_config",
        "mongodb_atlas",
        "validate_mongodb_atlas_config",
    ),
    MigrationCase("integrations/mongodb_atlas.py", "get_clusters", "mongodb_atlas", "get_clusters"),
    MigrationCase("integrations/mongodb_atlas.py", "get_alerts", "mongodb_atlas", "get_alerts"),
    MigrationCase(
        "integrations/mongodb_atlas.py",
        "get_cluster_metrics",
        "mongodb_atlas",
        "get_cluster_metrics",
    ),
    MigrationCase(
        "integrations/mongodb_atlas.py",
        "get_performance_advisor",
        "mongodb_atlas",
        "get_performance_advisor",
    ),
    MigrationCase(
        "integrations/mongodb_atlas.py",
        "get_cluster_events",
        "mongodb_atlas",
        "get_cluster_events",
    ),
    # sentry (its own validator captures into OpenSRE's Sentry)
    MigrationCase(
        "integrations/sentry.py", "validate_sentry_config", "sentry", "validate_sentry_config"
    ),
    # adjacent
    MigrationCase(
        "integrations/daily_update.py",
        "summarize_highlights",
        "daily_update",
        "summarize_highlights",
    ),
    MigrationCase(
        "integrations/llm_cli/kimi.py",
        "_check_kimi_auth_fallback",
        "kimi",
        "_check_kimi_auth_fallback",
    ),
)


def _walk_funcs(tree: ast.AST) -> Iterator[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            yield node


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for fn in _walk_funcs(tree):
        if fn.name == name:
            return fn
    return None


def _broad_except_handlers(fn: ast.FunctionDef) -> list[ast.ExceptHandler]:
    """Return the *outermost* ``except Exception`` handlers in the function body.

    Skips handlers that are themselves nested inside another broad-except — a
    `report_validation_failure` placed in an inner handler wouldn't compensate
    for a missing call in the outer one, which is the false-positive Greptile
    flagged in PR #1869.
    """
    all_handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            t = node.type
            if isinstance(t, ast.Name) and t.id == "Exception":
                all_handlers.append(node)

    nested: set[int] = set()
    for outer in all_handlers:
        for descendant in ast.walk(outer):
            if descendant is outer:
                continue
            if isinstance(descendant, ast.ExceptHandler):
                t = descendant.type
                if isinstance(t, ast.Name) and t.id == "Exception":
                    nested.add(id(descendant))

    return [h for h in all_handlers if id(h) not in nested]


def _calls_to(handler: ast.ExceptHandler, func_name: str) -> list[ast.Call]:
    """Find ``func_name(...)`` calls inside an except handler body.

    Does not descend into nested ``except`` handler bodies — a call inside a
    nested broad-except does not satisfy coverage of the outer one.
    """
    matches: list[ast.Call] = []
    stack: list[ast.AST] = list(handler.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ExceptHandler):
            # Don't descend into another except's body; its calls aren't ours.
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            matches.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return matches


def _decorator_names(fn: ast.FunctionDef) -> set[str]:
    """Return the bare names of a function's decorators (``@foo`` and ``@foo(...)``)."""
    names: set[str] = set()
    for node in fn.decorator_list:
        target = node.func if isinstance(node, ast.Call) else node
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _kwarg_has_name(call: ast.Call, key: str, expected: str) -> bool:
    """True when ``key=`` is passed the non-literal expression ``expected``."""
    for kw in call.keywords:
        if kw.arg != key:
            continue
        return ast.unparse(kw.value) == expected
    return False


def _kwarg_str(call: ast.Call, key: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _source_path(module_path: str) -> Path:
    path = _REPO_ROOT / module_path
    if path.exists():
        return path
    if module_path.startswith("integrations/") and module_path.endswith(".py"):
        package_path = _REPO_ROOT / module_path.removesuffix(".py") / "__init__.py"
        if package_path.exists():
            return package_path
    return path


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda c: f"{Path(c.module_path).stem}::{c.function}",
)
def test_broad_except_calls_report_validation_failure(case: MigrationCase) -> None:
    source = _source_path(case.module_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = _find_function(tree, case.function)
    assert fn is not None, f"function {case.function} not found in {case.module_path}"

    if case.decorated_with:
        # The broad-except lives in the shared wrapper, which reports with
        # integration=<vendor> and method=<wrapped fn name>. Assert the
        # delegation is in place; the wrapper itself is covered by
        # test_shared_read_only_wrapper_reports_failures.
        assert case.decorated_with in _decorator_names(fn), (
            f"{case.module_path}::{case.function} is expected to delegate error "
            f"reporting via @{case.decorated_with}, but that decorator is missing"
        )
        return

    handlers = _broad_except_handlers(fn)
    assert handlers, f"no `except Exception` handlers in {case.module_path}::{case.function}"

    matching_calls: list[ast.Call] = []
    for h in handlers:
        for call in _calls_to(h, "report_validation_failure"):
            integration = _kwarg_str(call, "integration")
            method = _kwarg_str(call, "method")
            if integration == case.integration and method == case.method:
                matching_calls.append(call)

    assert matching_calls, (
        f"{case.module_path}::{case.function} has no `report_validation_failure` call "
        f"with integration={case.integration!r} and method={case.method!r}"
    )
    # Each tagged (integration, method) pair should appear exactly once per function
    # to prevent accidental double-capture in the same except block.
    assert len(matching_calls) == 1, (
        f"{case.module_path}::{case.function} reports the same "
        f"(integration={case.integration!r}, method={case.method!r}) "
        f"{len(matching_calls)} times; expected exactly once"
    )


def test_shared_read_only_wrapper_reports_failures() -> None:
    """The shared relational wrapper must report on its broad-except.

    Cases with ``decorated_with`` set delegate their error handling here, so
    this is the single place that coverage is proven for all of them. The tags
    are dynamic — ``integration`` is the bound vendor name and ``method`` is the
    wrapped function's ``__name__`` — so assert on the expressions, not literals.
    """
    source = (_REPO_ROOT / "integrations/_relational.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    wrapper = _find_function(tree, "wrapper")
    assert wrapper is not None, "read_only_query no longer defines its `wrapper` closure"

    handlers = _broad_except_handlers(wrapper)
    assert handlers, "read_only_query's wrapper has no `except Exception` handler"

    calls = [c for h in handlers for c in _calls_to(h, "report_validation_failure")]
    assert len(calls) == 1, (
        f"read_only_query's wrapper should report exactly once; found {len(calls)}"
    )

    call = calls[0]
    assert _kwarg_has_name(call, "integration", "integration"), (
        "wrapper must tag the bound vendor name as integration="
    )
    assert _kwarg_has_name(call, "method", "fn.__name__"), (
        "wrapper must tag the wrapped function's name as method="
    )


def test_every_migrated_module_imports_the_helper() -> None:
    """Sanity guard: every file we touched should import report_validation_failure."""
    seen_modules = {case.module_path for case in CASES}
    for module_path in seen_modules:
        source = _source_path(module_path).read_text(encoding="utf-8")
        # Accept both a standalone import line and a combined import that also
        # brings in other helpers (e.g. report_classify_failure).
        imported = (
            "from integrations._validation_helpers import report_validation_failure" in source
            or (
                "from integrations._validation_helpers import" in source
                and "report_validation_failure" in source
            )
        )
        assert imported, (
            f"{module_path} migration is incomplete: missing import of report_validation_failure"
        )


def test_broad_except_handlers_skips_nested_handlers() -> None:
    """The helper must ignore broad-except handlers nested inside another broad-except.

    Otherwise a `report_validation_failure` call in the inner handler would
    falsely satisfy the assertion when the outer handler is uncovered (the
    false-positive path flagged by Greptile on PR #1869).
    """
    source = """
def f():
    try:
        do_thing()
    except Exception:
        try:
            cleanup()
        except Exception:
            report_validation_failure(err, integration="x", method="f")
"""
    tree = ast.parse(source)
    fn = _find_function(tree, "f")
    assert fn is not None
    handlers = _broad_except_handlers(fn)
    assert len(handlers) == 1, "should return only the outer handler"
    # The call exists only in the inner handler; helper must not surface it.
    assert _calls_to(handlers[0], "report_validation_failure") == []


def test_broad_except_handlers_finds_handler_inside_normal_control_flow() -> None:
    """Handlers inside for/while/if are still surfaced — only nested-except is excluded.

    Airflow's per-DAG-run capture lives inside a ``for`` loop's try block; the
    helper must not regress that case.
    """
    source = """
def f():
    for x in items:
        try:
            work(x)
        except Exception:
            report_validation_failure(err, integration="x", method="f")
            continue
"""
    tree = ast.parse(source)
    fn = _find_function(tree, "f")
    assert fn is not None
    handlers = _broad_except_handlers(fn)
    assert len(handlers) == 1
    assert _calls_to(handlers[0], "report_validation_failure")
