"""Runtime environment probes via pure Python (no subprocess).

Each probe replaces a shell command the agent would otherwise reach for:
timezone (``date``), hostname (``hostname``), interpreter version
(``python --version``), tool presence (``which``), kubeconfig
(``kubectl config view``), disk/memory (``df``/``free``/``top``), and cloud
identity (instance metadata endpoint).
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import sys
import time as _time
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from config.constants.runtime_metadata import (
    OPENSRE_ALLOW_NETWORK_ENV,
    WORKSPACE_REPO_ENV_KEYS,
)

# Tools the LLM commonly reflex-shells for. Presence-only surfaces so the agent
# can answer without invoking ``--version``, which the sandbox blocks.
_TOOLS_TO_PROBE = (
    "kubectl",
    "helm",
    "docker",
    "git",
    "python",
    "python3",
    "curl",
    "grep",
    "bash",
    "sh",
    "buzz",
)

_LOCALTIME_LINK = Path("/etc/localtime")

_HOSTNAME_FILE = Path("/etc/hostname")

# Values of CLOUD_PROVIDER for which AWS_REGION / AWS_DEFAULT_REGION describe
# the same deployment. Any other provider keeps its own region source.
_AWS_PROVIDER_NAMES: Final[frozenset[str]] = frozenset({"aws", "amazon"})

# ``sys.platform`` prefixes mapped to the OS name a user would recognise.
# Prefix-matched: Linux reports linux/linux2 and BSDs carry a version suffix.
_OS_FAMILY_BY_PLATFORM_PREFIX: Final[tuple[tuple[str, str], ...]] = (
    ("darwin", "macOS"),
    ("linux", "Linux"),
    ("win", "Windows"),
    ("freebsd", "FreeBSD"),
)


def local_tz_name() -> str:
    """Best-effort local timezone name — IANA (``Europe/Berlin``) when possible.

    Reads the ``/etc/localtime`` symlink target on macOS/Linux — the standard
    way the OS advertises which zone it's set to. Falls back to
    ``time.tzname`` short codes (``CET``, ``BST``) if the symlink can't be
    resolved (Windows, unusual OS config), and finally to ``UTC``.
    """
    try:
        if _LOCALTIME_LINK.is_symlink():
            target = os.readlink(_LOCALTIME_LINK)
            marker = "zoneinfo/"
            idx = target.rfind(marker)
            if idx >= 0:
                iana = target[idx + len(marker) :]
                if iana:
                    return iana
    except OSError:
        # Unreadable /etc/localtime: fall back to time.tzname/UTC below.
        pass
    return _time.tzname[0] if _time.tzname else "UTC"


def python_version_string() -> str:
    """Interpreter version as ``major.minor.patch`` from :mod:`sys`."""
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def installed_tools() -> dict[str, str]:
    """Map each probed tool name to its ``PATH`` location (empty if absent).

    :func:`shutil.which` walks ``PATH`` in pure Python. Version strings would
    require invoking the binary and are intentionally omitted; presence is what
    the agent needs to stop reflex-shelling for ``--version``.
    """
    return {tool: shutil.which(tool) or "" for tool in _TOOLS_TO_PROBE}


def pod_hostname() -> str:
    """Hostname via file read.

    ``/etc/hostname`` holds the pod name inside Kubernetes containers, which is
    the value SRE questions ("which pod am I in?") actually want. Falls back to
    :func:`socket.gethostname` on hosts without the file (macOS, some distros).
    """
    try:
        if _HOSTNAME_FILE.is_file():
            name = _HOSTNAME_FILE.read_text(encoding="utf-8").strip()
            if name:
                return name
    except OSError:
        # Unreadable /etc/hostname: fall back to the socket API below.
        pass
    try:
        return socket.gethostname()
    except OSError:
        return ""


def disk_memory_facts() -> dict[str, Any]:
    """Live disk and memory readings via psutil.

    Degrades to an empty dict if psutil misbehaves on an exotic platform —
    the facts are then simply absent rather than crashing prompt assembly.
    """
    try:
        import psutil

        disk = psutil.disk_usage("/")
        memory = psutil.virtual_memory()
    except Exception:
        return {}
    gib = 1024**3
    return {
        "disk_used_percent": round(disk.percent, 1),
        "disk_free_gb": round(disk.free / gib, 1),
        "memory_used_percent": round(memory.percent, 1),
        "memory_available_gb": round(memory.available / gib, 1),
    }


def host_os_facts() -> dict[str, str]:
    """Host OS family — ``macOS``, ``Linux``, ``Windows``.

    Always present so "what environment are you running in?" has a true local
    answer instead of a vacuum the model fills with a cloud guess.

    No version: ``platform.release()`` is the *kernel* release — on macOS the
    Darwin number (25.5.0), not the macOS version (26.5.2) — so publishing it
    as the OS release states a false fact in the block that exists to prevent
    them.

    ``sys.platform`` is a plain string, so it avoids importing the stdlib
    ``platform`` module just for ``platform.system()``.
    """
    identifier = sys.platform
    for prefix, family in _OS_FAMILY_BY_PLATFORM_PREFIX:
        if identifier.startswith(prefix):
            return {"os_family": family}
    return {"os_family": identifier or "unknown"}


def cloud_facts() -> dict[str, str]:
    """Cloud provider/region from deploy-time env vars — no metadata endpoint.

    ``CLOUD_PROVIDER`` / ``CLOUD_REGION`` are the canonical injection points
    (set at deploy time). ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` alone must
    **not** claim this process is running in AWS — those vars are routine on
    developer laptops (``.env.example`` ships ``AWS_REGION=us-east-1`` for the
    AWS integration). They may fill in the region only when the provider is
    itself AWS: an AWS region under ``CLOUD_PROVIDER=gcp`` would be a wrong
    location stated as authoritative runtime identity. Never calls the instance
    metadata service (IMDS).
    """
    provider = (os.environ.get("CLOUD_PROVIDER") or "").strip()
    region = (os.environ.get("CLOUD_REGION") or "").strip()
    if not region and provider.lower() in _AWS_PROVIDER_NAMES:
        region = (
            os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
        ).strip()
    return {"cloud_provider": provider, "cloud_region": region}


def kubeconfig_path() -> str:
    """Effective ``kubeconfig`` path from env, or the default under ``~/.kube``.

    Kept as a session-static fact so the agent can answer "which cluster
    config is loaded" without shelling to ``kubectl config view``.
    """
    override = (os.environ.get("KUBECONFIG") or "").strip()
    if override:
        # ``KUBECONFIG`` may be a ``:``-separated list; the first entry wins.
        first = override.split(os.pathsep, 1)[0]
        if first:
            return first
    default = Path.home() / ".kube" / "config"
    return str(default) if default.is_file() else ""


#: Hosts whose remotes shorten to a bare ``owner/repo`` identity.
_GITHUB_HOSTS: Final[frozenset[str]] = frozenset({"github.com", "www.github.com"})

#: scp-style remote: ``[user@]host:path`` (no scheme, so urlsplit cannot parse it).
_SCP_REMOTE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[^@/]+@)?(?P<host>[A-Za-z0-9._-]+):(?P<path>.+)$"
)


def _remote_host_and_path(text: str) -> tuple[str, str]:
    """Split a git remote into (host, path), or ``("", "")`` when unrecognised."""
    if "://" in text:
        parsed = urlsplit(text)
        return (parsed.hostname or "").lower(), parsed.path
    match = _SCP_REMOTE_RE.match(text)
    if match is None:
        return "", ""
    return match.group("host").lower(), match.group("path")


def _normalize_repo_identity(raw: str) -> str:
    """Shorten a GitHub remote to ``owner/repo``; leave anything else as-is.

    The host is compared exactly. A substring test would accept
    ``https://evil.test/github.com/attacker/repo`` and report ``attacker/repo``
    as this project's repository, and that fact reaches prompts and reports.
    """
    text = raw.strip()
    if not text:
        return ""
    if text.endswith(".git"):
        text = text[:-4]
    host, path = _remote_host_and_path(text)
    if host in _GITHUB_HOSTS and path.strip("/"):
        return path.strip("/")
    return text


def read_git_origin_identity(config_text: str) -> str:
    """Parse a ``.git/config`` body for the upstream, then origin, repository.

    In a fork checkout ``upstream`` is the original repository and ``origin``
    the fork, so the original is "ours".
    """
    lines = config_text.splitlines()
    for remote in ("upstream", "origin"):
        in_remote = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_remote = stripped.lower() == f'[remote "{remote}"]'
                continue
            if in_remote and stripped.lower().startswith("url"):
                _, _, value = stripped.partition("=")
                return _normalize_repo_identity(value)
    return ""


def _git_origin_from_config(repo_root: Path) -> str:
    config = repo_root / ".git" / "config"
    if not config.is_file():
        # Worktrees point .git at a file; skip deep resolution for the fact.
        return ""
    try:
        return read_git_origin_identity(config.read_text(encoding="utf-8"))
    except OSError:
        return ""


def workspace_identity_facts() -> dict[str, str]:
    """Which git/GitHub repo is “ours” for this OpenSRE process.

    Prefer an explicit env override, then the checkout's ``origin`` or
    ``upstream`` remote.
    Empty string means unknown — prompts must state that rather than invent
    Tracer-Cloud/opensre or the user’s employer repo.
    """
    for key in WORKSPACE_REPO_ENV_KEYS:
        value = _normalize_repo_identity(os.environ.get(key, ""))
        if value:
            return {"workspace_repo": value}
    # Walk up from cwd for a .git directory (dev checkouts).
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            origin = _git_origin_from_config(candidate)
            if origin:
                return {"workspace_repo": origin}
            break
    return {"workspace_repo": ""}


def capability_warning_facts(tools: dict[str, str] | None = None) -> dict[str, Any]:
    """Honest capability gaps the agent should surface at boot, not mid-turn.

    ``network_egress`` is false unless ``OPENSRE_ALLOW_NETWORK=1`` — matching the
    sandbox default (blocked egress). Shell and Python presence accept either
    supported executable name.
    """
    resolved = tools if tools is not None else installed_tools()
    missing = sorted(name for name, path in resolved.items() if not path)
    allow_network = (os.environ.get(OPENSRE_ALLOW_NETWORK_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    shell_available = bool(resolved.get("bash") or resolved.get("sh"))
    python_available = bool(resolved.get("python") or resolved.get("python3"))
    warnings: list[str] = []
    if "curl" in missing:
        warnings.append("curl is not on PATH — install curl and add it to PATH")
    if not shell_available:
        warnings.append(
            "no interactive shell (bash/sh) on PATH — install bash or sh and add it to PATH"
        )
    if "grep" in missing:
        warnings.append("grep is not on PATH — install grep and add it to PATH")
    if not allow_network:
        warnings.append(
            "network egress is blocked for sandboxed code by default — use a configured "
            "integration for outbound HTTP"
        )
    if not python_available:
        warnings.append(
            "python/python3 is not on PATH — install Python 3 and add python3 or python to PATH"
        )
    return {
        "network_egress": allow_network,
        "shell_available": shell_available,
        "capability_warnings": warnings,
    }


__all__ = [
    "capability_warning_facts",
    "cloud_facts",
    "disk_memory_facts",
    "host_os_facts",
    "installed_tools",
    "kubeconfig_path",
    "local_tz_name",
    "pod_hostname",
    "python_version_string",
    "read_git_origin_identity",
    "workspace_identity_facts",
]
