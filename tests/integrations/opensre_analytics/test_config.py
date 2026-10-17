"""A dashboard-connected record must classify into a usable MCP connection."""

from __future__ import annotations

from integrations.catalog import classify_integrations


def test_a_dashboard_record_classifies_with_the_site_url_pointed_at_the_mcp_endpoint() -> None:
    # Arrange: the webapp passes its form through unchanged: the site URL people
    # copy from the browser, and the token under the form's key.
    records = [
        {
            "id": "rec-analytics",
            "service": "opensre_analytics",
            "status": "active",
            "credentials": {
                "url": "https://app.opensre-analytics.com/",
                "auth_token": "Bearer analytics-token",
            },
        },
        {
            "id": "rec-no-token",
            "service": "opensre_analytics",
            "status": "active",
            "credentials": {"url": "https://other.example.com"},
        },
    ]

    # Act
    resolved = classify_integrations(records)

    # Assert: the tokenless record is skipped rather than shadowing the good one.
    config = resolved["opensre_analytics"]
    assert config.url == "https://app.opensre-analytics.com/api/viktor/mcp"
    assert config.request_headers == {"Authorization": "Bearer analytics-token"}
    assert config.integration_id == "rec-analytics"
    assert "_all_opensre_analytics_instances" not in resolved


def test_the_token_is_never_sent_over_plain_http_to_another_host() -> None:
    # Arrange: a record pointing at a non-local http URL.
    records = [
        {
            "id": "rec-http",
            "service": "opensre_analytics",
            "status": "active",
            "credentials": {"url": "http://analytics.example.com", "auth_token": "token"},
        }
    ]

    # Act
    resolved = classify_integrations(records)

    # Assert: skipped, so no tool or report sends the bearer token there.
    assert "opensre_analytics" not in resolved
