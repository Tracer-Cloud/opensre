"""Tests for the runtime-facts section of the assistant environment block."""

from __future__ import annotations

import pytest

from core.agent_harness.prompts.grounding import build_environment_block


def _env_block(runtime: dict[str, object]) -> str:
    return build_environment_block(integrations=(), known=False, runtime=runtime)


def test_environment_block_includes_version_without_subprocess_hint() -> None:
    block = _env_block({"opensre_version": "9.9.9", "runtime_env": "development"})
    assert "OpenSRE version is 9.9.9" in block
    assert "runtime environment is development" in block
    assert "opensre --version" in block
    assert "subprocess" in block.lower()


def test_environment_block_renders_timezone_but_not_live_clock() -> None:
    """Timezone is session-static (env prefix); ``now_iso`` is live (late block)."""
    from core.agent_harness.prompts.runtime_facts import render_live_runtime_facts

    runtime = {
        "opensre_version": "0.1",
        "now_iso": "2026-07-11T14:30:12+02:00",
        "tz_name": "Europe/Berlin",
    }
    block = _env_block(runtime)
    assert "local timezone is Europe/Berlin" in block
    assert "current time is" not in block

    live = render_live_runtime_facts(runtime)
    assert "current time is 2026-07-11T14:30:12+02:00" in live
    assert "do NOT guess a date/time" in live.replace("Do NOT", "do NOT")


def test_environment_block_renders_python_process_and_tools_facts() -> None:
    """The process/tooling facts must land in the block as verbatim-quotable
    strings, each with a corresponding "do not shell out" instruction that
    names the reflex command the LLM would otherwise reach for."""
    from core.agent_harness.prompts.runtime_facts import render_live_runtime_facts

    runtime = {
        "opensre_version": "0.1",
        "python_version": "3.12.4",
        "pid": 12345,
        "ppid": 6789,
        "uptime_seconds": 42.5,
        "tools": {"kubectl": "/usr/local/bin/kubectl", "helm": "", "git": "/usr/bin/git"},
        "kubeconfig": "/home/me/.kube/config",
    }
    block = _env_block(runtime)
    assert "Python interpreter version is 3.12.4" in block
    assert "process id is 12345, parent 6789" in block
    assert "process uptime is" not in block  # live — late block
    assert "installed tools on PATH are git, kubectl" in block, block
    assert "helm" not in block  # not-present tools are filtered
    assert "kubeconfig path is /home/me/.kube/config" in block
    # Anti-guess instruction names the actual shell commands the LLM would reach
    # for, in backticked form so a stray substring can't satisfy the check.
    assert "`python --version`" in block
    assert "`kubectl version`" in block
    assert "`which`" in block
    assert "`ps`" in block
    assert "process uptime is 42.5 seconds" in render_live_runtime_facts(runtime)


def test_environment_block_renders_hostname_disk_memory_and_scratchpad() -> None:
    """Hostname/scratchpad stay in the env prefix; disk/memory are live."""
    from core.agent_harness.prompts.runtime_facts import render_live_runtime_facts

    runtime = {
        "opensre_version": "0.1",
        "hostname": "opensre-pod-7d9f",
        "disk_used_percent": 63.2,
        "disk_free_gb": 120.5,
        "memory_used_percent": 41.0,
        "memory_available_gb": 9.4,
        "scratchpad_dir": "/tmp",
    }
    block = _env_block(runtime)
    assert "host name is opensre-pod-7d9f" in block
    assert "root disk is" not in block
    assert "memory is" not in block
    assert "scratchpad directory is /tmp" in block
    assert "`hostname`" in block
    assert "`ls`" in block
    assert "iterdir" in block  # pathlib guidance for directory listings
    live = render_live_runtime_facts(runtime)
    assert "root disk is 63.2% used with 120.5 GB free" in live
    assert "memory is 41.0% used with 9.4 GB available" in live


def test_environment_block_renders_cloud_provider_and_region() -> None:
    """Cloud identity must be quotable, with guidance to use injected facts —
    without naming link-local metadata addresses (that plants the reflex)."""
    block = _env_block(
        {
            "opensre_version": "0.1",
            "cloud_provider": "aws",
            "cloud_region": "eu-central-1",
        }
    )
    assert "cloud provider is aws" in block
    assert "cloud region is eu-central-1" in block
    assert "never probe cloud instance metadata" in block
    assert "169.254.169.254" not in block
    assert "`curl`" in block
    assert "`ping`" in block
    assert "`nslookup`" in block


def test_environment_block_states_cloud_absence_when_not_deployed() -> None:
    """Local dev: absence must be stated, not left as a gap.

    Omitting the fact was the earlier approach; a live agent turn still answered
    with a confident provider and region on a laptop, so the vacuum was being
    filled by a guess. An explicit "not detected" gives the model a true fact to
    quote, and the instruction not to name one is attached to it.
    """
    block = _env_block({"opensre_version": "0.1", "cloud_provider": "", "cloud_region": ""})
    assert "no cloud provider or cloud region was detected" in block
    assert "not running in a recognised cloud environment" in block
    assert "rather than naming" in block
    # No value slot is rendered, so there is nothing that reads as a detected one.
    assert "cloud provider is" not in block
    assert "cloud region is" not in block


def test_environment_block_renders_host_os_for_environment_questions() -> None:
    """macOS/Linux must be quotable so 'what environment' does not invent AWS."""
    block = _env_block(
        {
            "opensre_version": "0.1",
            "os_family": "macOS",
            "cloud_provider": "",
            "cloud_region": "",
        }
    )
    assert "host operating system is macOS;" in block  # no version appended
    assert "what environment this process is running in" in block
    assert "never invent AWS" in block
    assert "`uname`" in block
    assert "no cloud provider or cloud region was detected" in block


def test_environment_block_does_not_coach_arbitrary_reachability_probing() -> None:
    """The always-on prompt must not steer the model toward reachability
    probing. allow_network has no destination allowlist — coaching sockets
    invites SSRF on user-supplied hosts. Shell reflexes stay on the deny-list."""
    block = _env_block({"opensre_version": "0.1"})
    assert "socket.create_connection" not in block
    assert "allow_network" not in block
    assert "`curl`" in block
    assert "`ping`" in block


def test_environment_block_omits_disk_memory_when_absent() -> None:
    """psutil failures degrade to absent keys; no partial/empty usage lines."""
    block = _env_block({"opensre_version": "0.1", "disk_used_percent": 63.2})
    # disk_free_gb missing → the disk line needs both halves.
    assert "root disk is" not in block
    assert "memory is" not in block


def test_environment_block_omits_installed_tools_line_when_none_present() -> None:
    """When every probed tool is absent, the block must not render an
    empty ``installed tools on PATH are `` line."""
    block = _env_block({"opensre_version": "0.1", "tools": {"kubectl": "", "helm": "", "git": ""}})
    assert "installed tools on PATH" not in block


def test_environment_block_omits_time_when_slot_empty() -> None:
    """Released wheels or pathological callers may pass no time; the block
    must not render an empty ``current time is`` line in that case."""
    block = _env_block({"opensre_version": "0.1", "now_iso": "", "tz_name": ""})
    assert "current time is" not in block
    assert "local timezone is" not in block


def test_environment_block_renders_build_marker_when_provided() -> None:
    """In a git checkout the runtime metadata carries an opensre_build marker;
    the env block should render it inline with the version so the LLM can
    quote both parts."""
    block = _env_block(
        {
            "opensre_version": "0.1",
            "opensre_build": "dev, v0.1.2026.7.11 @ abc1234",
            "runtime_env": "development",
        }
    )
    assert "OpenSRE version is 0.1 (dev, v0.1.2026.7.11 @ abc1234)" in block


def test_environment_block_omits_build_parens_when_marker_empty() -> None:
    """Released wheels report opensre_build=''; version renders bare."""
    block = _env_block(
        {
            "opensre_version": "0.1.2026.7.11",
            "opensre_build": "",
            "runtime_env": "production",
        }
    )
    assert "OpenSRE version is 0.1.2026.7.11" in block
    assert "OpenSRE version is 0.1.2026.7.11 (" not in block


def test_environment_block_instructs_verbatim_quoting_not_field_names() -> None:
    """Regression guard: an earlier version of the prompt said 'including the
    build marker if present', which caused the LLM to treat 'build marker' as
    a field name and hallucinate a value like '0' when the slot was empty. The
    prompt now instructs verbatim quoting and explicitly forbids inventing field
    names or numbers not in the block."""
    block = _env_block(
        {
            "opensre_version": "0.1",
            "opensre_build": "dev, v0.1.2026.7.11 @ abc1234",
            "runtime_env": "development",
        }
    )
    assert "verbatim" in block
    assert "Do NOT invent field names" in block
    assert "build marker" not in block, "the 'build marker' phrase was a hallucination sink"


def test_environment_block_stays_empty_without_runtime_facts() -> None:
    """No facts supplied means detection never ran — the block must stay empty.

    ``_build_environment_block`` returns "" for an unknown session; a cloud
    'not detected' line rendered from an empty mapping would break that.
    """
    assert _env_block({}) == "" or "cloud" not in _env_block({})


def test_environment_block_quotes_hosted_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.account_credits import AccountCredits, HostedCreditsKindValue, HostedCreditsRead

    def _hosted_read() -> HostedCreditsRead:
        return HostedCreditsRead(
            HostedCreditsKindValue.OK,
            AccountCredits(
                total=12_500,
                monthly=12_500,
                monthly_limit=100_000,
                top_up=0,
                resets_at=None,
                plan_id="team",
            ),
            "OpenSRE hosted credits.",
        )

    monkeypatch.setattr("core.llm.hosted_credits.account_llm_route", lambda: object())
    monkeypatch.setattr("core.llm.hosted_credits.cached_hosted_credits", _hosted_read)
    block = _env_block({"opensre_version": "0.1"})
    assert "OpenSRE hosted credits remaining are 12,500" in block
    assert "Hosted LLM requests are allowed." in block
