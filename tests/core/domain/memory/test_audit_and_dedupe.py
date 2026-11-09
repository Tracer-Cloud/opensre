"""Near-duplicates, the bug class that filled real stores, and the audit that measures it.

A real 33-memory store held one fact ("interested in GitHub star metrics")
in five files: extraction only updated a memory when the model reused its
exact name, and upkeep merged only repository memories. These pin the fold
at write time, the merge in upkeep, and what the audit reports.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.domain.memory import (
    MemoryType,
    audit_memories,
    consolidate_memories,
    list_memories,
    load_memory,
    save_memory,
    save_memory_merging_duplicates,
)
from core.domain.memory.models import MemoryRecord
from core.domain.memory.usage import MemoryUsage

_NOW = datetime(2026, 10, 19, tzinfo=UTC)


def _record(
    slug: str,
    description: str,
    *,
    memory_type: MemoryType = MemoryType.PREFERENCE,
    body: str = "note",
    updated: str = "2026-10-01T00:00:00+00:00",
    verified: bool | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        slug=slug,
        memory_type=memory_type,
        description=description,
        created_at=updated,
        updated_at=updated,
        body=body,
        verified=verified,
    )


def test_a_paraphrased_fact_is_folded_into_the_memory_it_repeats() -> None:
    save_memory(
        slug="github-star-metrics-interest",
        memory_type="preference",
        description="User is interested in GitHub repository star metrics for Tracer-Cloud/opensre.",
        body="Wants star counts for Tracer-Cloud/opensre.",
    )

    stored = save_memory_merging_duplicates(
        slug="likes-github-repo-star-metrics",
        memory_type="preference",
        description="User wants GitHub repo star metrics for Tracer-Cloud/opensre.",
        body="Wants star counts for Tracer-Cloud/opensre.\nAlso wants weekly deltas.",
        source="user",
        evidence="show me the stars",
        verified=True,
    )

    assert stored is not None
    record, outcome = stored
    assert outcome == "merged"
    assert [r.slug for r in list_memories()] == ["github-star-metrics-interest"]
    # Old notes kept, the new line appended once, the better provenance adopted.
    assert record.body.count("Wants star counts") == 1
    assert "weekly deltas" in record.body
    assert record.verified is True


def test_a_repeat_that_adds_nothing_writes_nothing() -> None:
    save_memory(
        slug="prefers-concise-reports",
        memory_type="preference",
        description="User prefers concise incident reports with bullet points.",
        body="Keep reports short.",
    )
    before = load_memory("prefers-concise-reports")

    stored = save_memory_merging_duplicates(
        slug="concise-incident-report-preference",
        memory_type="preference",
        description="User prefers concise incident reports, bullet points.",
        body="Keep reports short.",
    )

    assert stored is not None and stored[1] == "unchanged"
    assert load_memory("prefers-concise-reports") == before


def test_different_facts_and_different_repositories_are_never_folded() -> None:
    save_memory(
        slug="repository-acme-payments",
        memory_type="repository",
        description="Primary repo for acme/payments CI work.",
        body="# acme/payments",
    )
    save_memory(
        slug="prod-cluster",
        memory_type="infrastructure",
        description="Production EKS cluster is eks-prod-1 in us-east-1.",
        body="eks-prod-1",
    )

    repo = save_memory_merging_duplicates(
        slug="repository-acme-ledger",
        memory_type="repository",
        description="Primary repo for acme/ledger CI work.",
        body="# acme/ledger",
    )
    infra = save_memory_merging_duplicates(
        slug="staging-cluster",
        memory_type="infrastructure",
        description="Staging EKS cluster is eks-stg-2 in eu-west-1.",
        body="eks-stg-2",
    )

    assert repo is not None and repo[1] == "created"
    assert infra is not None and infra[1] == "created"
    assert len(list_memories()) == 4


def test_upkeep_merges_same_type_duplicates_and_archives_the_rest() -> None:
    save_memory(
        slug="plain-english-overview-preference",
        memory_type="preference",
        description="User wants plain-English overviews of tools before technical detail.",
        body="Plain English first.",
    )
    save_memory(
        slug="wants-plain-english-tool-overviews",
        memory_type="preference",
        description="User wants plain-English tool overviews before technical detail.",
        body="Lead with a plain-English summary.",
    )

    result = consolidate_memories(now=_NOW)

    assert result.ran
    [kept] = list_memories()
    assert len(result.merged) == 1 and result.merged[0][1] == kept.slug
    assert "Plain English first." in kept.body
    assert "Lead with a plain-English summary." in kept.body


def test_audit_reports_the_defects_seen_in_real_stores_without_any_body() -> None:
    records = [
        _record("star-metrics-interest", "User is interested in GitHub repo star metrics."),
        _record("likes-repo-star-metrics", "User likes GitHub repo star metrics."),
        _record(
            "openai-provider-configured",
            "User completed OpenSRE onboarding and set the provider to OpenAI.",
            body="SECRET-ISH BODY TEXT",
        ),
        _record(
            "prod-cluster",
            "Production cluster is eks-prod-1.",
            memory_type=MemoryType.INFRASTRUCTURE,
            updated="2026-01-01T00:00:00+00:00",
        ),
        _record("user-name", "User's name is Ada.", memory_type=MemoryType.USER, verified=True),
    ]

    audit = audit_memories(
        records,
        usage={"prod-cluster": MemoryUsage(use_count=1, last_used_at="2026-02-01T00:00:00+00:00")},
        now=_NOW,
    )

    rules = {(finding.rule, finding.slug) for finding in audit.findings}
    # One of the pair is kept; only the other is reported.
    assert sum(rule == "duplicate" for rule, _slug in rules) == 1
    assert ("transient", "openai-provider-configured") in rules
    assert ("unverified", "prod-cluster") in rules
    assert ("stale", "prod-cluster") in rules
    # Personal facts never go stale and a stated name is not a defect.
    assert not any(slug == "user-name" for _rule, slug in rules)
    assert audit.score < 100
    # What leaves the gateway for admins carries names and counts, never a body.
    assert "SECRET-ISH" not in str(audit.to_dict())


def test_a_recently_used_fact_is_not_stale_even_when_old() -> None:
    old = _record(
        "prod-cluster",
        "Production cluster is eks-prod-1.",
        memory_type=MemoryType.INFRASTRUCTURE,
        updated=(_NOW - timedelta(days=400)).isoformat(),
        verified=True,
    )

    audit = audit_memories(
        [old],
        usage={"prod-cluster": MemoryUsage(use_count=3, last_used_at=_NOW.isoformat())},
        now=_NOW,
    )

    assert audit.findings == ()
    assert audit.score == 100


def test_facts_that_differ_only_in_an_identifier_or_a_negation_are_never_folded() -> None:
    # Review finding: search tokenization dropped "1"/"2", so two clusters in
    # two regions read as one fact and their bodies were folded together.
    save_memory(
        slug="prod-cluster-east",
        memory_type="infrastructure",
        description="Production EKS cluster eks-prod-1 runs in us-east-1.",
        body="eks-prod-1 / us-east-1",
    )
    save_memory(
        slug="pr-flow",
        memory_type="preference",
        description="User wants every fix to go through a pull request.",
        body="PR flow",
    )

    cluster = save_memory_merging_duplicates(
        slug="prod-cluster-east-2",
        memory_type="infrastructure",
        description="Production EKS cluster eks-prod-2 runs in us-east-2.",
        body="eks-prod-2 / us-east-2",
    )
    negated = save_memory_merging_duplicates(
        slug="no-pr-flow",
        memory_type="preference",
        description="User never wants a fix to go through a pull request.",
        body="push to main",
    )

    assert cluster is not None and cluster[1] == "created"
    assert negated is not None and negated[1] == "created"
    first = load_memory("prod-cluster-east")
    assert first is not None and "eks-prod-2" not in first.body


def test_a_chain_of_resemblances_never_joins_unlike_memories() -> None:
    # Review finding: clusters were built transitively, so A~B and B~C put C
    # with A even when the two say different things.
    from core.domain.memory.similarity import duplicate_clusters, similarity, subject_of

    a = _record("a", "User prefers short replies in Slack threads", updated="2026-10-03")
    b = _record("b", "User prefers short replies in Slack channels", updated="2026-10-02")
    c = _record("c", "User prefers Slack channels for incident updates", updated="2026-10-01")
    assert similarity(subject_of(a.slug, a.description), subject_of(c.slug, c.description)) < 0.6

    clusters = duplicate_clusters([a, b, c], threshold=0.4)

    assert all(not {"a", "c"} <= {record.slug for record in group} for group in clusters)


def test_a_windows_path_is_reported_as_machine_specific() -> None:
    record = _record("repo-root", "Where the repo lives", body=r"Checked out at C:\Users\ada\repo")

    rules = {finding.rule for finding in audit_memories([record], now=_NOW).findings}

    assert "machine_specific" in rules
