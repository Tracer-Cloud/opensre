"""Unit tests for the long-term memory domain store."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
    OPENSRE_MEMORY_DISABLED_ENV,
)
from core.domain.memory import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MemoryRecord,
    auto_extract_enabled,
    delete_memory,
    find_memory_safety_issues,
    is_valid_slug,
    list_memories,
    load_memory,
    memory_dir,
    memory_enabled,
    memory_path,
    render_prompt_index,
    save_memory,
    search_memories,
    slugify,
)
from core.domain.memory.frontmatter import parse_memory_file, serialize_memory


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    memory_home = tmp_path / "memory"
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(memory_home))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    monkeypatch.delenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, raising=False)
    return memory_home


def _save(slug: str = "prod-cluster", body: str = "Prod cluster is eks-prod-1.") -> MemoryRecord:
    result = save_memory(
        slug=slug,
        memory_type="infrastructure",
        description=f"Facts about {slug}",
        body=body,
    )
    assert result is not None
    return result[0]


class TestSlugs:
    def test_slugify_normalizes(self) -> None:
        assert slugify("  Prod Cluster / Conventions!  ") == "prod-cluster-conventions"

    @pytest.mark.parametrize("bad", ["", "../evil", "UPPER", "a b", "memory", "-lead", "trail-"])
    def test_invalid_slugs_rejected(self, bad: str) -> None:
        assert not is_valid_slug(bad)

    def test_valid_slug(self) -> None:
        assert is_valid_slug("user-profile-2")


class TestSaveAndLoad:
    def test_create_then_load_roundtrip(self) -> None:
        record = _save()
        loaded = load_memory("prod-cluster")
        assert loaded == record
        assert loaded is not None and loaded.memory_type == "infrastructure"

    def test_update_preserves_created_at_and_single_file(self) -> None:
        first = _save()
        result = save_memory(
            slug="prod-cluster",
            memory_type="infrastructure",
            description="updated description",
            body="Now eks-prod-2.",
        )
        assert result is not None
        updated, created = result
        assert created is False
        assert updated.created_at == first.created_at
        files = [p.name for p in memory_dir().glob("*.md") if p.name != "MEMORY.md"]
        assert files == ["prod-cluster.md"]

    def test_invalid_slug_raises(self) -> None:
        with pytest.raises(ValueError):
            save_memory(slug="../evil", memory_type="user", description="x", body="y")

    def test_description_flattened_and_capped(self) -> None:
        result = save_memory(
            slug="caps",
            memory_type="user",
            description="line one\nline two" + "x" * 500,
            body="body",
        )
        assert result is not None
        record = result[0]
        assert "\n" not in record.description
        assert len(record.description) <= MAX_DESCRIPTION_CHARS

    def test_body_truncated_at_cap(self) -> None:
        record = _save(body="z" * (MAX_BODY_CHARS + 1_000))
        assert len(record.body) <= MAX_BODY_CHARS
        assert record.body.endswith("...[truncated]")

    def test_memory_files_are_private_on_posix(self) -> None:
        _save()
        if os.name == "posix":
            assert memory_dir().stat().st_mode & 0o777 == 0o700
            assert memory_path("prod-cluster").stat().st_mode & 0o777 == 0o600
            assert (memory_dir() / "MEMORY.md").stat().st_mode & 0o777 == 0o600


class TestListDeleteSearch:
    def test_multiple_repository_memories_are_retained_independently(self) -> None:
        first = save_memory(
            slug="repo-tracer-cloud-opensre",
            memory_type="repository",
            description="OpenSRE repository",
            body="Tracer-Cloud/opensre uses main as its default branch.",
        )
        second = save_memory(
            slug="repo-acme-payments",
            memory_type="repository",
            description="Payments repository",
            body="acme/payments deploys the checkout API from release.",
        )

        assert first is not None and second is not None
        assert {record.slug for record in list_memories()} == {
            "repo-tracer-cloud-opensre",
            "repo-acme-payments",
        }
        rendered = render_prompt_index()
        assert "Tracer-Cloud/opensre" in rendered
        assert "acme/payments" in rendered

    def test_list_orders_by_updated_desc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stamps = iter(["2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00"])
        monkeypatch.setattr("core.domain.memory.store._now_iso", lambda: next(stamps))
        _save("older")
        _save("newer")
        assert [r.slug for r in list_memories()] == ["newer", "older"]

    def test_list_skips_malformed_and_index(self) -> None:
        _save()
        (memory_dir() / "broken.md").write_text("not frontmatter", encoding="utf-8")
        assert [r.slug for r in list_memories()] == ["prod-cluster"]

    def test_list_does_not_create_dir(self) -> None:
        assert list_memories() == []
        assert not memory_dir().exists()

    def test_delete_existing_and_missing(self) -> None:
        _save()
        assert delete_memory("prod-cluster") is True
        assert delete_memory("prod-cluster") is False
        assert load_memory("prod-cluster") is None

    def test_search_matches_slug_description_body(self) -> None:
        _save(body="The flaky service is checkout-api.")
        assert [r.slug for r in search_memories("checkout-API")] == ["prod-cluster"]
        assert search_memories("nomatch") == []


class TestIndex:
    def test_memory_md_rebuilt_on_save_and_delete(self) -> None:
        _save()
        index = (memory_dir() / "MEMORY.md").read_text(encoding="utf-8")
        assert "prod-cluster" in index
        delete_memory("prod-cluster")
        index = (memory_dir() / "MEMORY.md").read_text(encoding="utf-8")
        assert "prod-cluster" not in index

    def test_render_prompt_index_empty_and_populated(self) -> None:
        assert render_prompt_index() == ""
        _save()
        rendered = render_prompt_index()
        assert "[infrastructure] prod-cluster" in rendered
        assert "details" in rendered or "Prod" in rendered  # body or description

    def test_render_prompt_index_includes_body(self) -> None:
        _save(body="The flaky service is checkout-api.")
        rendered = render_prompt_index()
        assert "checkout-api" in rendered

    def test_render_prompt_index_respects_char_cap(self) -> None:
        for i in range(10):
            _save(f"mem-{i}", body=f"body-{i}-" + ("x" * 80))
        rendered = render_prompt_index(max_chars=120)
        assert "more memories (use memory_recall)" in rendered

    def test_ensure_memory_store_creates_dir_and_index(self) -> None:
        from core.domain.memory import ensure_memory_store

        path = ensure_memory_store()
        assert path.is_dir()
        assert (path / "MEMORY.md").is_file()


class TestFrontmatter:
    def test_roundtrip_with_fence_in_body(self) -> None:
        record = _save(body="intro\n---\nafter a fence")
        loaded = load_memory("prod-cluster")
        assert loaded is not None and loaded.body == record.body

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "no fences at all",
            "---\nname: x\n",  # unclosed
            "---\nname: bad slug!\ntype: user\ndescription: d\ncreated: c\nupdated: u\n---\n",
            "---\nname: ok\ntype: not-a-type\ndescription: d\ncreated: c\nupdated: u\n---\n",
            "---\nnamewithoutcolon\n---\n",
        ],
    )
    def test_malformed_returns_none(self, text: str) -> None:
        assert parse_memory_file(text) is None

    def test_serialize_parse_identity(self) -> None:
        record = MemoryRecord(
            slug="a-slug",
            memory_type="preference",
            description="desc",
            created_at="2026-07-09T00:00:00+00:00",
            updated_at="2026-07-09T00:00:00+00:00",
            body="body text",
        )
        assert parse_memory_file(serialize_memory(record)) == record


class TestSettings:
    def test_gate_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert memory_enabled() is True
        assert auto_extract_enabled() is True
        monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
        assert memory_enabled() is True
        assert auto_extract_enabled() is False
        monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "true")
        assert memory_enabled() is False
        assert auto_extract_enabled() is False

    def test_gateway_surfaces_require_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from config.constants import OPENSRE_MEMORY_GATEWAY_ENABLED_ENV
        from core.domain.memory import gateway_memory_enabled, memory_available_here
        from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context

        with bound_usage_context(surface=UsageSurface.SLACK):
            assert memory_enabled() is True
            assert gateway_memory_enabled() is False
            assert memory_available_here() is False
            assert auto_extract_enabled() is False
            monkeypatch.setenv(OPENSRE_MEMORY_GATEWAY_ENABLED_ENV, "1")
            assert memory_available_here() is True
            assert auto_extract_enabled() is True


class TestSafety:
    def test_secret_like_content_is_detected_without_echoing_value(self) -> None:
        # Construct at runtime so pre-commit secret scanners do not flag fixtures.
        fake_key = "sk-proj-" + ("a" * 32)
        issues = find_memory_safety_issues(
            "API key for old integration",
            f"OPENAI_API_KEY: {fake_key}",
        )
        assert {issue.rule for issue in issues} >= {"provider_token", "labeled_secret"}

    def test_non_secret_operational_terms_are_allowed(self) -> None:
        assert (
            find_memory_safety_issues(
                "Incident lesson",
                "Missing Kubernetes Secret binding caused startup failure.",
            )
            == ()
        )

    def test_save_memory_rejects_secret_like_content(self) -> None:
        fake_token = "ghp_" + ("a" * 36)
        with pytest.raises(ValueError, match="safety checks"):
            save_memory(
                slug="do-not-save",
                memory_type="preference",
                description="Temporary token",
                body=f"auth_token: {fake_token}",
            )
        assert list_memories() == []

    def test_redact_replaces_secret_spans(self) -> None:
        from core.domain.memory import redact_memory_unsafe_text

        fake_token = "ghp_" + ("a" * 36)
        redacted = redact_memory_unsafe_text(
            f"deploy used auth_token: {fake_token} and cluster eks-prod-1"
        )
        assert fake_token not in redacted
        assert "[REDACTED]" in redacted
        assert "eks-prod-1" in redacted

    def test_redact_keeps_separator_when_label_contains_value(self) -> None:
        from core.domain.memory import redact_memory_unsafe_text

        redacted = redact_memory_unsafe_text("pass1234_secret_key: pass1234")

        assert redacted == "pass1234_secret_key: [REDACTED]"


class TestConcurrency:
    def test_parallel_writes_to_same_slug_serialize(self) -> None:
        import concurrent.futures

        bodies = [f"body-{i}-" + ("x" * 32) for i in range(8)]

        def _write(body: str) -> tuple[MemoryRecord, bool] | None:
            return save_memory(
                slug="shared-fact",
                memory_type="preference",
                description="Shared preference",
                body=body,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_write, bodies))

        successful = [result for result in results if result is not None]
        assert len(successful) == len(results)
        assert sum(1 for _record, created in successful if created) == 1
        loaded = load_memory("shared-fact")
        assert loaded is not None
        assert loaded.body in bodies
        assert len(list_memories()) == 1
