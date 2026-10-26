from __future__ import annotations

from integrations.pipedream import classify, parse_apps, select_app


def test_classify_accepts_only_webapp_proxy_capability() -> None:
    apps = '[{"service":"notion","app_slug":"notion","account_id":"ap_1"}]'

    source, name = classify(
        {"provider": "pipedream", "access_mode": "webapp_proxy", "apps": apps},
        "pipedream:org_1",
    )

    assert name == "pipedream"
    assert source == {
        "provider": "pipedream",
        "access_mode": "webapp_proxy",
        "apps": [{"service": "notion", "app_slug": "notion", "account_id": "ap_1"}],
        "integration_id": "pipedream:org_1",
    }
    assert classify(
        {"provider": "pipedream", "auth_token": "project-wide", "apps": apps},
        "unsafe",
    ) == (None, None)


def test_duplicate_apps_require_account_id() -> None:
    apps = parse_apps(
        [
            {"service": "notion", "app_slug": "notion", "account_id": "ap_1"},
            {"service": "notion", "app_slug": "notion", "account_id": "ap_2"},
        ]
    )

    assert select_app(apps, "notion") is None
    assert select_app(apps, "notion", "ap_2") == apps[1]
