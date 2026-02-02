"""LangGraph authentication and authorization for multi-tenant access control."""

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


def _get_user_field(ctx: Auth.types.AuthContext, key: str) -> str:
    user = cast(Any, ctx.user)
    if hasattr(user, "get"):
        value = user.get(key, "")
    else:
        value = getattr(user, key, "")
    return value if isinstance(value, str) else str(value)


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

    return cast(
        Auth.types.MinimalUserDict,
        {
            "identity": claims.sub,
            "is_authenticated": True,
            "org_id": claims.organization,
            "organization_slug": claims.organization_slug,
            "email": claims.email,
            "full_name": claims.full_name,
        },
    )


@auth.on.threads.create  # type: ignore[arg-type]
async def on_thread_create(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],
) -> dict[str, str]:
    """Add organization ownership when creating threads."""
    org_id = _get_user_field(ctx, "org_id")
    user_id = _get_user_field(ctx, "identity")

    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    return {"org_id": org_id}


@auth.on.threads.read
async def on_thread_read(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.threads.update
async def on_thread_update(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.threads.delete
async def on_thread_delete(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.threads.search
async def on_thread_search(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.threads.create_run
async def on_thread_create_run(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.assistants.create  # type: ignore[arg-type]
async def on_assistant_create(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],
) -> dict[str, str]:
    """Add organization ownership when creating assistants."""
    org_id = _get_user_field(ctx, "org_id")
    user_id = _get_user_field(ctx, "identity")

    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    return {"org_id": org_id}


@auth.on.assistants.read
async def on_assistant_read(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.assistants.update
async def on_assistant_update(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.assistants.delete
async def on_assistant_delete(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.assistants.search
async def on_assistant_search(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.crons.create  # type: ignore[arg-type]
async def on_cron_create(
    ctx: Auth.types.AuthContext,
    value: dict[str, Any],
) -> dict[str, str]:
    """Add organization ownership when creating crons."""
    org_id = _get_user_field(ctx, "org_id")
    user_id = _get_user_field(ctx, "identity")

    metadata = value.setdefault("metadata", {})
    metadata["org_id"] = org_id
    metadata["created_by"] = user_id

    return {"org_id": org_id}


@auth.on.crons.read
async def on_cron_read(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.crons.update
async def on_cron_update(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.crons.delete
async def on_cron_delete(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}


@auth.on.crons.search
async def on_cron_search(
    ctx: Auth.types.AuthContext,
    value: Any,  # noqa: ARG001
) -> dict[str, str]:
    return {"org_id": _get_user_field(ctx, "org_id")}
