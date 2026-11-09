"""Availability checks shared by Bitbucket tools."""

from __future__ import annotations


def bitbucket_available_or_backend(sources: dict[str, dict]) -> bool:
    """Return whether credentials or a synthetic backend make Bitbucket available."""
    bitbucket = sources.get("bitbucket", {})
    if bitbucket.get("_backend"):
        return True
    return bool(
        bitbucket.get("connection_verified")
        and bitbucket.get("workspace")
        and bitbucket.get("username")
        and bitbucket.get("app_password")
    )
