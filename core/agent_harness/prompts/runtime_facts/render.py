"""Runtime facts rendered as quotable prompt text.

Renders the ``capture_runtime_facts()`` dict into verbatim-quotable prompt
strings plus anti-hallucination guidance. Phrased for quote-verbatim recall:
earlier prompt wording ("including the build marker if present") caused the
LLM to treat "build marker" as a slot name and hallucinate a value like ``0``
when the marker was empty.

Live facts (``now_iso``, uptime, disk, memory) are rendered separately from
session-static facts so the system/env prefix can stay cache-stable across
turns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from config.runtime_metadata import BLOCKED_INTROSPECTION_COMMANDS

_BLOCKED_COMMANDS = ", ".join(f"`{command}`" for command in BLOCKED_INTROSPECTION_COMMANDS)

_STATIC_GUIDANCE = (
    ". When the user asks which OpenSRE version is running, reply with the "
    "full version string above verbatim — including any parenthetical suffix. "
    "When the user asks what environment this process is running in, or for "
    "the host operating system, local timezone name, Python version, process "
    "id, parent process id, host/pod name, cloud provider or region, "
    "kubeconfig path, or which tools are installed, answer from the strings "
    "above directly, WITHOUT any tool call — these facts are authoritative "
    "and re-reading them through the sandbox wastes a round-trip. Quote the "
    "host operating system for environment questions; when no cloud provider "
    "was detected, say so — never invent AWS, GCP, Azure, or a region. Never "
    "run "
    f"{_BLOCKED_COMMANDS}, and never probe cloud instance metadata over the "
    "network. To list files in the scratchpad or another directory, "
    "use the Python execution sandbox with `pathlib.Path(...).iterdir()` — "
    "never `ls` or subprocess. Do NOT "
    "invent field names, values, or numbers not present above. Do NOT shell "
    "out or use subprocess — the Python execution sandbox blocks process "
    "spawning; when Python code you are already running for another reason "
    "needs these facts, read `inputs['opensre_runtime']` instead."
)

_LIVE_GUIDANCE = (
    ". When the user asks for the current date, time, day of the week, "
    "timezone offset embedded in the timestamp, uptime, disk or memory usage, "
    "answer from the strings above — do NOT guess a date/time from your "
    "training data. Never run "
    f"{_BLOCKED_COMMANDS}. Do NOT invent field names, values, or numbers not "
    "present above."
)

FactProducer = Callable[[Mapping[str, Any]], str | None]


def _clean_str(runtime: Mapping[str, Any], key: str) -> str:
    return str(runtime.get(key) or "").strip()


def _clean_int(runtime: Mapping[str, Any], key: str) -> int | None:
    value = runtime.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clean_number(runtime: Mapping[str, Any], key: str) -> float | None:
    value = runtime.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _str_fact(key: str, template: str) -> FactProducer:
    """Produce ``template.format(value)`` when ``key`` is a non-empty string."""

    def produce(runtime: Mapping[str, Any]) -> str | None:
        value = _clean_str(runtime, key)
        return template.format(value) if value else None

    return produce


def _version_line(runtime: Mapping[str, Any]) -> str | None:
    version = _clean_str(runtime, "opensre_version")
    if not version:
        return None
    build = _clean_str(runtime, "opensre_build")
    display = f"{version} ({build})" if build else version
    return f"OpenSRE version is {display}"


def _uptime_line(runtime: Mapping[str, Any]) -> str | None:
    uptime = _clean_number(runtime, "uptime_seconds")
    if uptime is None or uptime < 0:
        return None
    return f"process uptime is {uptime} seconds"


def _pid_line(runtime: Mapping[str, Any]) -> str | None:
    pid = _clean_int(runtime, "pid")
    if not pid:
        return None
    ppid = _clean_int(runtime, "ppid")
    parent = f", parent {ppid}" if ppid else ""
    return f"process id is {pid}{parent}"


def _pair_line(
    left_key: str,
    right_key: str,
    template: str,
) -> FactProducer:
    """Produce a line only when both numeric halves are present."""

    def produce(runtime: Mapping[str, Any]) -> str | None:
        left = _clean_number(runtime, left_key)
        right = _clean_number(runtime, right_key)
        if left is None or right is None:
            return None
        return template.format(left, right)

    return produce


def _tools_line(runtime: Mapping[str, Any]) -> str | None:
    tools = runtime.get("tools")
    if not isinstance(tools, dict):
        return None
    present = sorted(name for name, path in tools.items() if path)
    if not present:
        return None
    return f"installed tools on PATH are {', '.join(present)}"


def _host_os_line(runtime: Mapping[str, Any]) -> str | None:
    """Host OS — always stated when the key was probed."""
    if "os_family" not in runtime:
        return None
    family = _clean_str(runtime, "os_family")
    return f"host operating system is {family}" if family else None


def _cloud_line(runtime: Mapping[str, Any]) -> str | None:
    """Cloud identity, or an explicit statement that none was detected.

    Omitting the fact when detection finds nothing leaves a vacuum the model
    fills with a plausible guess (observed: a confident provider + region on a
    laptop). Stating the absence gives it something true to quote instead.
    """
    if "cloud_provider" not in runtime and "cloud_region" not in runtime:
        # Detection never ran (no runtime facts supplied) — asserting absence
        # here would put text in a block that must stay empty.
        return None
    provider = _clean_str(runtime, "cloud_provider")
    region = _clean_str(runtime, "cloud_region")
    if provider and region:
        return f"cloud provider is {provider} and cloud region is {region}"
    if provider:
        return f"cloud provider is {provider}; cloud region was not detected"
    if region:
        return f"cloud region is {region}; cloud provider was not detected"
    return (
        "no cloud provider or cloud region was detected, so this process is not "
        "running in a recognised cloud environment - say that rather than naming "
        "any provider or region"
    )


def _workspace_line(runtime: Mapping[str, Any]) -> str | None:
    """Which repo is “ours” — or an explicit unknown.

    Without this, “our GitHub stars” triggers follow-ups instead of resolving to
    the OpenSRE checkout / configured workspace repo.
    """
    if "workspace_repo" not in runtime:
        return None
    repo = _clean_str(runtime, "workspace_repo")
    if repo:
        return (
            f"this OpenSRE workspace default repo is {repo} — at session start, "
            f"treat “our” / “this repo” as {repo} unless the user names a different "
            "repository; session repository context can select another active repo, "
            "and this default does not limit how many repositories long-term memory "
            "can retain"
        )
    return (
        "no workspace git/GitHub repo was detected for this process — ask which "
        "repository the user means rather than assuming Tracer-Cloud/opensre or "
        "any other default"
    )


def _capability_warnings_line(runtime: Mapping[str, Any]) -> str | None:
    warnings = runtime.get("capability_warnings")
    if not isinstance(warnings, list) or not warnings:
        return None
    parts = [str(item).strip() for item in warnings if str(item).strip()]
    if not parts:
        return None
    return "".join(("capability warnings at boot: ", "; ".join(parts)))


# Order is part of the prompt contract — keep stable for snapshot/tests.
_STATIC_FACT_PRODUCERS: tuple[FactProducer, ...] = (
    _version_line,
    _str_fact("runtime_env", "runtime environment is {}"),
    _host_os_line,
    _str_fact("hostname", "host name is {}"),
    _str_fact("tz_name", "local timezone is {}"),
    _str_fact("python_version", "Python interpreter version is {}"),
    _pid_line,
    _cloud_line,
    _workspace_line,
    _tools_line,
    _capability_warnings_line,
    _str_fact("kubeconfig", "kubeconfig path is {}"),
    _str_fact("scratchpad_dir", "scratchpad directory is {}"),
)

_LIVE_FACT_PRODUCERS: tuple[FactProducer, ...] = (
    _str_fact("now_iso", "current time is {}"),
    _uptime_line,
    _pair_line("disk_used_percent", "disk_free_gb", "root disk is {}% used with {} GB free"),
    _pair_line(
        "memory_used_percent",
        "memory_available_gb",
        "memory is {}% used with {} GB available",
    ),
)


def _fact_lines(producers: tuple[FactProducer, ...], runtime: Mapping[str, Any]) -> list[str]:
    """One quotable string per available fact; empty slots are omitted."""
    return [line for produce in producers if (line := produce(runtime))]


def _render_facts(
    producers: tuple[FactProducer, ...],
    runtime: Mapping[str, Any],
    *,
    guidance: str,
) -> str:
    lines = _fact_lines(producers, runtime)
    if not lines:
        return ""
    return "".join(
        (
            "Runtime facts (quote the strings below EXACTLY when asked; do not "
            "paraphrase them into other field names): ",
            "; ".join(lines),
            guidance,
        )
    )


def render_static_runtime_facts(runtime: Mapping[str, Any]) -> str:
    """Session-stable runtime facts for the Environment / system prefix."""
    return _render_facts(_STATIC_FACT_PRODUCERS, runtime, guidance=_STATIC_GUIDANCE)


def render_live_runtime_facts(runtime: Mapping[str, Any]) -> str:
    """Per-turn live facts (time/uptime/disk/memory) for a late prompt block."""
    return _render_facts(_LIVE_FACT_PRODUCERS, runtime, guidance=_LIVE_GUIDANCE)


def build_live_runtime_facts_block(runtime: Mapping[str, Any]) -> str:
    """Late prompt section carrying live facts, or ``""`` when none."""
    body = render_live_runtime_facts(runtime)
    if not body:
        return ""
    return f"--- Live runtime facts ---\n{body}\n\n"


__all__ = [
    "build_live_runtime_facts_block",
    "render_live_runtime_facts",
    "render_static_runtime_facts",
]
