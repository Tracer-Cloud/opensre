"""Tests for the long-term memory tools (remember / forget / recall)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
from core.domain.types.tools import ToolRole
from core.tool_framework.tool_decorator import REGISTERED_TOOL_ATTR
from tests.tools.conftest import BaseToolContract
from tools.system.agent_memory import memory_forget, memory_recall, memory_remember
from tools.system.agent_memory._evidence import map_memory_recall
from tools.system.agent_memory.results import RECALL_BODY_CHAR_CAP
from tools.system.agent_memory.validation import MAX_RECALL_LIMIT


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)


def _registered(fn: Any) -> Any:
    return getattr(fn, REGISTERED_TOOL_ATTR)


def _remember(name: str = "prod-cluster", content: str = "Prod cluster is eks-prod-1.") -> Any:
    return memory_remember(
        name=name,
        type="infrastructure",
        description=f"Facts about {name}",
        content=content,
    )


class TestMemoryRememberContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(memory_remember)


class TestMemoryForgetContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(memory_forget)


class TestMemoryRecallContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered(memory_recall)


class TestMetadata:
    def test_surfaces_and_side_effects(self) -> None:
        assert _registered(memory_remember).surfaces == ("action",)
        assert _registered(memory_remember).side_effect_level == "mutating"
        assert _registered(memory_remember).role is ToolRole.BOOKKEEPING
        assert _registered(memory_forget).surfaces == ("action",)
        assert _registered(memory_forget).side_effect_level == "mutating"
        assert _registered(memory_forget).role is ToolRole.ACTION
        assert _registered(memory_recall).surfaces == ("action",)
        assert _registered(memory_recall).side_effect_level == "read_only"

    def test_unavailable_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for fn in (memory_remember, memory_forget, memory_recall):
            assert _registered(fn).is_available({}) is True
        monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
        for fn in (memory_remember, memory_forget, memory_recall):
            assert _registered(fn).is_available({}) is False


class TestRemember:
    def test_create_returns_path(self) -> None:
        result = _remember()
        assert result["status"] == "created"
        assert result["name"] == "prod-cluster"
        assert Path(result["path"]).is_file()

    def test_same_name_updates(self) -> None:
        _remember()
        assert _remember(content="Now eks-prod-2.")["status"] == "updated"

    def test_repository_type_keeps_distinct_repositories(self) -> None:
        first = memory_remember(
            name="repo-tracer-cloud-opensre",
            type="repository",
            description="OpenSRE repository",
            content="Tracer-Cloud/opensre uses main.",
        )
        second = memory_remember(
            name="repo-acme-payments",
            type="repository",
            description="Payments repository",
            content="acme/payments uses release.",
        )

        assert first["status"] == "created"
        assert second["status"] == "created"
        recalled = memory_recall(query="repository")
        assert {item["name"] for item in recalled["memories"]} == {
            "repo-tracer-cloud-opensre",
            "repo-acme-payments",
        }

    def test_free_form_name_normalized(self) -> None:
        result = _remember(name="Prod Cluster Conventions!")
        assert result["name"] == "prod-cluster-conventions"

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            ({"name": "!!!"}, "invalid_name"),
            ({"type": "nonsense"}, "invalid_type"),
            ({"description": "  "}, "empty_description"),
            ({"content": ""}, "empty_content"),
        ],
    )
    def test_invalid_args_return_structured_errors(
        self, kwargs: dict[str, Any], error: str
    ) -> None:
        args: dict[str, Any] = {
            "name": "ok-name",
            "type": "user",
            "description": "desc",
            "content": "body",
        }
        args.update(kwargs)
        assert memory_remember(**args)["error"] == error

    def test_secret_like_content_is_blocked(self) -> None:
        # Construct at runtime so pre-commit secret scanners do not flag fixtures.
        fake_token = "ghp_" + ("a" * 36)
        result = memory_remember(
            name="do-not-save-secret",
            type="preference",
            description="Temporary token",
            content=f"auth_token: {fake_token}",
        )
        assert result["error"] == "sensitive_content"
        assert "ghp_" not in str(result)


class TestForget:
    def test_delete_existing(self) -> None:
        _remember()
        assert memory_forget(name="prod-cluster")["status"] == "deleted"

    def test_missing_is_structured_not_exception(self) -> None:
        assert memory_forget(name="never-existed")["status"] == "not_found"

    def test_invalid_name(self) -> None:
        assert memory_forget(name="!!!")["error"] == "invalid_name"


class TestRecall:
    def test_by_name_returns_full_body(self) -> None:
        _remember()
        result = memory_recall(name="prod-cluster")
        assert result["total_stored"] == 1
        assert result["memories"][0]["content"] == "Prod cluster is eks-prod-1."

    def test_unknown_name_not_found(self) -> None:
        assert memory_recall(name="nope")["error"] == "not_found"

    def test_by_query(self) -> None:
        _remember()
        _remember(name="user-profile", content="Name is Vaibhav.")
        result = memory_recall(query="vaibhav")
        assert [m["name"] for m in result["memories"]] == ["user-profile"]

    def test_no_args_lists_index_without_bodies(self) -> None:
        _remember()
        result = memory_recall()
        assert result["total_stored"] == 1
        assert "content" not in result["memories"][0]

    def test_body_cap_enforced(self) -> None:
        _remember(content="z" * (RECALL_BODY_CHAR_CAP + 500))
        content = memory_recall(name="prod-cluster")["memories"][0]["content"]
        assert content.endswith("...[truncated]")
        assert len(content) <= RECALL_BODY_CHAR_CAP + len("\n...[truncated]")

    def test_query_limit_is_capped_and_invalid_query_is_structured(self) -> None:
        for i in range(MAX_RECALL_LIMIT + 5):
            _remember(name=f"prod-cluster-{i}", content="needle")

        result = memory_recall(query="needle", limit=999)
        assert len(result["memories"]) == MAX_RECALL_LIMIT
        assert memory_recall(query=123)["error"] == "invalid_query"


class TestMapMemoryRecall:
    def test_records_named_recall(self) -> None:
        evidence: dict[str, Any] = {}
        map_memory_recall(
            evidence,
            {"memories": [{"name": "prod-cluster"}], "total_stored": 5},
            {"name": "prod-cluster"},
        )
        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "memory_recall"
        assert entries[0]["summary"] == "recalled memory 'prod-cluster'"

    def test_records_query_search(self) -> None:
        evidence: dict[str, Any] = {}
        map_memory_recall(
            evidence,
            {"memories": [{"name": "a"}, {"name": "b"}], "total_stored": 10},
            {"query": "cluster"},
        )
        assert evidence["catalog_entries"][0]["summary"] == (
            "2 memory match(es) for query 'cluster' (of 10 stored)"
        )

    def test_records_index_listing(self) -> None:
        evidence: dict[str, Any] = {}
        map_memory_recall(evidence, {"memories": [{"name": "a"}], "total_stored": 1}, {})
        assert evidence["catalog_entries"][0]["summary"] == "1 memory index entries (of 1 stored)"

    def test_skips_empty_and_error_results(self) -> None:
        evidence: dict[str, Any] = {}
        map_memory_recall(evidence, {"memories": []}, {})
        assert "catalog_entries" not in evidence

        evidence2: dict[str, Any] = {}
        map_memory_recall(evidence2, {"error": "not_found", "name": "x"}, {"name": "x"})
        assert "catalog_entries" not in evidence2

    def test_disambiguates_repeat_calls(self) -> None:
        evidence: dict[str, Any] = {}
        map_memory_recall(evidence, {"memories": [{"name": "a"}]}, {"name": "a"})
        map_memory_recall(evidence, {"memories": [{"name": "b"}]}, {"name": "b"})
        sources = [e["source"] for e in evidence["catalog_entries"]]
        assert sources == ["memory_recall", "memory_recall#2"]
