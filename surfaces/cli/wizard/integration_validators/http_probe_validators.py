"""HTTP-probe onboarding integration validators."""

from __future__ import annotations

from http import HTTPStatus

import httpx

from integrations.config_models import SlackWebhookConfig

from .shared import IntegrationHealthResult


def validate_slack_webhook(*, webhook_url: str) -> IntegrationHealthResult:
    """Validate Slack webhook format and do a non-posting reachability probe."""
    try:
        slack_config = SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    try:
        response = httpx.get(
            slack_config.webhook_url,
            timeout=10,
            follow_redirects=False,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Slack webhook validation failed: {err}")

    if response.status_code == HTTPStatus.NOT_FOUND:
        return IntegrationHealthResult(
            ok=False, detail="Slack webhook returned 404; the URL looks invalid."
        )
    if response.status_code in {
        HTTPStatus.OK,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }:
        return IntegrationHealthResult(
            ok=True,
            detail=f"Slack webhook endpoint reachable (HTTP {response.status_code}) using a non-posting probe.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Slack webhook probe returned unexpected HTTP {response.status_code}.",
    )


def validate_jira_integration(
    *, base_url: str, email: str, api_token: str, project_key: str
) -> IntegrationHealthResult:
    """Validate Jira connectivity and project key accessibility."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/rest/api/3/myself",
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == HTTPStatus.OK:
            data = resp.json()
            display = data.get("displayName") or data.get("emailAddress") or email

            project_resp = httpx.get(
                f"{base_url.rstrip('/')}/rest/api/3/project/{project_key}",
                auth=(email, api_token),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if project_resp.status_code == HTTPStatus.NOT_FOUND:
                return IntegrationHealthResult(
                    ok=False, detail=f"Project '{project_key}' not found. Check the project key."
                )
            if project_resp.status_code != HTTPStatus.OK:
                return IntegrationHealthResult(
                    ok=False,
                    detail=f"Could not verify project '{project_key}': HTTP {project_resp.status_code}.",
                )

            return IntegrationHealthResult(
                ok=True, detail=f"Jira connected as {display}, project '{project_key}' verified."
            )
        if resp.status_code == HTTPStatus.UNAUTHORIZED:
            return IntegrationHealthResult(
                ok=False, detail="Jira credentials invalid. Check email and API token."
            )
        if resp.status_code == HTTPStatus.NOT_FOUND:
            return IntegrationHealthResult(
                ok=False, detail="Jira base URL not found. Check the URL."
            )
        return IntegrationHealthResult(
            ok=False, detail=f"Jira returned unexpected status {resp.status_code}."
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Jira validation failed: {err}")


def validate_servicenow_integration(
    *, instance_url: str, username: str, password: str
) -> IntegrationHealthResult:
    """Validate ServiceNow connectivity with a minimal authenticated table read."""
    from integrations.servicenow import build_servicenow_config, validate_servicenow_config

    outcome = validate_servicenow_config(
        build_servicenow_config(
            {"instance_url": instance_url, "username": username, "password": password}
        )
    )
    return IntegrationHealthResult(ok=outcome.ok, detail=outcome.detail)


def validate_discord_bot(*, bot_token: str) -> IntegrationHealthResult:
    """Validate a Discord bot token by calling the /users/@me endpoint."""
    try:
        resp = httpx.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Discord API unreachable: {err}")

    if resp.status_code == HTTPStatus.OK:
        username = resp.json().get("username", "unknown")
        return IntegrationHealthResult(ok=True, detail=f"Discord bot authenticated as @{username}.")
    if resp.status_code == HTTPStatus.UNAUTHORIZED:
        return IntegrationHealthResult(ok=False, detail="Discord bot token is invalid or revoked.")
    return IntegrationHealthResult(
        ok=False, detail=f"Discord API returned unexpected HTTP {resp.status_code}."
    )


def validate_rocketchat_webhook(*, webhook_url: str) -> IntegrationHealthResult:
    """Validate a Rocket.Chat incoming webhook with a non-posting reachability probe."""
    url = webhook_url.strip()
    if not url:
        return IntegrationHealthResult(ok=False, detail="Missing webhook_url.")

    try:
        resp = httpx.get(url, timeout=10, follow_redirects=False)
    except httpx.RequestError as err:
        return IntegrationHealthResult(
            ok=False, detail=f"Rocket.Chat webhook validation failed: {err}"
        )

    if resp.status_code == HTTPStatus.NOT_FOUND:
        return IntegrationHealthResult(
            ok=False, detail="Rocket.Chat webhook returned 404; the URL looks invalid."
        )
    if resp.status_code in {
        HTTPStatus.OK,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }:
        return IntegrationHealthResult(
            ok=True,
            detail=f"Rocket.Chat webhook endpoint reachable (HTTP {resp.status_code}) "
            "using a non-posting probe.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Rocket.Chat webhook probe returned unexpected HTTP {resp.status_code}.",
    )


def validate_rocketchat(
    *, server_url: str, auth_token: str, user_id: str
) -> IntegrationHealthResult:
    """Validate Rocket.Chat credentials by calling the /api/v1/me endpoint."""
    base = server_url.strip().rstrip("/")
    if not base:
        return IntegrationHealthResult(ok=False, detail="Missing server_url.")
    if not auth_token.strip() or not user_id.strip():
        return IntegrationHealthResult(ok=False, detail="Missing auth_token or user_id.")

    try:
        resp = httpx.get(
            f"{base}/api/v1/me",
            headers={"X-Auth-Token": auth_token, "X-User-Id": user_id},
            timeout=10,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Rocket.Chat API unreachable: {err}")

    if resp.status_code == HTTPStatus.OK:
        try:
            username = resp.json().get("username", "unknown")
        except Exception:
            username = "unknown"
        return IntegrationHealthResult(ok=True, detail=f"Rocket.Chat authenticated as @{username}.")
    if resp.status_code == HTTPStatus.UNAUTHORIZED:
        return IntegrationHealthResult(
            ok=False, detail="Rocket.Chat auth token or user ID is invalid or expired."
        )
    return IntegrationHealthResult(
        ok=False, detail=f"Rocket.Chat API returned unexpected HTTP {resp.status_code}."
    )
