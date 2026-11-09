"""Environment validation helpers for agent clients."""

from __future__ import annotations

import os
from typing import Any

from config.constants.aws import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_SECRET_ACCESS_KEY_ENV,
    AWS_SESSION_TOKEN_ENV,
    AWS_STATIC_KEY_PAIR_ENV,
)


def _static_pair_is_half_set() -> bool:
    """One of the static key pair is in ``os.environ`` without the other."""
    first, second = AWS_STATIC_KEY_PAIR_ENV
    return bool(os.environ.get(first)) != bool(os.environ.get(second))


#: boto3 providers that reach the network: the ECS container-credentials
#: endpoint and EC2 instance metadata (169.254.169.254). On a machine that is
#: neither, they wait out connect timeouts before reporting "no credentials".
_NETWORK_CREDENTIAL_PROVIDERS: tuple[str, ...] = ("container-role", "iam-role")


def base_session(*, region: str | None = None, local_only: bool = False) -> Any:
    """A boto3 session for OpenSRE's own base identity, whatever supplies it.

    Order: a *complete* static pair resolved the OpenSRE way (process env, then
    the keyring / fallback file — so the id in ``.env`` pairs with the secret in
    the keyring), else boto3's normal chain (``~/.aws`` profiles, instance or
    task role, …). OpenSRE keeps ``AWS_SECRET_ACCESS_KEY`` keyring-only, so its
    ``.env`` legitimately loads ``AWS_ACCESS_KEY_ID`` alone into the process;
    boto3 reads the pair as one unit and would raise ``PartialCredentialsError``
    on that lone id instead of falling through, so with a half-pair the ``env``
    provider is dropped from the chain.

    *local_only* also drops the network providers (ECS / EC2 metadata). Use it
    for a pre-check that must answer instantly — a setup prompt deciding
    whether to warn — never for a real call, where an attached instance role
    is exactly the credential to find. Returns ``None`` when boto3 is missing.
    """
    try:
        import boto3
        import botocore.session
    except ImportError:
        return None
    from config.llm_credentials import resolve_env_credential

    access_key_id = resolve_env_credential(AWS_ACCESS_KEY_ID_ENV)
    secret_access_key = resolve_env_credential(AWS_SECRET_ACCESS_KEY_ENV)
    if access_key_id and secret_access_key:
        return boto3.session.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=resolve_env_credential(AWS_SESSION_TOKEN_ENV) or None,
            region_name=region,
        )
    to_remove: list[str] = []
    if _static_pair_is_half_set():
        to_remove.append("env")
    if local_only:
        to_remove.extend(_NETWORK_CREDENTIAL_PROVIDERS)
    if not to_remove:
        return boto3.session.Session(region_name=region)
    core = botocore.session.get_session()
    resolver = core.get_component("credential_provider")
    for name in to_remove:
        resolver.remove(name)
    return boto3.session.Session(botocore_session=core, region_name=region)


def _missing_env_error(
    missing: list[str], *, context: str, hint: str | None = None
) -> dict[str, Any]:
    error = f"Missing required environment variables for {context}: {', '.join(missing)}"
    payload: dict[str, Any] = {
        "success": False,
        "error": error,
        "missing_env": missing,
        "context": context,
    }
    if hint:
        payload["hint"] = hint
    return payload


def make_boto3_client(service: str):
    """Return a boto3 client for the given service using the configured AWS region."""
    session = base_session(region=os.getenv("AWS_REGION", "us-east-1"))
    if session is None:
        return None
    return session.client(service)  # type: ignore[call-overload]


def require_aws_credentials(*, context: str) -> dict[str, Any] | None:
    session = base_session()
    if session is None:
        return {
            "success": False,
            "error": "boto3 not available",
            "context": context,
        }
    if session.get_credentials() is not None:
        return None

    missing: list[str] = []
    if not os.getenv("AWS_ACCESS_KEY_ID"):
        missing.append("AWS_ACCESS_KEY_ID")
    if not os.getenv("AWS_SECRET_ACCESS_KEY"):
        missing.append("AWS_SECRET_ACCESS_KEY")

    hint = (
        "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN if needed), "
        "or configure an IAM role for this runtime."
    )
    if not missing:
        missing = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    return _missing_env_error(missing, context=context, hint=hint)
