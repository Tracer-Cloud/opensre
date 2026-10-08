"""Which memories reach the prompt, in what order, and within which budgets.

The audit that motivated this: memories were picked by edit time, so a fresh
note about anything outranked the one memory about the repository the user was
asking about, and most memories never reached the model at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV, paths
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.domain.memory import (
    DEFAULT_RELEVANT_MEMORY_CHARS,
    DEFAULT_RELEVANT_MEMORY_ITEMS,
    memory_dir,
    render_prompt_index,
    render_relevant_memories,
    save_memory,
    search_memories_scored,
    select_relevant_memories,
)
from core.domain.memory.files import SUMMARY_FILENAME
from core.domain.memory.index import RELEVANT_BODY_CHARS


@pytest.fixture(autouse=True)
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "memory"
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(directory))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    return directory


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Successive ``updated`` stamps, one minute apart, so recency is deterministic."""
    start = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    issued: list[str] = []

    def _next() -> str:
        issued.append((start + timedelta(minutes=len(issued))).isoformat(timespec="seconds"))
        return issued[-1]

    monkeypatch.setattr("core.domain.memory.store._now_iso", _next)
    yield issued


def _save(slug: str, memory_type: str, description: str, body: str) -> None:
    assert save_memory(slug=slug, memory_type=memory_type, description=description, body=body)


def _slugs_in(text: str) -> list[str]:
    """Slugs of the entries in a RELEVANT MEMORIES block, in order."""
    return [
        line.split("] ", 1)[1].split(" — ", 1)[0]
        for line in text.splitlines()
        if line.startswith("[")
    ]


@pytest.mark.usefixtures("clock")
def test_the_matching_repository_beats_newer_unrelated_memories() -> None:
    _save(
        "repository-acme-payments",
        "repository",
        "acme/payments owns the checkout API",
        "checkout-api deploys from the release branch; canary runs in us-east-1.",
    )
    for topic in ("grafana-dashboards", "pagerduty-rotation", "vault-policies", "kafka-topics"):
        _save(topic, "infrastructure", f"Notes on {topic}", f"{topic} conventions for the team.")

    text = render_relevant_memories("why did the checkout-api deploy fail?")

    assert _slugs_in(text) == ["repository-acme-payments"]
    assert "canary runs in us-east-1" in text


@pytest.mark.usefixtures("clock")
def test_matching_personal_memories_lead_and_unmatched_ones_stay_out() -> None:
    _save(
        "pagerduty-escalation",
        "infrastructure",
        "Incident escalation goes to PagerDuty platform-oncall",
        "Incident escalation: page platform-oncall, then the incident commander.",
    )
    _save(
        "incident-report-format",
        "preference",
        "Incident reports as bullet lists with a timeline",
        "Write incident reports as bullet lists with a UTC timeline.",
    )
    _save("user-profile", "user", "Vaibhav on the platform team", "Name is Vaibhav.")

    ranked = select_relevant_memories("draft the incident report and the escalation path")

    assert [record.slug for record in ranked] == [
        "incident-report-format",
        "pagerduty-escalation",
    ]


@pytest.mark.usefixtures("clock")
def test_a_request_without_signal_or_matches_selects_nothing() -> None:
    _save(
        "repository-tracer-cloud-opensre",
        "repository",
        "Tracer-Cloud/opensre is the SRE assistant",
        "Default branch main.",
    )

    # The active repository alone is context, not a request.
    assert render_relevant_memories("ok", context=["Tracer-Cloud/opensre"]) == ""
    assert render_relevant_memories("what is the weather in lisbon") == ""


@pytest.mark.usefixtures("clock")
def test_context_terms_rank_below_the_request_itself() -> None:
    _save(
        "repository-tracer-cloud-opensre",
        "repository",
        "Tracer-Cloud/opensre is the SRE assistant",
        "Default branch main.",
    )
    _save("redis-eviction", "infrastructure", "Redis eviction policy", "Redis uses allkeys-lru.")

    ranked = select_relevant_memories("is redis eviction safe?", context=["Tracer-Cloud/opensre"])

    assert [record.slug for record in ranked] == [
        "redis-eviction",
        "repository-tracer-cloud-opensre",
    ]


@pytest.mark.usefixtures("clock")
def test_relevant_memories_stay_within_item_and_character_budgets() -> None:
    for index in range(8):
        _save(
            f"cluster-note-{index}",
            "infrastructure",
            f"Cluster note {index}",
            f"cluster {index} " + ("x" * 3_000),
        )

    text = render_relevant_memories("cluster")

    entries = _slugs_in(text)
    assert 1 <= len(entries) <= DEFAULT_RELEVANT_MEMORY_ITEMS
    assert len(text) <= DEFAULT_RELEVANT_MEMORY_CHARS
    for block in text.split("\n\n"):
        body = block.split("\n", 1)[1]
        assert len(body) <= RELEVANT_BODY_CHARS


@pytest.mark.usefixtures("clock")
def test_the_index_is_byte_stable_across_requests_until_a_memory_changes() -> None:
    _save("user-profile", "user", "Vaibhav on the platform team", "Name is Vaibhav.")
    _save("redis-eviction", "infrastructure", "Redis eviction policy", "allkeys-lru")
    first = render_prompt_index()

    # Showing memories records usage, which feeds index order; that must not
    # change the cached prompt prefix between turns.
    render_relevant_memories("is redis eviction safe?")
    render_relevant_memories("who am I, Vaibhav?")

    assert render_prompt_index() == first
    _save("kafka-topics", "infrastructure", "Kafka topic naming", "team.domain.event")
    assert render_prompt_index() != first


@pytest.mark.usefixtures("clock")
def test_the_index_lists_every_memory_personal_first_with_a_recall_tail() -> None:
    for index in range(30):
        description = f"Service {index} runbook: " + "escalation detail " * 5
        _save(f"service-{index:02d}", "infrastructure", description, "body")
    _save("deploy-window", "preference", "Deploy only before 16:00 UTC", "body")
    _save("user-profile", "user", "Vaibhav on the platform team", "body")

    lines = render_prompt_index().splitlines()

    assert lines[0] == "- [user] user-profile — Vaibhav on the platform team"
    assert lines[1] == "- [preference] deploy-window — Deploy only before 16:00 UTC"
    assert lines[-1].startswith("… and ") and lines[-1].endswith(" more (memory_recall)")
    assert sum(len(line) + 1 for line in lines[:-1]) <= 2_001


def test_the_consolidated_summary_sits_above_the_index(memory_home: Path) -> None:
    _save("user-profile", "user", "Vaibhav on the platform team", "body")
    (memory_dir() / SUMMARY_FILENAME).write_text("## User\nVaibhav, platform SRE.\n")

    rendered = render_prompt_index()

    assert rendered.index("Vaibhav, platform SRE.") < rendered.index("- [user] user-profile")


@pytest.mark.usefixtures("clock")
def test_demo_output_reaches_neither_prompt_block_nor_search() -> None:
    _save(
        "repository-davincios-opensre-ci-repair-demo-yx06",
        "repository",
        "davincios/opensre-ci-repair-demo-yx06 is a retained private local CI-repair demo "
        "with successfully repaired PR #1.",
        "Repair commit 21fe2fe; PR #1 passing.",
    )
    _save("reuse-demo-repositories", "preference", "Reuse an existing CI demo repository", "b")

    request = "which ci repair demo repository has the repaired PR?"
    assert "ci-repair-demo-yx06" not in render_prompt_index()
    assert "ci-repair-demo-yx06" not in render_relevant_memories(request)
    assert [record.slug for record, _ in search_memories_scored(request)] == [
        "reuse-demo-repositories"
    ]


@pytest.mark.usefixtures("clock")
def test_search_matches_any_query_word_and_ranks_by_score() -> None:
    _save("payments-flake", "investigation_learning", "Payments tests flake", "payments retries")
    _save("windows-runner", "infrastructure", "Windows runners are slow", "windows image")
    _save("unrelated", "infrastructure", "Grafana folders", "dashboards")

    scored = search_memories_scored("payments windows flake")

    assert [record.slug for record, _ in scored] == ["payments-flake", "windows-runner"]
    assert scored[0][1] > scored[1][1] > 0


@pytest.mark.usefixtures("clock")
def test_a_repository_with_a_short_name_is_found_by_its_identifier() -> None:
    """Words under three characters are dropped, so ``a/b`` used to leave no search term."""
    _save("repository-a-b", "repository", "a/b deploys from the release branch", "Owner: SRE")
    _save("repository-acme-api", "repository", "acme/api is the public API", "b team")

    assert [record.slug for record, _ in search_memories_scored("a/b")] == ["repository-a-b"]
    assert [record.slug for record in select_relevant_memories("how does a/b deploy?")] == [
        "repository-a-b"
    ]


def test_an_empty_store_renders_nothing() -> None:
    assert render_prompt_index() == ""
    assert render_relevant_memories("anything at all") == ""
    assert search_memories_scored("anything") == []


def test_one_members_memories_never_reach_anothers_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gateway members share an org root but keep separate memory folders."""
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(OPENSRE_MEMORY_DIR_ENV, raising=False)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    acme = Principal.org("org_acme")
    alice = StorageScope(principal=acme, actor=Actor(id="U_ALICE"))
    bob = StorageScope(principal=acme, actor=Actor(id="U_BOB"))
    with bound_storage_scope(alice):
        _save("prod-cluster", "infrastructure", "Prod cluster name", "Alice runs eks-alice.")
    with bound_storage_scope(bob):
        _save("prod-cluster", "infrastructure", "Prod cluster name", "Bob runs eks-bob.")

    with bound_storage_scope(alice):
        alice_view = render_relevant_memories("which prod cluster?")
        alice_dir = memory_dir()

    assert "eks-alice" in alice_view
    assert "eks-bob" not in alice_view
    assert (alice_dir / ".usage.json").is_file()
    with bound_storage_scope(bob):
        assert not (memory_dir() / ".usage.json").exists()
