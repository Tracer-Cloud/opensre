"""Compare the running version with the latest main-build release."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
from http import HTTPStatus

from config.constants import RELEASES_API_URL_ENV, UV_RUN_RECURSION_DEPTH_ENV

MAIN_BUILD_RELEASE_URL = "https://github.com/Tracer-Cloud/opensre/releases/tag/main-build"
_MAIN_BUILD_RELEASE_API = (
    "https://api.github.com/repos/Tracer-Cloud/opensre/releases/tags/main-build"
)
_VERSION_FROM_RELEASE_BODY = re.compile(r"Version:\s*`([^`\n]+)`", re.IGNORECASE)
_MAIN_BUILD_SHA_SUFFIX = re.compile(r"\+main\.([0-9a-f]+)$", re.IGNORECASE)


def _main_build_release_api_url() -> str:
    return os.getenv(RELEASES_API_URL_ENV, _MAIN_BUILD_RELEASE_API)


def extract_main_build_version(release_body: str) -> str:
    """The version string recorded in a main-build release body, or empty."""
    match = _VERSION_FROM_RELEASE_BODY.search(release_body)
    if not match:
        return ""
    return match.group(1).strip()


def extract_main_build_sha(version: str) -> str | None:
    """The commit SHA suffix of a main-build version, or None for tagged releases."""
    match = _MAIN_BUILD_SHA_SUFFIX.search(version.strip())
    if not match:
        return None
    return match.group(1).lower()


def fetch_latest_version() -> str:
    """Latest main-build version from the GitHub release; raises RuntimeError on failure."""
    import httpx

    try:
        resp = httpx.get(_main_build_release_api_url(), timeout=10, follow_redirects=True)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise RuntimeError("request timed out") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise RuntimeError("GitHub API rate limit exceeded, try again later") from exc
        raise RuntimeError(f"GitHub API returned HTTP {exc.response.status_code}") from exc
    except httpx.ConnectError as exc:
        raise RuntimeError(
            "could not connect to GitHub — check your network or HTTPS_PROXY settings"
        ) from exc

    body = resp.json().get("body") or ""
    return extract_main_build_version(body)


def is_update_available(current: str, latest: str) -> bool:
    """True when ``latest`` is newer than ``current``; never reports a downgrade."""
    current_main_sha = extract_main_build_sha(current)
    latest_main_sha = extract_main_build_sha(latest)
    if current_main_sha is not None and latest_main_sha is not None:
        # Same-day main rebuilds share a calendar prefix; compare commit SHAs
        # instead of PEP 440 local segments (hex SHAs are not ordered chronologically).
        return current_main_sha != latest_main_sha

    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return current != latest
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return current != latest


def is_editable_install() -> bool:
    """True when ``opensre`` was installed editable (a git checkout)."""
    try:
        dist = importlib.metadata.distribution("opensre")
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            info = json.loads(direct_url_text)
            return bool(info.get("dir_info", {}).get("editable", False))
    except (importlib.metadata.PackageNotFoundError, json.JSONDecodeError, OSError):
        return False
    return False


def development_install_doctor_version_detail(current: str) -> str | None:
    """The doctor line for a local checkout (editable install or ``uv run``), else None."""
    labels: list[str] = []
    if is_editable_install():
        labels.append("editable install")
    # uv sets this on the Python process when invoked via `uv run …`.
    if os.environ.get(UV_RUN_RECURSION_DEPTH_ENV) is not None:
        labels.append("uv run")
    if not labels:
        return None
    ctx = " + ".join(labels)
    return f"{current} ({ctx}; skipped comparing to latest main build)"


__all__ = [
    "MAIN_BUILD_RELEASE_URL",
    "development_install_doctor_version_detail",
    "extract_main_build_sha",
    "extract_main_build_version",
    "fetch_latest_version",
    "is_editable_install",
    "is_update_available",
]
