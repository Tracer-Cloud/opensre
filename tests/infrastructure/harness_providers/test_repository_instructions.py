"""Remote AGENTS.md reads behind the action prompt: cache, isolation, and time bounds.

Prompt assembly calls this on every turn, so a read must be cached, must never
cross from one GitHub grant to another, must give up after its timeout without
losing the answer, and must never raise.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from config.constants.repository_instructions import (
    REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS,
    REPOSITORY_INSTRUCTIONS_RETRY_SECONDS,
)
from infrastructure.harness_providers import (
    RemoteInstructions,
    RemoteInstructionsStatus,
    checkout_matches_repository,
    fetch_repository_instructions,
    register_repository_instructions_source,
    reset_harness_providers,
)
from infrastructure.harness_providers import repository_instructions as provider_module

_FOUND = RemoteInstructions(
    RemoteInstructionsStatus.FOUND, content=b"Run make test.", origin="Fake default branch"
)
_MISSING = RemoteInstructions(RemoteInstructionsStatus.MISSING, origin="Fake default branch")
_REFUSED = RemoteInstructions(RemoteInstructionsStatus.UNAVAILABLE, reason="Fake refused")


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


class _FakeSource:
    """Answers with ``result`` for every read; one credential per ``token`` key."""

    vendor = "fake"
    label = "Fake"

    def __init__(self, result: RemoteInstructions = _FOUND) -> None:
        self.result = result
        self.reads: list[str] = []
        self.checks: list[Path] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def checkout_matches(self, repository: str, root: Path) -> bool:
        _ = repository
        self.checks.append(root)
        return True

    def credential_scope(self, resolved_integrations: Mapping[str, Any]) -> str | None:
        token = resolved_integrations.get("token")
        return f"scope-{token}" if token else None

    def fetch(
        self, repository: str, resolved_integrations: Mapping[str, Any]
    ) -> RemoteInstructions:
        _ = repository
        self.reads.append(str(resolved_integrations.get("token")))
        self.entered.set()
        self.release.wait(10)
        return self.result


class _RaisingSource(_FakeSource):
    def checkout_matches(self, repository: str, root: Path) -> bool:
        _ = (repository, root)
        raise RuntimeError("git exploded")

    def fetch(
        self, repository: str, resolved_integrations: Mapping[str, Any]
    ) -> RemoteInstructions:
        _ = (repository, resolved_integrations)
        raise RuntimeError("GitHub exploded")


@pytest.fixture(autouse=True)
def _empty_provider() -> Iterator[None]:
    reset_harness_providers()
    yield
    reset_harness_providers()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(provider_module, "time", fake)
    return fake


def _install(source: _FakeSource) -> _FakeSource:
    register_repository_instructions_source(source)
    return source


def _fetch(repository: str, resolved: Mapping[str, Any]) -> RemoteInstructions:
    """A read with room for a loaded CI worker to schedule the reader thread."""
    return fetch_repository_instructions("fake", repository, resolved, timeout_seconds=10)


@pytest.mark.parametrize("answer", [_FOUND, _MISSING], ids=["found", "missing"])
def test_a_read_and_a_confirmed_absence_are_reused_for_the_ttl(
    clock: _Clock, answer: RemoteInstructions
) -> None:
    # Arrange
    source = _install(_FakeSource(answer))
    resolved = {"token": "a"}

    # Act
    first = _fetch("acme/payments", resolved)
    clock.now += REPOSITORY_INSTRUCTIONS_CACHE_TTL_SECONDS - 1
    within_ttl = _fetch("ACME/Payments", resolved)
    clock.now += 2
    after_ttl = _fetch("acme/payments", resolved)

    # Assert: one read per TTL, whatever the repository's casing.
    assert first == within_ttl == after_ttl == answer
    assert source.reads == ["a", "a"]


def test_a_failed_read_is_retried_after_the_short_ttl(clock: _Clock) -> None:
    # Arrange
    source = _install(_FakeSource(_REFUSED))
    resolved = {"token": "a"}

    # Act
    first = _fetch("acme/payments", resolved)
    clock.now += REPOSITORY_INSTRUCTIONS_RETRY_SECONDS - 1
    _fetch("acme/payments", resolved)
    source.result = _FOUND
    clock.now += 2
    recovered = _fetch("acme/payments", resolved)

    # Assert: an outage does not cost every turn a read, nor last ten minutes.
    assert first == _REFUSED
    assert recovered == _FOUND
    assert len(source.reads) == 2


def test_a_cached_read_never_reaches_a_session_with_another_credential() -> None:
    # Arrange: grant "a" can read the private file; grant "b" cannot see the repository.
    source = _install(_FakeSource(_FOUND))
    _fetch("acme/private", {"token": "a"})
    source.result = _REFUSED

    # Act
    other_grant = _fetch("acme/private", {"token": "b"})

    # Assert
    assert other_grant == _REFUSED
    assert source.reads == ["a", "b"]


def test_no_connection_answers_without_a_read() -> None:
    # Arrange
    source = _install(_FakeSource())

    # Act
    result = _fetch("acme/payments", {})

    # Assert
    assert result.status is RemoteInstructionsStatus.UNAVAILABLE
    assert result.reason == "no Fake connection"
    assert source.reads == []


def test_a_slow_read_times_out_then_serves_the_next_caller_from_one_shared_read() -> None:
    # Arrange: the read blocks until released.
    source = _install(_FakeSource(_FOUND))
    source.release.clear()
    resolved = {"token": "a"}
    first: list[RemoteInstructions] = []

    def _patient_caller() -> None:
        first.append(_fetch("acme/payments", resolved))

    waiter = threading.Thread(target=_patient_caller)
    waiter.start()
    assert source.entered.wait(10)

    # Act: a second caller gives up at its own timeout instead of starting a read.
    impatient = fetch_repository_instructions(
        "fake", "acme/payments", resolved, timeout_seconds=0.05
    )
    source.release.set()
    waiter.join(10)
    later = _fetch("acme/payments", resolved)

    # Assert
    assert impatient.status is RemoteInstructionsStatus.UNAVAILABLE
    assert impatient.reason == "Fake did not answer within 0.05 seconds"
    assert first == [_FOUND]
    assert later == _FOUND
    assert source.reads == ["a"]


def test_a_failing_source_never_raises_into_prompt_assembly(tmp_path: Path) -> None:
    # Arrange
    _install(_RaisingSource())

    # Act
    result = _fetch("acme/payments", {"token": "a"})
    matches = checkout_matches_repository("fake", "acme/payments", tmp_path)

    # Assert: a failed check never trusts the checkout.
    assert result.status is RemoteInstructionsStatus.UNAVAILABLE
    assert result.reason == "the Fake read failed"
    assert matches is False


def test_an_unregistered_vendor_is_unavailable_and_trusts_no_checkout(tmp_path: Path) -> None:
    # Act
    result = fetch_repository_instructions("gitlab", "group/project", {"token": "a"})
    matches = checkout_matches_repository("gitlab", "group/project", tmp_path)

    # Assert
    assert result.status is RemoteInstructionsStatus.UNAVAILABLE
    assert result.reason == "no gitlab support"
    assert matches is False


def test_a_checkout_check_is_reused_for_a_minute(clock: _Clock, tmp_path: Path) -> None:
    # Arrange
    source = _install(_FakeSource())

    # Act
    checkout_matches_repository("fake", "acme/payments", tmp_path)
    checkout_matches_repository("fake", "acme/payments", tmp_path)
    clock.now += 61
    checkout_matches_repository("fake", "acme/payments", tmp_path)

    # Assert: asking git costs a process, so not on every turn.
    assert source.checks == [tmp_path, tmp_path]
