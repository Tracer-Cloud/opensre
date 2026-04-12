"""LangGraph authentication for multi-tenant access control."""

from __future__ import annotations

from typing import Any, cast

from langgraph_sdk import Auth

from app.auth.jwt_auth import (
    JWTExpiredError,
    JWTInvalidIssuerError,
    JWTMissingClaimError,
    JWTVerificationError,
    verify_jwt_async,
)

auth = Auth()


def _get_org_id(ctx: Auth.types.AuthContext) -> str:
    user = cast(Any, ctx.user)
    val = user.get("org_id", "") if hasattr(user, "get") else getattr(user, "org_id", "")
    return val if isinstance(val, str) else str(val)


@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    """Validate JWT and extract user info."""
    if not authorization:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid Authorization format")

    try:
        claims = await verify_jwt_async(parts[1])
    except JWTExpiredError as e:
        raise Auth.exceptions.HTTPException(status_code=401, detail="JWT expired") from e
    except (JWTInvalidIssuerError, JWTMissingClaimError, JWTVerificationError) as e:
        raise Auth.exceptions.HTTPException(status_code=401, detail=str(e)) from e

    return cast(
        Auth.types.MinimalUserDict,
        {
            "identity": claims.sub,
            "is_authenticated": True,
            "org_id": claims.organization,
            "organization_slug": claims.organization_slug,
            "email": claims.email,
            "full_name": claims.full_name,
            "token": parts[1],  # Raw JWT for downstream API calls
        },
    )


# Threads - no filtering to allow stateless runs
@auth.on.threads.create  # type: ignore[arg-type]
async def on_thread_create(ctx: Auth.types.AuthContext, value: dict[str, Any]) -> None:
    """Tag thread with org_id but don't filter."""
    md = value.setdefault("metadata", {})
    md["org_id"] = _get_org_id(ctx)


@auth.on.threads.read
async def on_thread_read(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> None:
    return None


@auth.on.threads.update
async def on_thread_update(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> None:
    return None


@auth.on.threads.delete
async def on_thread_delete(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> None:
    return None


@auth.on.threads.search
async def on_thread_search(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.threads.create_run
async def on_thread_create_run(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> None:
    return None


# Assistants - filter by org
@auth.on.assistants.create  # type: ignore[arg-type]
async def on_assistant_create(ctx: Auth.types.AuthContext, value: dict[str, Any]) -> dict[str, str]:
    md = value.setdefault("metadata", {})
    md["org_id"] = _get_org_id(ctx)
    return {"org_id": _get_org_id(ctx)}


@auth.on.assistants.read
async def on_assistant_read(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.assistants.update
async def on_assistant_update(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.assistants.delete
async def on_assistant_delete(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.assistants.search
async def on_assistant_search(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


# Crons - filter by org
@auth.on.crons.create  # type: ignore[arg-type]
async def on_cron_create(ctx: Auth.types.AuthContext, value: dict[str, Any]) -> dict[str, str]:
    md = value.setdefault("metadata", {})
    md["org_id"] = _get_org_id(ctx)
    return {"org_id": _get_org_id(ctx)}


@auth.on.crons.read
async def on_cron_read(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.crons.update
async def on_cron_update(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.crons.delete
async def on_cron_delete(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}


@auth.on.crons.search
async def on_cron_search(ctx: Auth.types.AuthContext, value: Any) -> dict[str, str]:  # noqa: ARG001
    return {"org_id": _get_org_id(ctx)}
