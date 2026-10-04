"""Consolidation: archive demo and stale memories, merge duplicate repositories, write the summary.

Each test drives :func:`consolidate_memories` with an explicit clock, because
the six-hour cooldown and the 120-day staleness rule are the contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
from core.domain.memory import (
    ConsolidationInput,
    append_session_summary,
    consolidate_memories,
    delete_memory,
    list_memories,
    load_memory,
    memory_dir,
    record_memory_usage,
    render_prompt_index,
    save_memory,
    search_memories,
    search_memories_scored,
)
from core.domain.memory.consolidation_state import read_consolidation_state
from core.domain.memory.files import ARCHIVE_DIRNAME, INDEX_FILENAME, SUMMARY_FILENAME
from core.domain.memory.models import TRUNCATION_MARKER

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    return tmp_path / "memory"


def _save_at(
    monkeypatch: pytest.MonkeyPatch,
    when: datetime,
    *,
    slug: str,
    memory_type: str,
    description: str,
    body: str = "body",
) -> None:
    stamp = when.isoformat(timespec="seconds")
    monkeypatch.setattr("core.domain.memory.store._now_iso", lambda: stamp)
    assert save_memory(slug=slug, memory_type=memory_type, description=description, body=body)


def _archived() -> set[str]:
    archive = memory_dir() / ARCHIVE_DIRNAME
    return {path.stem for path in archive.glob("*.md")} if archive.is_dir() else set()


def test_demo_and_stale_memories_are_archived_but_personal_ones_never_age_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = NOW - timedelta(days=200)
    _save_at(
        monkeypatch,
        NOW,
        slug="repository-davincios-opensre-ci-repair-demo-yx06",
        memory_type="repository",
        description="davincios/opensre-ci-repair-demo-yx06 is a retained CI-repair demo.",
    )
    _save_at(monkeypatch, old, slug="old-vpn-host", memory_type="infrastructure", description="VPN")
    _save_at(monkeypatch, old, slug="report-style", memory_type="preference", description="Terse")
    _save_at(monkeypatch, NOW, slug="prod-cluster", memory_type="infrastructure", description="EKS")

    result = consolidate_memories(now=NOW)

    assert result.ran
    assert set(result.archived) == {
        "repository-davincios-opensre-ci-repair-demo-yx06",
        "old-vpn-host",
    }
    assert _archived() == set(result.archived)
    assert {record.slug for record in list_memories()} == {"report-style", "prod-cluster"}
    index_file = (memory_dir() / INDEX_FILENAME).read_text(encoding="utf-8")
    assert "old-vpn-host" not in index_file


def test_recent_use_keeps_an_old_memory_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    _save_at(
        monkeypatch,
        NOW - timedelta(days=200),
        slug="old-vpn-host",
        memory_type="infrastructure",
        description="VPN host",
    )
    record_memory_usage(["old-vpn-host"], now=NOW - timedelta(days=10))

    assert consolidate_memories(now=NOW).archived == ()
    assert load_memory("old-vpn-host") is not None


def test_duplicate_repository_memories_merge_into_the_newest_without_losing_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kept memory is the newest, but the older one's notes must survive in it."""
    _save_at(
        monkeypatch,
        NOW - timedelta(days=21),
        slug="repository-opensre",
        memory_type="repository",
        description="Tracer-Cloud/opensre has a two-minute local CI-repair loop for PR #6148.",
        body=(
            "### Tracer-Cloud/opensre\n- Default branch: `main`\n\n\n"
            "- CI runs on GitHub Actions\n- Windows test jobs are flaky"
        ),
    )
    _save_at(
        monkeypatch,
        NOW - timedelta(days=1),
        slug="repository-tracer-cloud-opensre",
        memory_type="repository",
        description="Tracer-Cloud/opensre is a repository the user selected for analysis.",
        body="# Tracer-Cloud/opensre\n- CI runs on GitHub Actions",
    )
    _save_at(
        monkeypatch,
        NOW - timedelta(days=2),
        slug="repository-opensre-webapp",
        memory_type="repository",
        description="Tracer-Cloud/opensre-webapp is a different repository.",
    )

    result = consolidate_memories(now=NOW)

    assert result.merged == (("repository-opensre", "repository-tracer-cloud-opensre"),)
    assert _archived() == {"repository-opensre"}
    kept = load_memory("repository-tracer-cloud-opensre")
    assert kept is not None
    assert kept.body == (
        "# Tracer-Cloud/opensre\n"
        "- CI runs on GitHub Actions\n"
        "\n"
        "## Merged from repository-opensre (2026-09-13)\n"
        "### Tracer-Cloud/opensre\n"
        "- Default branch: `main`\n"
        "\n"
        "- Windows test jobs are flaky"
    )
    assert load_memory("repository-opensre-webapp") is not None


def test_a_duplicate_whose_notes_do_not_fit_stays_live_instead_of_being_archived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archiving a duplicate whose notes were cut took them out of prompts and recall."""
    own_body = "# acme/payments\n" + "\n".join(f"- note {index:04d}" for index in range(700))
    _save_at(
        monkeypatch,
        NOW - timedelta(days=9),
        slug="repo-acme-payments-old",
        memory_type="repository",
        description="acme/payments deploys from release",
        body="\n".join(f"- older note {index:04d}" for index in range(300)),
    )
    _save_at(
        monkeypatch,
        NOW - timedelta(days=5),
        slug="repo-acme-payments-ci",
        memory_type="repository",
        description="acme/payments CI notes",
        body="- CI runs on GitHub Actions",
    )
    _save_at(
        monkeypatch,
        NOW - timedelta(days=1),
        slug="repository-acme-payments",
        memory_type="repository",
        description="acme/payments owns the checkout API",
        body=own_body,
    )

    result = consolidate_memories(now=NOW)

    assert result.merged == (("repo-acme-payments-ci", "repository-acme-payments"),)
    assert _archived() == {"repo-acme-payments-ci"}
    kept = load_memory("repository-acme-payments")
    assert kept is not None
    assert kept.body.startswith(own_body)
    assert kept.body.endswith("- CI runs on GitHub Actions")
    assert TRUNCATION_MARKER not in kept.body
    left = load_memory("repo-acme-payments-old")
    assert left is not None and "- older note 0299" in left.body
    assert "repo-acme-payments-old" in {
        record.slug for record, _ in search_memories_scored("older note 0299")
    }


def test_runs_once_per_cooldown_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    for days, slug in ((20, "repository-opensre"), (1, "repository-tracer-cloud-opensre")):
        _save_at(
            monkeypatch,
            NOW - timedelta(days=days),
            slug=slug,
            memory_type="repository",
            description="Tracer-Cloud/opensre notes",
        )

    assert consolidate_memories(now=NOW).merged
    assert not consolidate_memories(now=NOW + timedelta(hours=1)).ran
    again = consolidate_memories(now=NOW + timedelta(hours=7))

    assert again.ran
    assert (again.archived, again.merged) == ((), ())
    kept = load_memory("repository-tracer-cloud-opensre")
    assert kept is not None
    assert kept.body.count("## Merged from") == 1


def test_archived_and_derived_files_are_never_read_as_live_memories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_at(monkeypatch, NOW, slug="prod-cluster", memory_type="infrastructure", description="EKS")
    archive = memory_dir() / ARCHIVE_DIRNAME
    archive.mkdir()
    (archive / "ghost-memory.md").write_text(
        "---\nname: ghost-memory\ntype: user\ndescription: archived ghost\n"
        "created: 2026-01-01T00:00:00+00:00\nupdated: 2026-01-01T00:00:00+00:00\n---\nghost\n"
    )
    append_session_summary("session-1", "Investigated the ghost memory.", outcome="success")

    assert [record.slug for record in list_memories()] == ["prod-cluster"]
    assert search_memories("ghost") == []
    assert "ghost" not in render_prompt_index()


def test_summary_is_written_then_skipped_while_its_input_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_at(monkeypatch, NOW, slug="user-profile", memory_type="user", description="Vaibhav")
    append_session_summary("session-1", "Repaired the payments CI.", outcome="success")
    seen: list[ConsolidationInput] = []

    def _summarize(source: ConsolidationInput) -> str:
        seen.append(source)
        return "## User\nVaibhav, platform SRE."

    first = consolidate_memories(now=NOW, summarize=_summarize)
    later = consolidate_memories(now=NOW + timedelta(hours=7), summarize=_summarize)

    assert first.summary_written and not later.summary_written
    assert len(seen) == 1
    assert [record.slug for record in seen[0].memories] == ["user-profile"]
    assert [summary.text for summary in seen[0].session_summaries] == ["Repaired the payments CI."]
    assert "Vaibhav, platform SRE." in render_prompt_index()


def test_an_unavailable_summarizer_still_tidies_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _save_at(monkeypatch, NOW, slug="user-profile", memory_type="user", description="Vaibhav")

    def _unavailable(_source: ConsolidationInput) -> str:
        raise RuntimeError("no LLM configured")

    result = consolidate_memories(now=NOW, summarize=_unavailable)

    assert result.ran and not result.summary_written
    assert not (memory_dir() / SUMMARY_FILENAME).exists()


def test_forgetting_a_memory_drops_the_summary_and_reopens_consolidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_at(monkeypatch, NOW, slug="employer", memory_type="user", description="Works at Acme")
    consolidate_memories(now=NOW, summarize=lambda _source: "## User\nWorks at Acme.")
    assert (memory_dir() / SUMMARY_FILENAME).exists()

    assert delete_memory("employer")

    assert not (memory_dir() / SUMMARY_FILENAME).exists()
    assert read_consolidation_state(memory_dir()).last_run_at is None
    assert consolidate_memories(now=NOW + timedelta(minutes=5)).ran


def test_a_forgotten_fact_never_returns_through_a_session_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session summary may describe the forgotten fact; consolidation must not read it back."""
    _save_at(monkeypatch, NOW, slug="user-profile", memory_type="user", description="Vaibhav")
    _save_at(
        monkeypatch,
        NOW,
        slug="payments-seed-fix",
        memory_type="investigation_learning",
        description="Pinning the random seed fixes the payments flake",
    )
    started = datetime.now(UTC) - timedelta(minutes=5)
    assert append_session_summary(
        "before-forget", "Fixed the payments flake by pinning the seed.", session_started=started
    )

    assert delete_memory("payments-seed-fix")

    # A session that was running when the memory was forgotten records nothing more.
    assert not append_session_summary(
        "before-forget", "Pinned the seed once more.", session_started=started
    )
    forgotten = read_consolidation_state(memory_dir()).forgotten_at
    assert forgotten is not None
    later = forgotten + timedelta(minutes=1)
    assert append_session_summary(
        "after-forget", "Reviewed the deploy dashboards.", session_started=later, now=later
    )
    seen: list[ConsolidationInput] = []

    def _summarize(source: ConsolidationInput) -> str:
        seen.append(source)
        return "## User\nVaibhav."

    assert consolidate_memories(now=NOW, summarize=_summarize).summary_written
    [source] = seen
    assert [summary.session_id for summary in source.session_summaries] == ["after-forget"]
    assert [record.slug for record in source.memories] == ["user-profile"]


@pytest.mark.parametrize("change", ["forget", "update"])
def test_a_summary_of_a_store_that_changed_meanwhile_is_dropped(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """The summarizer runs unlocked; its result must not undo a forget or hide an update."""
    _save_at(monkeypatch, NOW, slug="user-profile", memory_type="user", description="Vaibhav")
    _save_at(monkeypatch, NOW, slug="employer", memory_type="user", description="Works at Acme")

    def _summarize(_source: ConsolidationInput) -> str:
        if change == "forget":
            assert delete_memory("employer")
        else:
            assert save_memory(
                slug="employer", memory_type="user", description="Works at Globex", body="Globex"
            )
        return "## User\nVaibhav works at Acme."

    result = consolidate_memories(now=NOW, summarize=_summarize)

    assert result.ran and not result.summary_written
    assert not (memory_dir() / SUMMARY_FILENAME).exists()
    assert read_consolidation_state(memory_dir()).last_run_at is None
    assert consolidate_memories(now=NOW + timedelta(minutes=5)).ran
