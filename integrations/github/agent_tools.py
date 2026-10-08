"""Require app credentials at GitHub tool discovery and execution boundaries."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from config.constants.github import GITHUB_CONNECTION_ORIGIN_PARAM, GITHUB_CONNECTION_ORIGIN_TAG
from integrations.github.envelope import missing_token_envelope
from integrations.github.rest_token import github_rest_token, has_github_rest_token


def github_tool_available(
    predicate: Callable[[dict[str, dict]], bool] | None = None,
) -> Callable[[dict[str, dict]], bool]:
    """Keep a tool's scope requirements while requiring app authentication."""

    def available(sources: dict[str, dict]) -> bool:
        return has_github_rest_token(sources) and (predicate(sources) if predicate else True)

    return available


def github_tool_params(
    extractor: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
) -> Callable[[dict[str, dict]], dict[str, Any]]:
    """Inject authoritative app credentials even without a custom extractor."""

    def extract(sources: dict[str, dict]) -> dict[str, Any]:
        github = sources.get("github", {})
        return {
            **(extractor(sources) if extractor else {}),
            "github_token": github_rest_token(sources),
            GITHUB_CONNECTION_ORIGIN_PARAM: github.get(GITHUB_CONNECTION_ORIGIN_TAG, ""),
            "github_connection_id": github.get("connection_id", ""),
        }

    return extract


def require_webapp_github[**P](
    function: Callable[P, dict[str, Any]],
) -> Callable[P, dict[str, Any]]:
    """Refuse ambient credentials before a GitHub tool can perform work."""
    parameters = inspect.signature(function).parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    accepts_token = "github_token" in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )

    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        supplied = dict(kwargs)
        explicit = supplied.get("github_token")
        origin = supplied.pop(GITHUB_CONNECTION_ORIGIN_PARAM, None)
        token = github_rest_token(
            explicit=explicit if isinstance(explicit, str) else "",
            connection_origin=origin if isinstance(origin, str) else None,
        )
        if not token:
            scope = (
                f"{supplied.get('owner')}/{supplied.get('repo')}"
                if supplied.get("owner") and supplied.get("repo")
                else "the requested scope"
            )
            return missing_token_envelope(
                "Connect GitHub in the OpenSRE app, then refresh the connection before retrying.",
                blocked=f"GitHub work for {scope} cannot run",
            )
        if accepts_kwargs or GITHUB_CONNECTION_ORIGIN_PARAM in parameters:
            supplied[GITHUB_CONNECTION_ORIGIN_PARAM] = origin
        if accepts_token:
            supplied["github_token"] = token
        else:
            supplied.pop("github_token", None)
        if not accepts_kwargs and "github_connection_id" not in parameters:
            supplied.pop("github_connection_id", None)
        return cast(Callable[..., dict[str, Any]], function)(*args, **supplied)

    return run
