"""Tests for the shared configured-integration-services helper.

This helper is the single source of truth shared by the welcome banner and the
REPL session, so it must return lowercase service keys, deduplicate, and never
raise (returning an empty list on failure).
"""

from __future__ import annotations

from typing import Any

from integrations import catalog


def test_merge_preserves_multiple_connections_for_every_service() -> None:
    remote = [
        {"id": "grafana-personal", "service": "grafana", "status": "active"},
        {"id": "grafana-team", "service": "grafana", "status": "active"},
    ]

    merged = catalog.merge_integrations_by_service(
        [{"id": "grafana-env", "service": "grafana", "status": "active"}],
        [],
        remote,
    )

    assert [record["id"] for record in merged] == ["grafana-personal", "grafana-team"]


def test_returns_lowercase_service_keys_deduplicated(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        catalog,
        "load_env_integration_services",
        lambda: ["GitLab", "datadog", "gitlab", ""],
    )
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == ["gitlab", "datadog"]


def test_includes_active_store_integrations_and_dedupes_with_env(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        catalog,
        "load_env_integration_services",
        lambda: ["sentry", "gitlab"],
    )
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [
            {"service": "GitHub", "status": "active"},  # store-only (e.g. first-launch login)
            {"service": "gitlab", "status": "active"},  # duplicate of env entry
            {"service": "datadog", "status": "disabled"},  # inactive — ignored
            {"service": "", "status": "active"},  # ignored
        ],
    )
    assert catalog.configured_integration_services() == ["sentry", "gitlab", "github"]


def test_unsupported_active_store_integration_is_not_advertised(monkeypatch: Any) -> None:
    """A record left by an uninstalled integration must not reach startup surfaces."""
    monkeypatch.setattr(catalog, "load_env_integration_services", list)
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [
            {
                "service": "retired_integration",
                "status": "active",
                "credentials": {"token": "stale"},
            }
        ],
    )

    assert catalog.configured_integration_services() == []
    assert catalog.configured_integration_health() == []


def test_active_store_family_member_is_advertised(monkeypatch: Any) -> None:
    """Persisted family members remain visible after registry-based filtering."""
    monkeypatch.setattr(catalog, "load_env_integration_services", list)
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [{"service": "grafana_local", "status": "active"}],
    )

    assert catalog.configured_integration_services() == ["grafana_local"]
    assert catalog.configured_integration_health() == [("grafana_local", "ok")]


def test_returns_empty_list_when_env_loader_raises(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integration_services", _boom)
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == []


def test_store_only_when_env_loader_raises(monkeypatch: Any) -> None:
    def _boom() -> list[str]:
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(catalog, "load_env_integration_services", _boom)
    monkeypatch.setattr(
        catalog,
        "load_integrations",
        lambda: [{"service": "github", "status": "active"}],
    )
    assert catalog.configured_integration_services() == ["github"]


def test_empty_when_no_integrations(monkeypatch: Any) -> None:
    monkeypatch.setattr(catalog, "load_env_integration_services", list)
    monkeypatch.setattr(catalog, "load_integrations", list)
    assert catalog.configured_integration_services() == []


def test_configured_services_do_not_call_full_env_loader(monkeypatch: Any) -> None:
    def _full_loader_should_not_run() -> list[dict[str, Any]]:
        raise AssertionError("startup metadata path must not resolve env integrations")

    monkeypatch.setattr(catalog, "load_env_integrations", _full_loader_should_not_run)
    monkeypatch.setattr(catalog, "load_env_integration_services", lambda: ["gitlab"])
    monkeypatch.setattr(catalog, "load_integrations", list)

    assert catalog.configured_integration_services() == ["gitlab"]


def test_env_service_list_uses_plain_env_without_credentials_file(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("POSTHOG_MCP_AUTH_TOKEN", raising=False)

    def _file_should_not_run(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("startup metadata path must not read the credentials file")

    monkeypatch.setattr("config.secrets.local_file.get", _file_should_not_run)

    assert "gitlab" in catalog.load_env_integration_services()


def test_slack_bot_token_marks_slack_configured(monkeypatch: Any) -> None:
    # The Slack gateway runs on SLACK_BOT_TOKEN; slack must show as a configured
    # integration so the agent doesn't self-describe as telegram-only on Slack.
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    assert "slack" in catalog.load_env_integration_services()


def test_slack_absent_without_slack_env(monkeypatch: Any) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_ACCESS_TOKEN", raising=False)
    assert "slack" not in catalog.load_env_integration_services()


def test_google_docs_env_marks_google_docs_configured(monkeypatch: Any) -> None:
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "/path/to/sa.json")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-1")
    assert "google_docs" in catalog.load_env_integration_services()


def test_google_docs_absent_without_both_env_vars(monkeypatch: Any) -> None:
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "/path/to/sa.json")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    assert "google_docs" not in catalog.load_env_integration_services()


class TestConfiguredIntegrationHealth:
    """Offline health for the welcome banner: present vs. minimally usable.

    The banner must not imply a half-configured integration (e.g. a hosted MCP
    record saved without an API token) is connected, so this helper downgrades
    such records to ``"incomplete"`` without running any network verification.
    """

    def test_ok_when_classified_into_usable_config(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "load_env_integration_services", lambda: ["datadog", "gitlab"])
        monkeypatch.setattr(catalog, "load_integrations", list)
        assert catalog.configured_integration_health() == [
            ("datadog", "ok"),
            ("gitlab", "ok"),
        ]

    def test_hosted_mcp_without_token_is_incomplete(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "load_env_integration_services", list)
        monkeypatch.setattr(
            catalog,
            "load_integrations",
            lambda: [
                {
                    "service": "posthog_mcp",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {
                                "mode": "streamable-http",
                                "url": "https://mcp.posthog.com/mcp",
                                "auth_token": "",
                            },
                        }
                    ],
                }
            ],
        )
        assert catalog.configured_integration_health() == [("posthog_mcp", "incomplete")]

    def test_hosted_mcp_with_token_is_ok(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "load_env_integration_services", list)
        monkeypatch.setattr(
            catalog,
            "load_integrations",
            lambda: [
                {
                    "service": "posthog_mcp",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {
                                "mode": "streamable-http",
                                "url": "https://mcp.posthog.com/mcp",
                                "auth_token": "phx_secret",
                            },
                        }
                    ],
                }
            ],
        )
        assert catalog.configured_integration_health() == [("posthog_mcp", "ok")]

    def test_stdio_mcp_without_token_is_ok(self, monkeypatch: Any) -> None:
        # stdio MCP authenticates via the local subprocess, so no token is needed.
        monkeypatch.setattr(catalog, "load_env_integration_services", list)
        monkeypatch.setattr(
            catalog,
            "load_integrations",
            lambda: [
                {
                    "service": "posthog_mcp",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {"mode": "stdio", "command": "npx", "auth_token": ""},
                        }
                    ],
                }
            ],
        )
        assert catalog.configured_integration_health() == [("posthog_mcp", "ok")]

    def test_non_mcp_empty_token_field_is_not_flagged(self, monkeypatch: Any) -> None:
        # Only the hosted-MCP token rule applies; an unrelated service that
        # classified successfully stays "ok" even if it lacks an auth_token key.
        monkeypatch.setattr(catalog, "load_env_integration_services", lambda: ["github"])
        monkeypatch.setattr(catalog, "load_integrations", list)
        assert catalog.configured_integration_health() == [("github", "ok")]

    def test_empty_when_no_integrations(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(catalog, "load_env_integration_services", list)
        monkeypatch.setattr(catalog, "load_integrations", list)
        assert catalog.configured_integration_health() == []

    def test_defaults_ok_when_store_load_raises(self, monkeypatch: Any) -> None:
        def _boom() -> list[dict[str, Any]]:
            raise RuntimeError("store unreadable")

        monkeypatch.setattr(catalog, "load_env_integration_services", lambda: ["datadog"])
        monkeypatch.setattr(catalog, "load_integrations", _boom)
        # Resolution failure must not crash the banner or alarm the user: when
        # health can't be determined offline, every service falls back to "ok".
        assert catalog.configured_integration_health() == [("datadog", "ok")]

    def test_health_does_not_call_effective_resolution(self, monkeypatch: Any) -> None:
        def _resolve_should_not_run() -> dict[str, Any]:
            raise AssertionError("startup health must not resolve secrets")

        monkeypatch.setattr(catalog, "load_env_integration_services", lambda: ["datadog"])
        monkeypatch.setattr(catalog, "load_integrations", list)
        monkeypatch.setattr(catalog, "resolve_effective_integrations", _resolve_should_not_run)

        assert catalog.configured_integration_health() == [("datadog", "ok")]

    def test_health_loads_store_at_most_once(self, monkeypatch: Any) -> None:
        calls = 0

        def counting_load() -> list[dict[str, Any]]:
            nonlocal calls
            calls += 1
            return [
                {
                    "service": "github",
                    "status": "active",
                    "instances": [
                        {
                            "name": "default",
                            "tags": {},
                            "credentials": {"token": "ghp_test"},
                        }
                    ],
                }
            ]

        monkeypatch.setattr(catalog, "load_env_integration_services", list)
        monkeypatch.setattr(catalog, "load_integrations", counting_load)

        assert catalog.configured_integration_health() == [("github", "ok")]
        assert calls == 1
