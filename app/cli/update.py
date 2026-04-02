from __future__ import annotations

import subprocess
import sys

from app.version import PACKAGE_NAME, get_version

_RELEASES_API = "https://api.github.com/repos/Tracer-Cloud/opensre/releases/latest"
_INSTALL_SCRIPT = "https://raw.githubusercontent.com/Tracer-Cloud/opensre/main/install.sh"


def _fetch_latest_version() -> str:
    import httpx

    resp = httpx.get(_RELEASES_API, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    tag: str = resp.json().get("tag_name", "")
    return tag.lstrip("v")


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _upgrade_via_pip() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME],
        check=False,
    )
    return result.returncode


def run_update(*, check_only: bool = False, yes: bool = False) -> int:
    current = get_version()

    try:
        latest = _fetch_latest_version()
    except Exception as exc:
        print(f"  error: could not fetch latest version: {exc}", file=sys.stderr)
        return 1

    if not latest:
        print("  error: could not determine latest version from release data.", file=sys.stderr)
        return 1

    if current == latest:
        print(f"  opensre {current} is already up to date.")
        return 0

    print(f"  current: {current}")
    print(f"  latest:  {latest}")

    if check_only:
        return 1

    if _is_binary_install():
        print("  automatic update is not supported for binary installs.")
        print("  to update, re-run the install script:")
        print(f"    curl -fsSL {_INSTALL_SCRIPT} | bash")
        return 1

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
    else:
        print(
            f"  pip upgrade failed. Try: pip install --upgrade {PACKAGE_NAME}",
            file=sys.stderr,
        )
    return rc
