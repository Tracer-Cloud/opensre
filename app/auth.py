"""LangGraph authentication and authorization for multi-tenant access control."""

from __future__ import annotations

from langgraph_sdk import Auth

from app.pipeline_assistant.jwt_auth import (
    JWTExpiredError,
    JWTInvalidIssuerError,
    JWTMissingClaimError,
    JWTVerificationError,
    verify_jwt_async,
)

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """Validate JWT token and extract user information."""
    if not authorization:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="Invalid Authorization header format. Expected: Bearer <token>"
        )

    token = parts[1]

    try:
        claims = await verify_jwt_async(token)
    except JWTExpiredError as e:
        raise Auth.exceptions.HTTPException(status_code=401, detail="JWT has expired") from e
    except JWTInvalidIssuerError as e:
        raise Auth.exceptions.HTTPException(status_code=401, detail=str(e)) from e
    except JWTMissingClaimError as e:
        raise Auth.exceptions.HTTPException(status_code=401, detail=str(e)) from e
    except JWTVerificationError as e:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail=f"JWT verification failed: {e}"
        ) from e

    return {
        "identity": claims.sub,
        "is_authenticated": True,
        "org_id": claims.organization,
        "organization_slug": claims.organization_slug,
        "email": claims.email,
        "full_name": claims.full_name,
    }


@auth.on.threads.create
async def on_thread_create(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.create.value,
) -> dict[str, str]:
    """Add organization ownership when creating threads."""
    org_id = ctx.user.get("org_id", "")
    user_id = ctx.user.identity

    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    return {"org_id": org_id}


@auth.on.threads.read
async def on_thread_read(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.read.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.update
async def on_thread_update(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.update.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.delete
async def on_thread_delete(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.delete.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.search
async def on_thread_search(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.search.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.threads.create_run
async def on_thread_create_run(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.create_run.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


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
    value: Auth.types.on.assistants.read.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.assistants.update
async def on_assistant_update(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.update.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.assistants.delete
async def on_assistant_delete(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.delete.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.assistants.search
async def on_assistant_search(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.assistants.search.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


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
    value: Auth.types.on.crons.read.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.crons.update
async def on_cron_update(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.update.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.crons.delete
async def on_cron_delete(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.delete.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}


@auth.on.crons.search
async def on_cron_search(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.search.value,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": ctx.user.get("org_id", "")}
