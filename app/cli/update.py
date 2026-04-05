from __future__ import annotations

import os
import subprocess
import sys

from app.version import PACKAGE_NAME, get_version

_RELEASES_API = "https://api.github.com/repos/Tracer-Cloud/opensre/releases/latest"
_INSTALL_SCRIPT = "https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.sh"
_INSTALL_SCRIPT_PS1 = "https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.ps1"
_RELEASE_URL = "https://github.com/Tracer-Cloud/opensre/releases/tag/v{}"


def _releases_api_url() -> str:
    return os.getenv("OPENSRE_RELEASES_API_URL", _RELEASES_API)


def _fetch_latest_version() -> str:
    import httpx

    try:
        resp = httpx.get(_releases_api_url(), timeout=10, follow_redirects=True)
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise RuntimeError("request timed out") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise RuntimeError("GitHub API rate limit exceeded, try again later") from exc
        raise RuntimeError(f"GitHub API returned HTTP {exc.response.status_code}") from exc
    except httpx.ConnectError as exc:
        raise RuntimeError(
            "could not connect to GitHub — check your network or HTTPS_PROXY settings"
        ) from exc

    tag: str = resp.json().get("tag_name", "")
    return tag.lstrip("v")


def _is_update_available(current: str, latest: str) -> bool:
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return current != latest
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return current != latest


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_editable_install() -> bool:
    import importlib.metadata
    import json

    try:
        dist = importlib.metadata.distribution(PACKAGE_NAME)
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            info = json.loads(direct_url_text)
            return bool(info.get("dir_info", {}).get("editable", False))
    except (importlib.metadata.PackageNotFoundError, json.JSONDecodeError, OSError):
        return False
    return False


def _upgrade_via_pip() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME],
        check=False,
    )
    return result.returncode


def run_update(*, check_only: bool = False, yes: bool = False) -> int:
    # To skip this check in CI or automated environments, set OPENSRE_NO_UPDATE_CHECK=1.
    current = get_version()

    try:
        latest = _fetch_latest_version()
    except Exception as exc:
        print(f"  error: could not fetch latest version: {exc}", file=sys.stderr)
        return 1

    if not latest:
        print("  error: could not determine latest version from release data.", file=sys.stderr)
        return 1

    if not _is_update_available(current, latest):
        print(f"  opensre {current} is already up to date.")
        return 0

    print(f"  current: {current}")
    print(f"  latest:  {latest}")
    print(f"  release: {_RELEASE_URL.format(latest)}")

    if check_only:
        return 1

    if _is_binary_install():
        print("  automatic update is not supported for binary installs.")
        print("  to update, re-run the install script:")
        if _is_windows():
            print(f"    irm {_INSTALL_SCRIPT_PS1} | iex")
        else:
            print(f"    curl -fsSL {_INSTALL_SCRIPT} | bash")
        return 1

    if _is_editable_install():
        print("  warning: this is an editable install — upgrading will replace it with a release build.")

    if not yes:
        try:
            import questionary

            confirmed = questionary.confirm(f"  Update to {latest}?", default=True).ask()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1
        if not confirmed:
            print("  Cancelled.")
            return 0

    rc = _upgrade_via_pip()
    if rc == 0:
        print(f"  updated: {current} -> {latest}")
        print(f"  release notes: {_RELEASE_URL.format(latest)}")
    else:
        print(f"  pip upgrade failed (exit {rc}).", file=sys.stderr)
        print(
            f"  your install may be incomplete. Run: pip install --upgrade {PACKAGE_NAME}",
            file=sys.stderr,
        )
    return rc
