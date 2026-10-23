"""xAI device OAuth with a public CLI registration and isolated rotating sessions."""

from __future__ import annotations

import math
import os
import time
import uuid
from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from config.constants.grok import (
    GROK_DEVICE_GRANT,
    GROK_DISCOVERY_URL,
    GROK_ISSUER,
    GROK_OAUTH_CLIENT_ID,
    GROK_OAUTH_CLIENT_ID_ENV,
    GROK_OAUTH_REFERRER,
    GROK_RESOURCE,
    GROK_SCOPES,
)
from config.llm_auth.records import save_provider_auth_record_values
from config.llm_auth.subscription_storage import read_store, subscription_lock, write_store
from core.llm.subscriptions.types import SubscriptionAuth, SubscriptionLoginRequired


def _endpoint(value: Any) -> str:
    parsed = urlsplit(str(value))
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"auth.x.ai", "accounts.x.ai"}
        or parsed.fragment
        or parsed.username
    ):
        raise SubscriptionLoginRequired("xAI returned an unexpected OAuth endpoint.")
    return str(value)


def _discovery(client: httpx.Client) -> dict[str, Any]:
    response = client.get(GROK_DISCOVERY_URL)
    response.raise_for_status()
    data = dict(response.json())
    if data.get("issuer") != GROK_ISSUER:
        raise SubscriptionLoginRequired("xAI issuer discovery failed validation.")
    return data


def _positive(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise SubscriptionLoginRequired("xAI returned an invalid OAuth lifetime or interval.")
    return number


def _metadata(store: dict[str, Any]) -> None:
    active = str(store.get("active", ""))
    connected = bool(store["profiles"].get(active, {}).get("access_token"))
    labels = "; ".join(
        f"{key} ({profile.get('subject', 'xAI account')})"
        for key, profile in store["profiles"].items()
    )
    save_provider_auth_record_values(
        "grok",
        {
            "kind": "subscription",
            "source": "subscription" if connected else "none",
            "verified": "false",
            "stale": "false",
            "active_profile": active,
            "detail": f"{'Signed in' if connected else 'Signed out'}: {active}. Accounts: {labels}",
        },
    )


def _clear(profile: dict[str, Any]) -> None:
    for key in ("access_token", "refresh_token", "expires_at"):
        profile.pop(key, None)


def _tokens(data: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    scopes = str(data.get("scope", " ".join(previous.get("scopes", [])))).split()
    if not {"grok-cli:access", "api:access"}.issubset(scopes):
        raise SubscriptionLoginRequired("xAI did not grant subscription API access.")
    access = data.get("access_token")
    refresh = data.get("refresh_token", previous.get("refresh_token"))
    if (
        str(data.get("token_type", "")).lower() != "bearer"
        or not isinstance(access, str)
        or not access
        or not isinstance(refresh, str)
        or not refresh
    ):
        raise SubscriptionLoginRequired("xAI did not return a renewable bearer session.")
    return {
        **previous,
        "access_token": access,
        "refresh_token": refresh,
        "scopes": scopes,
        "expires_at": time.time() + _positive(data.get("expires_in", 0)),
    }


def _available_models(client: httpx.Client, token: str) -> list[tuple[str, str]]:
    response = client.get(f"{GROK_RESOURCE}/models", headers={"Authorization": f"Bearer {token}"})
    if response.status_code in {HTTPStatus.PAYMENT_REQUIRED, HTTPStatus.FORBIDDEN}:
        raise SubscriptionLoginRequired(
            "xAI denied Grok subscription model access. Check the signed-in account's "
            "active plan and remaining allowance at https://grok.com. "
            "No API-key fallback was used."
        )
    response.raise_for_status()
    result = [
        (str(item["id"]), str(item.get("name") or item["id"]))
        for item in response.json().get("data", [])
        if item.get("id")
    ]
    if not result:
        raise SubscriptionLoginRequired("This xAI subscription has no available models.")
    return result


def login(
    present: Callable[[str], None],
    *,
    select_model: Callable[[list[tuple[str, str]]], str],
    profile_id: str | None = None,
    new_account: bool = False,
    credential: str | None = None,
) -> str:
    """Authorize via RFC 8628; save only after identity and model selection succeed."""
    if credential is not None:
        raise SubscriptionLoginRequired("Grok subscriptions use OAuth, not XAI_API_KEY.")
    with subscription_lock("grok"):
        store = read_store("grok")
        selected = profile_id or ("" if new_account else str(store.get("active", "")))
        if selected and selected not in store["profiles"]:
            raise SubscriptionLoginRequired(
                "Unknown xAI account ID. See `opensre auth status grok`."
            )
        previous = dict(store["profiles"].get(selected, {}))
    configured_client = os.getenv(GROK_OAUTH_CLIENT_ID_ENV, "").strip()
    client_id = str(previous.get("client_id") or configured_client or GROK_OAUTH_CLIENT_ID)
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        metadata = _discovery(client)
        token_url = _endpoint(metadata["token_endpoint"])
        response = client.post(
            _endpoint(metadata["device_authorization_endpoint"]),
            data={
                "client_id": client_id,
                "scope": GROK_SCOPES,
                "referrer": GROK_OAUTH_REFERRER,
            },
        )
        response.raise_for_status()
        device = response.json()
        deadline = time.monotonic() + _positive(device["expires_in"])
        interval = _positive(device.get("interval", 5))
        # Build from the trusted verification URI, never a token-bearing complete URL.
        url = _endpoint(device["verification_uri"])
        present(f"{url}{'&' if '?' in url else '?'}{urlencode({'user_code': device['user_code']})}")
        data: dict[str, Any] = {}
        while time.monotonic() < deadline:
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            response = client.post(
                token_url,
                data={
                    "grant_type": GROK_DEVICE_GRANT,
                    "client_id": client_id,
                    "device_code": device["device_code"],
                },
            )
            data = dict(response.json())
            if response.status_code == HTTPStatus.OK:
                break
            error = data.get("error")
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                raise SubscriptionLoginRequired(
                    "xAI authorization failed or was denied. Run login again."
                )
        else:
            data = {}
        if not data.get("access_token"):
            raise SubscriptionLoginRequired("xAI authorization timed out. Run login again.")
        tokens = _tokens(data, {"client_id": client_id, "scopes": GROK_SCOPES.split()})
        identity = client.get(
            _endpoint(metadata["userinfo_endpoint"]),
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
            },
        )
        identity.raise_for_status()
        subject = identity.json().get("sub")
        if not isinstance(subject, str) or not subject:
            raise SubscriptionLoginRequired("xAI did not return an account identity.")
        if previous.get("subject") and previous["subject"] != subject:
            raise SubscriptionLoginRequired("xAI identity changed. Use --new-account instead.")
        tokens["subject"] = subject
        selected_model = select_model(_available_models(client, tokens["access_token"]))
    with subscription_lock("grok"):
        store = read_store("grok")
        selected = selected or str(uuid.uuid4())
        store["profiles"][selected] = tokens
        store["active"] = selected
        write_store("grok", store)
        _metadata(store)
    return selected_model


def resolve() -> SubscriptionAuth:
    """Serialize renewal for the active account; retain credentials on transient failure."""
    with subscription_lock("grok"):
        store = read_store("grok")
        selected = str(store.get("active", ""))
        profile = store["profiles"].get(selected, {})
        if not profile.get("access_token"):
            raise SubscriptionLoginRequired("Run `opensre auth login grok` to sign in.")
        if float(profile.get("expires_at", 0)) <= time.time() + 60:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                endpoint = _endpoint(_discovery(client)["token_endpoint"])
                response = client.post(
                    endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": profile["client_id"],
                        "refresh_token": profile["refresh_token"],
                    },
                )
                if response.status_code != HTTPStatus.OK:
                    try:
                        error = response.json().get("error")
                    except ValueError:
                        error = None
                    if error in {"invalid_grant", "invalid_token", "token_expired"}:
                        _clear(profile)
                        write_store("grok", store)
                        _metadata(store)
                    raise SubscriptionLoginRequired(
                        "xAI session renewal failed. Retry or run `opensre auth login grok`."
                    )
                tokens = _tokens(dict(response.json()), profile)
            store["profiles"][selected] = tokens
            write_store("grok", store)
            profile = tokens
        return SubscriptionAuth(profile["access_token"], GROK_RESOURCE)


def models() -> list[tuple[str, str]]:
    """Discover account-visible models with the active subscription token."""
    auth = resolve()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        return _available_models(client, auth.token)


def verify() -> list[tuple[str, str]]:
    """Check renewal and model access; inference entitlement is enforced by xAI."""
    return models()


def logout() -> str:
    """Revoke the selected grant when supported; always clear its local tokens."""
    confirmed = False
    with subscription_lock("grok"):
        store = read_store("grok")
        profile = store["profiles"].get(store.get("active"), {})
        refresh = profile.get("refresh_token")
        if refresh:
            try:
                with httpx.Client(timeout=10, follow_redirects=False) as client:
                    endpoint = _endpoint(_discovery(client).get("revocation_endpoint"))
                    response = client.post(
                        endpoint,
                        data={
                            "token": refresh,
                            "token_type_hint": "refresh_token",
                            "client_id": profile["client_id"],
                        },
                    )
                    confirmed = response.status_code == HTTPStatus.OK
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
                confirmed = False
        _clear(profile)
        write_store("grok", store)
        _metadata(store)
    detail = "Grok session removed locally; saved account registration retained."
    if refresh and not confirmed:
        detail += " Remote revocation was not confirmed; revoke OpenSRE access in your xAI account."
    return detail
