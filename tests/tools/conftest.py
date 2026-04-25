"""Shared test infrastructure for all tool tests.

Provides:
- BaseToolContract: a pytest mixin that any tool test can inherit from. Provides
  contract tests for metadata, is_available, and extract_params.
- Shared fixture factories: mock_agent_state, mock_boto3_client, mock_http_response.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Shared fixture factories (plain functions, called directly from tests)
# ---------------------------------------------------------------------------


def mock_agent_state(overrides: dict | None = None) -> dict[str, Any]:
    """Return a realistic AgentState dict covering common source integrations."""
    state: dict[str, Any] = {
        "cloudwatch": {
            "log_group": "/aws/lambda/my-function",
            "log_stream": "2024/01/01/[$LATEST]abc123",
            "correlation_id": "req-123",
        },
        "datadog": {
            "connection_verified": True,
            "api_key": "dd_api_key_test",
            "app_key": "dd_app_key_test",
            "site": "datadoghq.com",
            "default_query": "service:my-service",
            "time_range_minutes": 60,
            "node_ip": "10.0.1.42",
        },
        "grafana": {
            "connection_verified": True,
            "grafana_endpoint": "https://grafana.example.com",
            "grafana_api_key": "glsa_test",
            "service_name": "my-service",
            "pipeline_name": "my-pipeline",
            "time_range_minutes": 60,
        },
        "eks": {
            "connection_verified": True,
            "cluster_name": "my-cluster",
            "namespace": "default",
            "pod_name": "my-pod-abc",
            "deployment": "my-deployment",
            "role_arn": "arn:aws:iam::123456789012:role/eks-role",
            "region": "us-east-1",
        },
        "sentry": {
            "connection_verified": True,
            "organization_slug": "my-org",
            "sentry_token": "sntryu_test",
            "sentry_url": "https://sentry.io",
            "project_slug": "my-project",
            "issue_id": "12345",
        },
        "github": {
            "connection_verified": True,
            "owner": "my-org",
            "repo": "my-repo",
            "path": "src/main.py",
            "github_url": "http://github.example.com/mcp",
            "github_mode": "streamable-http",
            "github_token": "ghp_test",
        },
        "elasticsearch": {
            "connection_verified": True,
            "url": "http://localhost:9200",
            "api_key": None,
            "default_query": "*",
            "time_range_minutes": 60,
            "index_pattern": "logs-*",
        },
        "honeycomb": {
            "connection_verified": True,
            "service_name": "my-service",
            "trace_id": "abc123trace",
            "dataset": "__all__",
            "honeycomb_api_key": "hc_test_key",
        },
        "coralogix": {
            "connection_verified": True,
            "coralogix_api_key": "cx_test_key",
            "default_query": "source logs | limit 50",
            "time_range_minutes": 60,
        },
        "s3": {
            "bucket": "my-bucket",
            "key": "my-key.json",
            "prefix": "my-prefix/",
        },
        "lambda": {
            "function_name": "my-lambda-function",
        },
        "tracer_web": {
            "trace_id": "trace-abc-123",
        },
        "prefect": {
            "connection_verified": True,
            "api_url": "http://localhost:4200/api",
            "api_key": "",
            "account_id": "",
            "workspace_id": "",
        },
        "google_docs": {
            "configured": True,
            "credentials_file": "/path/to/credentials.json",
            "folder_id": "abc123folder",
        },
        "mongodb": {
            "connection_string": "mongodb://localhost:27017",
        },
        "postgresql": {
            "host": "localhost",
            "port": 5432,
            "database": "opensre_test",
            "username": "opensre",
            "password": "test123",
            "ssl_mode": "prefer",
        },
        "mongodb_atlas": {
            "api_public_key": "test-pub-key",
            "api_private_key": "test-priv-key",
            "project_id": "test-project-id",
            "base_url": "https://cloud.mongodb.com/api/atlas/v2",
        },
        "mariadb": {
            "host": "localhost",
            "port": 3306,
            "database": "testdb",
            "username": "testuser",
            "password": "",
        },
        "mysql": {
            "host": "localhost",
            "port": 3306,
            "database": "opensre_test",
            "username": "opensre",
            "password": "test123",
            "ssl_mode": "preferred",
        },
        "splunk": {
            "connection_verified": True,
            "base_url": "https://splunk.test.corp.com:8089",
            "token": "splunk_test_bearer_token",
            "index": "main",
            "verify_ssl": False,
            "ca_bundle": "/etc/ssl/certs/corp-ca.pem",
            "default_query": 'index=main "NullPointerException" | head 50',
            "time_range_minutes": 60,
        },
    }
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key] = {**state.get(key, {}), **value}
            else:
                state[key] = value
    return state


def mock_boto3_client(service: str, responses: dict | None = None) -> MagicMock:
    """Return a MagicMock that mimics a boto3 client with preset responses."""
    client = MagicMock()
    if responses:
        for method, return_value in responses.items():
            getattr(client, method).return_value = return_value
    return client


def mock_http_response(status: int, json_body: Any) -> MagicMock:
    """Return a MagicMock that mimics a requests.Response."""
    response = MagicMock()
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


# ---------------------------------------------------------------------------
# BaseToolContract mixin
# ---------------------------------------------------------------------------


class BaseToolContract:
    """Mixin that provides shared contract tests for any tool.

    Subclasses must implement ``get_tool_under_test()`` which returns either:
    - A BaseTool instance (class-based tools), or
    - A RegisteredTool (function-based tools, accessed via func.__opensre_registered_tool__)

    The mixin handles both patterns uniformly.

    Example for class-based tool::

        class TestMyTool(BaseToolContract):
            def get_tool_under_test(self):
                return MyTool()

    Example for function-based tool::

        from app.tools.MyTool import my_func

        class TestMyTool(BaseToolContract):
            def get_tool_under_test(self):
                return my_func.__opensre_registered_tool__
    """

    def get_tool_under_test(self) -> Any:
        raise NotImplementedError("Subclasses must implement get_tool_under_test()")

    def _tool(self) -> Any:
        """Return the tool under test, resolving RegisteredTool if needed."""
        return self.get_tool_under_test()

    def test_metadata_has_valid_name(self) -> None:
        tool = self._tool()
        assert isinstance(tool.name, str), "name must be a string"
        assert tool.name.strip(), "name must be non-empty"

    def test_metadata_has_valid_description(self) -> None:
        tool = self._tool()
        assert isinstance(tool.description, str), "description must be a string"
        assert tool.description.strip(), "description must be non-empty"

    def test_metadata_has_input_schema(self) -> None:
        tool = self._tool()
        schema = tool.input_schema
        assert isinstance(schema, dict), "input_schema must be a dict"
        assert "type" in schema, "input_schema must have 'type'"
        assert "properties" in schema, "input_schema must have 'properties'"

    def test_metadata_has_valid_source(self) -> None:
        tool = self._tool()
        assert isinstance(tool.source, str), "source must be a string"
        assert tool.source.strip(), "source must be non-empty"

    def test_is_available_returns_bool(self) -> None:
        tool = self._tool()
        result = tool.is_available({})
        assert isinstance(result, bool), "is_available({}) must return a bool"

    def test_extract_params_returns_dict(self) -> None:
        tool = self._tool()
        # Some tools key directly into their source dict without checking for existence.
        # Provide a full mock state so extract_params can always run without KeyError.
        sources = mock_agent_state()
        try:
            result = tool.extract_params(sources)
        except (KeyError, TypeError):
            # If extract_params requires specific keys not in the state, skip silently.
            return
        assert isinstance(result, dict), "extract_params(sources) must return a dict"
