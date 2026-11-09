"""Tests for session runtime metadata: static facts, live capture, and merge."""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.runtime_metadata import (
    LIVE_FACT_KEYS,
    RUNTIME_INPUTS_KEY,
    STATIC_FACT_KEYS,
    build_runtime_metadata,
    capture_runtime_facts,
    merge_runtime_into_inputs,
)
from config.runtime_metadata import probes as probes_module
from config.version import get_opensre_version
from core.agent_harness.prompts.runtime_facts.render import render_static_runtime_facts
from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager


@pytest.fixture(autouse=True)
def _no_real_integration_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SessionCore, "warm_resolved_integrations", lambda _self, **_k: None)
    monkeypatch.setattr(SessionCore, "hydrate_configured_integrations", lambda _self: None)


def test_build_runtime_metadata_uses_importlib_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_ENV", "staging")
    meta = build_runtime_metadata()
    assert meta["opensre_version"] == get_opensre_version()
    assert meta["runtime_env"] == "staging"
    # opensre_build is populated in git checkouts (dev), empty in installed wheels.
    # Just assert the key exists and is a string — the value varies by env.
    assert isinstance(meta["opensre_build"], str)
    # tz_name is OS-dependent but always present.
    assert isinstance(meta["tz_name"], str) and meta["tz_name"]


def test_build_runtime_metadata_populates_process_and_python_facts() -> None:
    """Session-init process facts: python version, PID, PPID, tools
    manifest, kubeconfig — all pure-Python, none via subprocess."""
    import sys as _sys

    meta = build_runtime_metadata()
    assert meta["python_version"] == (
        f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
    )
    assert meta["pid"] == os.getpid()
    assert meta["ppid"] == os.getppid()
    assert isinstance(meta["tools"], dict)
    # Python itself must be on PATH (we're running under it right now).
    assert meta["tools"]["python"] or meta["tools"]["python3"], meta["tools"]
    assert isinstance(meta["kubeconfig"], str)


def test_build_runtime_metadata_reflects_kubeconfig_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``KUBECONFIG`` env var wins over the default under ``~/.kube/config``."""
    override = tmp_path / "mycluster.yaml"
    override.write_text("apiVersion: v1\n", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(override))
    assert build_runtime_metadata()["kubeconfig"] == str(override)


def test_build_runtime_metadata_kubeconfig_takes_first_of_colon_separated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``KUBECONFIG`` may hold multiple paths joined by ``os.pathsep``; the
    first is the merged base and is what we report."""
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", f"{first}{os.pathsep}{second}")
    assert build_runtime_metadata()["kubeconfig"] == str(first)


def test_build_runtime_metadata_populates_hostname_and_scratchpad() -> None:
    """Static filesystem facts: hostname via file/socket (never the `hostname`
    binary), scratchpad dir via tempfile — both pure Python."""
    meta = build_runtime_metadata()
    assert isinstance(meta["hostname"], str) and meta["hostname"]
    assert meta["scratchpad_dir"] == tempfile.gettempdir()


def test_pod_hostname_prefers_etc_hostname_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside Kubernetes, /etc/hostname holds the pod name — that file must win
    over socket.gethostname() so "which pod am I in?" gets the pod, not the node."""
    hostname_file = tmp_path / "hostname"
    hostname_file.write_text("opensre-pod-7d9f\n", encoding="utf-8")
    monkeypatch.setattr(probes_module, "_HOSTNAME_FILE", hostname_file)
    assert probes_module.pod_hostname() == "opensre-pod-7d9f"


def test_pod_hostname_falls_back_to_socket_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probes_module, "_HOSTNAME_FILE", tmp_path / "absent")
    assert probes_module.pod_hostname() == socket.gethostname()


def test_local_tz_name_reads_iana_from_localtime_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The IANA name (``Europe/Berlin``) is much clearer to the LLM than the
    OS short code (``CEST``/``BST``), which is ambiguous across regions.
    Reading ``/etc/localtime``'s symlink target is the standard way to get it."""
    zonefile = tmp_path / "usr" / "share" / "zoneinfo" / "Europe" / "Berlin"
    zonefile.parent.mkdir(parents=True)
    zonefile.write_bytes(b"")
    fake_link = tmp_path / "localtime"
    os.symlink(zonefile, fake_link)
    monkeypatch.setattr(probes_module, "_LOCALTIME_LINK", fake_link)
    assert probes_module.local_tz_name() == "Europe/Berlin"


def test_build_runtime_metadata_populates_build_marker_in_git_checkout() -> None:
    """In a git checkout (this test tree), opensre_build should include a SHA
    or release tag so the LLM can quote a precise build identifier without
    shelling out. The exact string varies with head, but must be non-empty."""
    meta = build_runtime_metadata()
    # This test runs from the opensre checkout, so .git exists → build marker
    # is populated. If someone ever runs the test suite from an installed
    # wheel, this test would need adjusting.
    assert meta["opensre_build"], "opensre_build should be populated in a git checkout"
    assert meta["opensre_build"].startswith("dev"), meta["opensre_build"]


def test_cloud_facts_read_deploy_time_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud identity comes from deploy-time env vars, never the metadata
    endpoint (which the sandbox network block would break anyway)."""
    monkeypatch.setenv("CLOUD_PROVIDER", "gcp")
    monkeypatch.setenv("CLOUD_REGION", "europe-west3")
    meta = build_runtime_metadata()
    assert meta["cloud_provider"] == "gcp"
    assert meta["cloud_region"] == "europe-west3"


def test_cloud_facts_aws_region_alone_does_not_claim_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Laptop ``.env`` often sets AWS_REGION for the AWS integration — that is
    not evidence this process is running inside AWS."""
    for var in ("CLOUD_PROVIDER", "CLOUD_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    assert probes_module.cloud_facts() == {"cloud_provider": "", "cloud_region": ""}


def test_cloud_facts_aws_region_fills_region_when_provider_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silos set CLOUD_PROVIDER=aws; AWS_REGION may supply the region."""
    for var in ("CLOUD_PROVIDER", "CLOUD_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    assert probes_module.cloud_facts() == {
        "cloud_provider": "aws",
        "cloud_region": "eu-central-1",
    }


def test_cloud_facts_empty_when_not_deployed_to_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local dev has no cloud identity — empty strings, never a guess."""
    for var in ("CLOUD_PROVIDER", "CLOUD_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    assert probes_module.cloud_facts() == {"cloud_provider": "", "cloud_region": ""}


def test_host_os_facts_are_always_present() -> None:
    """Environment questions need a true host OS, not a cloud vacuum."""
    # Arrange / Act.
    facts = probes_module.host_os_facts()
    meta = build_runtime_metadata()

    # Assert.
    assert facts["os_family"]
    assert meta["os_family"] == facts["os_family"]


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [("darwin", "macOS"), ("linux", "Linux"), ("linux2", "Linux"), ("win32", "Windows")],
)
def test_host_os_family_reports_the_product_not_the_kernel(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str
) -> None:
    """``Darwin`` is the kernel; a user on a MacBook is running macOS."""
    # Arrange.
    monkeypatch.setattr(probes_module.sys, "platform", sys_platform)

    # Act / Assert.
    assert probes_module.host_os_facts() == {"os_family": expected}


def test_host_os_facts_omit_a_version() -> None:
    """``platform.release()`` is the kernel version, not the OS version.

    On macOS it reports Darwin's number (25.5.0) while the OS is 26.5.2, so
    publishing it as the OS release states a false fact in the block whose
    whole purpose is preventing them.
    """
    # Arrange / Act / Assert.
    assert set(probes_module.host_os_facts()) == {"os_family"}


def test_cloud_facts_never_touch_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud identity must come from env alone — no instance metadata service
    (IMDS) call, which would hang or fail off-cloud and is blocked in the sandbox."""

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cloud_facts must not open sockets")

    monkeypatch.setattr(socket, "socket", _explode)
    monkeypatch.setattr(socket, "create_connection", _explode)
    monkeypatch.setenv("CLOUD_PROVIDER", "aws")
    monkeypatch.setenv("CLOUD_REGION", "eu-central-1")
    assert probes_module.cloud_facts() == {
        "cloud_provider": "aws",
        "cloud_region": "eu-central-1",
    }


def test_static_facts_match_the_contract_exactly() -> None:
    """The metadata dict and STATIC_FACT_KEYS must never drift — the prompt
    renderer, sandbox tool schema, and smoke validators all derive from the
    tuple. A new fact must land in both places in the same change."""
    assert set(build_runtime_metadata()) == set(STATIC_FACT_KEYS)


def test_live_facts_match_the_contract_exactly() -> None:
    """capture_runtime_facts adds exactly the LIVE_FACT_KEYS on a machine with
    working psutil (disk/memory keys are allowed to be absent only when psutil
    fails, which the degraded-path unit covers)."""
    meta = build_runtime_metadata()
    facts = capture_runtime_facts(metadata=meta)
    assert set(facts) - set(meta) == set(LIVE_FACT_KEYS)


def test_build_runtime_metadata_does_not_include_live_slots() -> None:
    """Live values must NOT live on the session-cached metadata: caching them
    at bootstrap would freeze the clock, uptime, and usage numbers."""
    meta = build_runtime_metadata()
    for key in LIVE_FACT_KEYS:
        assert key not in meta, f"{key} must be live, not cached"


def test_capture_runtime_facts_adds_fresh_now_iso() -> None:
    meta = build_runtime_metadata()
    facts = capture_runtime_facts(metadata=meta)
    assert facts["opensre_version"] == meta["opensre_version"]
    assert facts["tz_name"] == meta["tz_name"]
    assert facts["now_iso"], "now_iso should always be populated"
    # ISO 8601 with offset (e.g. 2026-07-11T14:30:12+02:00 or ...Z-form).
    assert "T" in facts["now_iso"]


def test_capture_runtime_facts_populates_uptime_seconds() -> None:
    facts = capture_runtime_facts()
    assert isinstance(facts["uptime_seconds"], float)
    assert facts["uptime_seconds"] >= 0.0


def test_capture_runtime_facts_populates_disk_and_memory_via_psutil() -> None:
    facts = capture_runtime_facts()
    assert 0.0 <= facts["disk_used_percent"] <= 100.0
    assert facts["disk_free_gb"] >= 0.0
    assert 0.0 <= facts["memory_used_percent"] <= 100.0
    assert facts["memory_available_gb"] >= 0.0


def test_capture_runtime_facts_uptime_grows_over_time() -> None:
    """Uptime is monotonic — a later capture must be >= an earlier one, and
    grow by roughly the elapsed sleep. Regression guard against accidentally
    caching a snapshot in metadata."""
    import time as _t

    first = capture_runtime_facts()["uptime_seconds"]
    _t.sleep(0.05)
    second = capture_runtime_facts()["uptime_seconds"]
    assert second > first
    assert second - first >= 0.04


def test_capture_runtime_facts_refreshes_now_between_calls() -> None:
    """Live time slot must actually be live — two calls one second apart
    should differ. Regression guard against accidentally caching now_iso on
    the session metadata."""
    import time as _t

    first = capture_runtime_facts()["now_iso"]
    _t.sleep(1.05)
    second = capture_runtime_facts()["now_iso"]
    assert first != second


def test_merge_runtime_into_inputs_does_not_overwrite_caller_key() -> None:
    custom = {"opensre_version": "custom"}
    merged = merge_runtime_into_inputs({"x": 1, RUNTIME_INPUTS_KEY: custom})
    assert merged["x"] == 1
    assert merged[RUNTIME_INPUTS_KEY] == custom


def test_session_bootstrap_populates_runtime_metadata() -> None:
    manager = SessionManager(
        store=InMemorySessionStore(),
        repo=SimpleNamespace(load_session=lambda _sid: None),
    )
    session = manager.create(hydrate_integrations=False, persistent_tasks=False, open_store=False)
    assert session.runtime_metadata["opensre_version"] == get_opensre_version()
    assert "runtime_env" in session.runtime_metadata
    assert "opensre_build" in session.runtime_metadata


def test_session_clear_repopulates_runtime_metadata() -> None:
    session = SessionCore()
    session.refresh_runtime_metadata()
    session.runtime_metadata = {}
    session.clear(rotate_identity=False)
    assert session.runtime_metadata["opensre_version"] == get_opensre_version()


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [("darwin", "macOS"), ("linux", "Linux"), ("linux2", "Linux"), ("win32", "Windows")],
)
def test_os_family_reports_the_product_not_the_kernel(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str
) -> None:
    """``Darwin`` is the kernel; a user on a MacBook is running macOS."""
    monkeypatch.setattr(probes_module.sys, "platform", sys_platform)
    assert probes_module.host_os_facts() == {"os_family": expected}


def test_runtime_facts_block_names_the_operating_system() -> None:
    """The agent must be able to answer "what environment are you running in?"."""
    runtime = build_runtime_metadata()
    block = render_static_runtime_facts(runtime)
    assert runtime["os_family"]
    assert f"host operating system is {runtime['os_family']}" in block
    # Exactly one OS line — do not also emit a duplicate operating_system fact.
    assert block.count("host operating system is ") == 1
