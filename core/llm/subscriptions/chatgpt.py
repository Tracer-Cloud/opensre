"""Public ChatGPT OAuth registration, token renewal, and session revocation."""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

import httpx

from config.constants.chatgpt import (
    CHATGPT_AUTHORIZE_URL,
    CHATGPT_DYNAMIC_CLIENT,
    CHATGPT_RESOURCE,
    CHATGPT_SCOPES,
    CHATGPT_TOKEN_URL,
)
from config.llm_auth.records import save_provider_auth_record_values
from config.llm_auth.subscription_storage import read_store, subscription_lock, write_store
from core.llm.subscriptions.callback import OAuthCallback
from core.llm.subscriptions.identity import discovery, issuer_endpoint, validate_identity
from core.llm.subscriptions.types import SubscriptionAuth, SubscriptionLoginRequired


class _UnusableRefreshToken(SubscriptionLoginRequired):
    """The selected registration needs a new OAuth grant."""


def _clear_tokens(profile: dict[str, Any]) -> None:
    for key in (
        "access_token",
        "refresh_token",
        "id_token",
        "expires_at",
        "earliest_refresh_at",
    ):
        profile.pop(key, None)


def _metadata(store: dict[str, Any]) -> None:
    profiles = store["profiles"]
    active = str(store.get("active", ""))
    profile = profiles.get(active, {})
    connected = bool(profile.get("access_token"))
    labels = "; ".join(
        f"{key} ({value.get('email', 'ChatGPT account')})" for key, value in profiles.items()
    )
    save_provider_auth_record_values(
        "chatgpt",
        {
            "kind": "subscription",
            "source": "subscription" if connected else "none",
            "verified": "false",
            "stale": "false",
            "active_profile": active,
            "detail": f"{'Signed in' if connected else 'Signed out'}: {active}. Accounts: {labels}",
        },
    )


def _tokens(
    client: httpx.Client,
    data: dict[str, Any],
    client_id: str,
    *,
    previous: dict[str, Any] | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    previous = previous or {}
    scopes = str(data.get("scope", " ".join(previous.get("scopes", [])))).split()
    if not {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(scopes):
        raise SubscriptionLoginRequired(
            "ChatGPT plan usage was not authorized. Enable it during login."
        )
    if str(data.get("token_type", "")).lower() != "bearer":
        raise SubscriptionLoginRequired("ChatGPT returned an unsupported token type.")
    if not data.get("access_token") or not data.get("refresh_token"):
        raise SubscriptionLoginRequired(
            "ChatGPT did not return a renewable session. Run login again."
        )
    identity = previous
    if data.get("id_token"):
        identity = validate_identity(client, data["id_token"], client_id, nonce=nonce)
        if previous.get("subject") and identity["sub"] != previous["subject"]:
            raise SubscriptionLoginRequired(
                "ChatGPT identity changed. Add a separate account instead."
            )
    elif nonce is not None:
        raise SubscriptionLoginRequired("ChatGPT did not return an ID token.")
    lifetime = float(data.get("expires_in", 0))
    if not math.isfinite(lifetime) or lifetime <= 0:
        raise SubscriptionLoginRequired("ChatGPT returned an invalid token lifetime.")
    return {
        **previous,
        "client_id": client_id,
        "subject": identity.get("sub", previous.get("subject")),
        "issuer": identity.get("iss", previous.get("issuer")),
        "email": identity.get("email", previous.get("email", "ChatGPT account")),
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "id_token": data.get("id_token", previous.get("id_token", "")),
        "scopes": scopes,
        "expires_at": time.time() + lifetime,
        "earliest_refresh_at": data.get("earliest_refresh_at", 0),
    }


def _exchange(client: httpx.Client, form: dict[str, str]) -> dict[str, Any]:
    response = client.post(CHATGPT_TOKEN_URL, data={**form, "resource": CHATGPT_RESOURCE})
    if response.status_code != HTTPStatus.OK:
        try:
            error = response.json()
        except ValueError:
            error = {}
        code = error.get("error") if isinstance(error, dict) else None
        if isinstance(code, dict):
            code = code.get("code")
        if (
            form.get("grant_type") == "refresh_token"
            and isinstance(code, str)
            and code
            in {
                "invalid_grant",
                "invalid_refresh_token",
                "token_expired",
                "refresh_token_expired",
                "refresh_token_invalidated",
                "refresh_token_reused",
            }
        ):
            raise _UnusableRefreshToken(
                "ChatGPT session is no longer usable. Run `opensre auth login chatgpt` again."
            )
        raise SubscriptionLoginRequired(
            "ChatGPT token exchange failed. Run `opensre auth login chatgpt` again."
        )
    return dict(response.json())


def login(
    present: Callable[[str], None],
    *,
    select_model: Callable[[list[tuple[str, str]]], str],
    profile_id: str | None = None,
    new_account: bool = False,
) -> str:
    """Validate a browser login before activating it; retain registrations across logout."""
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        store.setdefault("host_id", f"urn:uuid:{uuid.uuid4()}")
        selected = profile_id or (
            store.get("pending", "")
            if new_account
            else store.get("active") or store.get("pending", "")
        )
        if (
            profile_id
            and profile_id not in store["profiles"]
            and profile_id != store.get("pending")
        ):
            raise SubscriptionLoginRequired(
                "Unknown ChatGPT account ID. See `opensre auth status chatgpt`."
            )
        previous = dict(store["profiles"].get(selected, {}))
        host_id = store["host_id"]
        write_store("chatgpt", store)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    client_id = selected or CHATGPT_DYNAMIC_CLIENT
    listener = OAuthCallback(state)

    def authorize(redirect_uri: str) -> str:
        values = {
            "client_id": client_id,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": CHATGPT_SCOPES,
            "resource": CHATGPT_RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if client_id == CHATGPT_DYNAMIC_CLIENT:
            values["agent_name_hint"] = "OpenSRE"
        # Hints are optional; omitting them keeps tokens out of displayed login URLs.
        return f"{CHATGPT_AUTHORIZE_URL}?{urlencode(values)}"

    callback = listener.receive(authorize, present)
    issued = callback.get("client_id", selected)
    if not issued or issued == CHATGPT_DYNAMIC_CLIENT or (selected and issued != selected):
        raise SubscriptionLoginRequired(
            "ChatGPT callback returned an unexpected client registration."
        )
    if not callback.get("code"):
        raise SubscriptionLoginRequired("ChatGPT callback did not contain an authorization code.")
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        if not selected and issued in store["profiles"]:
            raise SubscriptionLoginRequired(
                "This registration is already saved. Select its account ID to sign in again."
            )
        if not previous:
            store["pending"] = issued
        write_store("chatgpt", store)
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        data = _exchange(
            client,
            {
                "grant_type": "authorization_code",
                "client_id": issued,
                "code": callback["code"],
                "code_verifier": verifier,
                "redirect_uri": listener.redirect_uri,
            },
        )
        tokens = _tokens(client, data, issued, previous=previous, nonce=nonce)
        models = available_models(client, tokens["access_token"])
        selected_model = select_model(models)
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        store["profiles"][issued] = tokens
        store["active"] = issued
        store.pop("pending", None)
        write_store("chatgpt", store)
        _metadata(store)
    return selected_model


def available_models(client: httpx.Client, token: str) -> list[tuple[str, str]]:
    """List account-visible models in the server's order."""
    response = client.get(
        f"{CHATGPT_RESOURCE}/models", headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    models = [
        (str(item["slug"]), str(item.get("display_name") or item["slug"]))
        for item in response.json().get("models", [])
        if item.get("visibility") == "list" and item.get("slug")
    ]
    if not models:
        raise SubscriptionLoginRequired("This ChatGPT account has no available models.")
    return models


def resolve() -> SubscriptionAuth:
    """Resolve and refresh exactly the selected session; never start interactive login."""
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        selected = str(store.get("active", ""))
        profile = store["profiles"].get(selected, {})
        if not profile.get("access_token"):
            raise SubscriptionLoginRequired("Run `opensre auth login chatgpt` to sign in.")
        now = time.time()
        if float(profile.get("expires_at", 0)) <= now + 60:
            refresh_at = profile.get("earliest_refresh_at") or 0
            try:
                earliest = float(refresh_at)
            except ValueError:
                earliest = datetime.fromisoformat(
                    str(refresh_at).replace("Z", "+00:00")
                ).timestamp()
            if earliest > now:
                if float(profile.get("expires_at", 0)) <= now:
                    raise SubscriptionLoginRequired(
                        "ChatGPT token renewal is not available yet. Retry shortly."
                    )
            else:
                with httpx.Client(timeout=30, follow_redirects=False) as client:
                    try:
                        data = _exchange(
                            client,
                            {
                                "grant_type": "refresh_token",
                                "client_id": selected,
                                "refresh_token": profile["refresh_token"],
                            },
                        )
                    except _UnusableRefreshToken:
                        _clear_tokens(profile)
                        write_store("chatgpt", store)
                        _metadata(store)
                        raise
                    profile = _tokens(client, data, selected, previous=profile)
                store["profiles"][selected] = profile
                write_store("chatgpt", store)
        return SubscriptionAuth(profile["access_token"], CHATGPT_RESOURCE)


def verify() -> list[tuple[str, str]]:
    """Check token renewal and current account model access."""
    auth = resolve()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        return available_models(client, auth.token)


def logout() -> str:
    """Revoke and clear the selected session, retaining host and account mapping."""
    confirmed = False
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        profile = store["profiles"].get(store.get("active"), {})
        refresh = profile.get("refresh_token")
        if refresh:
            for attempt in range(2):
                try:
                    with httpx.Client(timeout=10, follow_redirects=False) as client:
                        endpoint = issuer_endpoint(discovery(client)["revocation_endpoint"])
                        response = client.post(
                            endpoint,
                            data={
                                "token": refresh,
                                "token_type_hint": "refresh_token",
                                "client_id": profile["client_id"],
                            },
                        )
                        confirmed = response.status_code == HTTPStatus.OK
                        if response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
                            break
                except (httpx.HTTPError, KeyError, ValueError, SubscriptionLoginRequired):
                    pass
                if attempt == 0:
                    time.sleep(0.5)
        _clear_tokens(profile)
        write_store("chatgpt", store)
        _metadata(store)
    detail = "ChatGPT session removed locally; saved account registration retained."
    if refresh and not confirmed:
        detail += " Remote revocation was not confirmed; disconnect OpenSRE in ChatGPT Settings."
    return detail
