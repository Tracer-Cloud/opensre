"""Probe whether sandbox capabilities actually work in this environment.

Distinct from :mod:`config.runtime_metadata.probes`, which only checks whether
binaries exist on ``PATH``. A tool can be present and still unusable.

Probes are cheap, local, and non-fatal. Failures become ``available=False``
with a short detail string.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Capability(StrEnum):
    """A thing the agent is told it can do."""

    PYTHON = "python execution"
    SHELL = "shell commands"
    FILE_READ = "file reading"
    FILE_GREP = "file search with grep"
    NETWORK = "network requests"


@dataclass(frozen=True)
class CapabilityStatus:
    """Whether one capability is usable, and why not when it is not."""

    capability: Capability
    available: bool
    detail: str


def _python_available() -> bool:
    """True when the sandbox can execute generated Python."""
    from infrastructure.safety.sandbox.runner import run_python_sandbox

    result = run_python_sandbox("print(1 + 1)")
    return (
        bool(getattr(result, "success", False))
        and str(getattr(result, "stdout", "")).strip() == "2"
    )


def _shell_available() -> bool:
    """True when a shell interpreter exists to run generated scripts."""
    return bool(shutil.which("bash") or shutil.which("sh"))


def _file_read_available() -> bool:
    """True when the process can read its own working directory."""
    cwd = Path.cwd()
    if not cwd.is_dir():
        return False
    try:
        # Empty trees are fine — we only need list/read permission.
        next(cwd.iterdir(), None)
        return True
    except OSError:
        return False


def _file_grep_available() -> bool:
    """True when grep exists to search local files."""
    return shutil.which("grep") is not None


def _network_available() -> bool:
    """True when the default sandbox policy allows outbound sockets.

    Probes under the normal runner defaults (``allow_network=False``). Opening a
    real remote connection is avoided — ``socket.socket()`` is enough to hit the
    injected policy block without depending on an external host.
    """
    from infrastructure.safety.sandbox.runner import run_python_sandbox

    result = run_python_sandbox("import socket; socket.socket()")
    return bool(getattr(result, "success", False))


#: Probes are held by name, not by reference, so they resolve through the module
#: at call time — a test substituting one is honoured.
_PROBES: tuple[tuple[Capability, str], ...] = (
    (Capability.PYTHON, "_python_available"),
    (Capability.SHELL, "_shell_available"),
    (Capability.FILE_READ, "_file_read_available"),
    (Capability.FILE_GREP, "_file_grep_available"),
    (Capability.NETWORK, "_network_available"),
)

_CAPABILITY_FIXES: dict[Capability, str] = {
    Capability.PYTHON: "make the process temp directory writable, then run `make install`.",
    Capability.SHELL: "install bash or sh and add it to PATH.",
    Capability.FILE_READ: "run OpenSRE from a readable working directory.",
    Capability.FILE_GREP: "install grep and add it to PATH.",
    Capability.NETWORK: (
        "use a configured integration for outbound HTTP; raw sandbox network access "
        "is blocked by default."
    ),
}


def probe_capabilities() -> dict[Capability, CapabilityStatus]:
    """Check every capability. Never raises — a failed probe reports unavailable."""
    results: dict[Capability, CapabilityStatus] = {}
    for capability, probe_name in _PROBES:
        try:
            available = bool(globals()[probe_name]())
            detail = "" if available else "probe returned unavailable"
        except Exception as err:
            available, detail = False, f"{type(err).__name__}: {err}"
        results[capability] = CapabilityStatus(
            capability=capability, available=available, detail=detail
        )
    return results


def unavailable_capability_warnings(
    results: dict[Capability, CapabilityStatus] | None = None,
) -> list[str]:
    """One human-readable warning per capability this environment withholds."""
    checked = probe_capabilities() if results is None else results
    return [
        f"{status.capability} is unavailable in this environment"
        + (f" ({status.detail})" if status.detail else "")
        + f" — the agent will not be able to use it. Fix: {_CAPABILITY_FIXES[status.capability]}"
        for status in checked.values()
        if not status.available
    ]


def boot_capability_warnings(
    *,
    include_path_facts: bool = True,
    installed_tools: dict[str, str] | None = None,
) -> list[str]:
    """Merge PATH tool gaps with sandbox usability gaps; preserve order, drop dupes.

    PATH facts come from :mod:`config.runtime_metadata.probes` (config must not
    import ``platform``). Pass ``installed_tools`` when the caller already probed
    PATH so this helper does not walk ``PATH`` again.
    """
    warnings: list[str] = []
    if include_path_facts:
        from config.runtime_metadata.probes import capability_warning_facts

        path_warnings = capability_warning_facts(installed_tools).get("capability_warnings") or []
        if isinstance(path_warnings, list):
            warnings.extend(str(item) for item in path_warnings if str(item).strip())
    warnings.extend(unavailable_capability_warnings())
    seen: set[str] = set()
    ordered: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        ordered.append(warning)
    return ordered


__all__ = [
    "Capability",
    "CapabilityStatus",
    "boot_capability_warnings",
    "probe_capabilities",
    "unavailable_capability_warnings",
]
