"""LangGraph authentication and authorization for multi-tenant access control.

This module implements JWT-based authentication and organization-scoped
authorization for LangGraph threads and resources.

JWT claims expected:
    - sub: User ID (e.g., "user_2zr9qkHIGPAP5K2GYNKMYLKv0F6")
    - organization: Organization ID (e.g., "org_33W1pou1nUzYoYPZj3OCQ3jslB2")
    - organization_slug: Organization slug (e.g., "tracer-bioinformatics")
    - email: User email
    - full_name: User's full name
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from langgraph_sdk import Auth

auth = Auth()


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification.

    Note: In production, you should verify the JWT signature using
    the issuer's public key. This implementation assumes the JWT
    has already been validated by an API gateway or load balancer.

    Args:
        token: JWT token string

    Returns:
        Decoded payload as dictionary

    Raises:
        ValueError: If token format is invalid
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format: expected 3 parts")

    payload_b64 = parts[1]
    # Add padding if needed
    payload_b64 += "=" * (4 - len(payload_b64) % 4)

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception as e:
        raise ValueError(f"Failed to decode JWT payload: {e}") from e


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """Validate JWT token and extract user information.

    This handler runs on every request. It validates the Authorization header
    and returns user information that will be available to authorization
    handlers and within graph execution via config["configurable"]["langgraph_auth_user"].

    Args:
        authorization: Authorization header value (e.g., "Bearer <token>")

    Returns:
        User information dict with identity, organization, and metadata

    Raises:
        Auth.exceptions.HTTPException: If authentication fails
    """
    if not authorization:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Missing Authorization header"
        )

    # Parse Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Expected: Bearer <token>"
        )

    token = parts[1]

    try:
        payload = decode_jwt_payload(token)
    except ValueError as e:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail=str(e)
        ) from e

    # Extract required claims
    user_id = payload.get("sub")
    org_id = payload.get("organization")

    if not user_id:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="JWT missing required 'sub' claim"
        )

    if not org_id:
        raise Auth.exceptions.HTTPException(
            status_code=401,
            detail="JWT missing required 'organization' claim"
        )

    # Return user information
    # This will be accessible via:
    # - ctx.user in authorization handlers
    # - config["configurable"]["langgraph_auth_user"] in graph nodes
    return {
        "identity": user_id,
        "is_authenticated": True,
        # Custom fields for organization scoping
        "org_id": org_id,
        "organization_slug": payload.get("organization_slug", ""),
        "email": payload.get("email", ""),
        "full_name": payload.get("full_name", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Authorization Handlers
# ─────────────────────────────────────────────────────────────────────────────
# All resources (threads, assistants, crons) are scoped to the organization.
# Users can only access resources belonging to their organization.


@auth.on.threads.create
async def on_thread_create(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.create.value,
) -> dict[str, str]:
    """Add organization ownership when creating threads.

    Stores org_id and user_id in thread metadata for access control.
    Returns filters that will be applied to subsequent reads.
    """
    org_id = ctx.user.get("org_id", "")
    user_id = ctx.user.identity

    # Set metadata on the thread being created
    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    # Return filter - only this org can see this thread
    return {"org_id": org_id}


@auth.on.threads.read
async def on_thread_read(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.read.value,
) -> dict[str, str]:
    """Filter thread reads to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.update
async def on_thread_update(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.update.value,
) -> dict[str, str]:
    """Filter thread updates to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.delete
async def on_thread_delete(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.delete.value,
) -> dict[str, str]:
    """Filter thread deletes to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.search
async def on_thread_search(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.search.value,
) -> dict[str, str]:
    """Filter thread searches to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.create_run
async def on_thread_create_run(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.create_run.value,
) -> dict[str, str]:
    """Filter run creation to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


# ─────────────────────────────────────────────────────────────────────────────
# Assistants Authorization (if using custom assistants)
# ─────────────────────────────────────────────────────────────────────────────


@auth.on.assistants.create
async def on_assistant_create(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.create.value,
) -> dict[str, str]:
    """Add organization ownership when creating assistants."""
    org_id = ctx.user.get("org_id", "")
    user_id = ctx.user.identity

    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    return {"org_id": org_id}


@auth.on.assistants.read
async def on_assistant_read(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.read.value,
) -> dict[str, str]:
    """Filter assistant reads to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.assistants.update
async def on_assistant_update(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.update.value,
) -> dict[str, str]:
    """Filter assistant updates to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.assistants.delete
async def on_assistant_delete(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.delete.value,
) -> dict[str, str]:
    """Filter assistant deletes to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.assistants.search
async def on_assistant_search(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.search.value,
) -> dict[str, str]:
    """Filter assistant searches to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


# ─────────────────────────────────────────────────────────────────────────────
# Crons Authorization (if using scheduled runs)
# ─────────────────────────────────────────────────────────────────────────────


@auth.on.crons.create
async def on_cron_create(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.create.value,
) -> dict[str, str]:
    """Add organization ownership when creating crons."""
    org_id = ctx.user.get("org_id", "")
    user_id = ctx.user.identity

    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    return {"org_id": org_id}


@auth.on.crons.read
async def on_cron_read(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.read.value,
) -> dict[str, str]:
    """Filter cron reads to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.crons.update
async def on_cron_update(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.update.value,
) -> dict[str, str]:
    """Filter cron updates to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.crons.delete
async def on_cron_delete(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.delete.value,
) -> dict[str, str]:
    """Filter cron deletes to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.crons.search
async def on_cron_search(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.search.value,
) -> dict[str, str]:
    """Filter cron searches to user's organization."""
    return {"org_id": ctx.user.get("org_id", "")}
