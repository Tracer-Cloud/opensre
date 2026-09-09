from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

from config.runbook_sources import RunbookSourceConfig
from core.domain.runbooks import (
    RunbookCatalog,
    RunbookCatalogEntry,
    RunbookDocument,
    RunbookMatch,
    RunbookReference,
)
from core.tool import AgentToolContext
from tools import registry as registry_module
from tools.system.runbook_guidance_tool import load_runbook_guidance
from tools.system.runbook_guidance_tool._evidence import map_runbook_guidance

runbook_tool_module = import_module("tools.system.runbook_guidance_tool.tool")

_SHA = "a" * 40
_CONFIG = RunbookSourceConfig(
    name="platform",
    provider="github",
    repository="acme/operations",
    ref="main",
    manifest=".opensre/runbooks.yaml",
)


class _FakeRunbookSource:
    provider = "github"

    def __init__(
        self,
        *,
        catalog: RunbookCatalog | None = None,
        accepted_url: str = "",
        document_error: Exception | None = None,
    ) -> None:
        self.catalog = catalog
        self.accepted_url = accepted_url
        self.document_error = document_error
        self.fetched_reference: RunbookReference | None = None

    def verify(self) -> tuple[bool, str]:
        return True, "ok"

    def resolve_reference(self, url: str) -> RunbookReference | None:
        if url != self.accepted_url:
            return None
        return RunbookReference(
            source_name="platform",
            document_id="checkout",
            path="runbooks/checkout.md",
            requested_revision="main",
        )

    def fetch_catalog(self) -> RunbookCatalog:
        if self.catalog is None:
            raise RuntimeError("catalog unavailable")
        return self.catalog

    def fetch_document(self, reference: RunbookReference) -> RunbookDocument:
        if self.document_error is not None:
            raise self.document_error
        self.fetched_reference = reference
        return RunbookDocument(
            reference=reference,
            content="# Checkout latency\n\nInspect the latest deployment.",
            resolved_revision=_SHA,
            source_uri=(f"https://github.com/acme/operations/blob/{_SHA}/runbooks/checkout.md"),
            title="Checkout latency",
        )


def _install_source(
    monkeypatch: pytest.MonkeyPatch,
    source: _FakeRunbookSource,
    *,
    configs: tuple[RunbookSourceConfig, ...] = (_CONFIG,),
) -> None:
    monkeypatch.setattr(runbook_tool_module, "load_runbook_sources", lambda: configs)
    monkeypatch.setattr(
        runbook_tool_module,
        "resolve_runbook_source",
        lambda _config, _integrations: source,
    )


def _context() -> AgentToolContext:
    return AgentToolContext(resolved_integrations={"github": {"connection_verified": True}})


def test_tool_is_discoverable_on_the_shared_chat_surface() -> None:
    registered = registry_module.get_registered_tool_map("chat")["load_runbook_guidance"]

    assert registered.accepts_runtime_context is True
    assert registered.side_effect_level == "read_only"
    assert registered.evidence_mapper is map_runbook_guidance


def test_explicit_trusted_url_loads_document_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/acme/operations/blob/main/runbooks/checkout.md"
    source = _FakeRunbookSource(accepted_url=url)
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(runbook_url=url, context=_context())

    assert result["status"] == "loaded"
    assert result["runbook"]["match_reason"] == "explicit_url"
    assert result["runbook"]["revision"] == _SHA
    assert result["runbook"]["url"].endswith(f"{_SHA}/runbooks/checkout.md")


def test_manifest_match_fetches_document_at_catalog_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = RunbookCatalogEntry(
        document_id="checkout-high-latency",
        title="Checkout latency",
        path="runbooks/checkout.md",
        match=RunbookMatch(
            alertname="CheckoutHighLatency",
            labels=(("severity", "critical"),),
        ),
    )
    source = _FakeRunbookSource(
        catalog=RunbookCatalog(
            source_name="platform",
            entries=(entry,),
            resolved_revision=_SHA,
            source_uri=f"https://github.com/acme/operations/blob/{_SHA}/.opensre/runbooks.yaml",
        )
    )
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(
        alertname="CheckoutHighLatency",
        labels={"severity": "critical", "region": "us-east-1"},
        context=_context(),
    )

    assert result["status"] == "loaded"
    assert result["runbook"]["match_reason"] == "alertname_labels"
    assert result["runbook"]["matched_fields"] == ["alertname", "label:severity"]
    assert source.fetched_reference is not None
    assert source.fetched_reference.requested_revision == _SHA


def test_equal_manifest_matches_are_reported_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = tuple(
        RunbookCatalogEntry(
            document_id=document_id,
            path=f"runbooks/{document_id}.md",
            match=RunbookMatch(alertname="CheckoutDown"),
        )
        for document_id in ("checkout-a", "checkout-b")
    )
    source = _FakeRunbookSource(
        catalog=RunbookCatalog(
            source_name="platform",
            entries=entries,
            resolved_revision=_SHA,
            source_uri="manifest",
        )
    )
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(alertname="CheckoutDown", context=_context())

    assert result["status"] == "ambiguous"
    assert result["candidates"] == ["platform/checkout-a", "platform/checkout-b"]
    assert source.fetched_reference is None


def test_highest_precedence_match_wins_across_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic_config = _CONFIG.model_copy(update={"name": "service-runbooks"})
    specific_config = _CONFIG.model_copy(update={"name": "alert-runbooks"})
    generic_source = _FakeRunbookSource(
        catalog=RunbookCatalog(
            source_name=generic_config.name,
            entries=(
                RunbookCatalogEntry(
                    document_id="checkout-service",
                    path="runbooks/checkout-service.md",
                    match=RunbookMatch(service="checkout"),
                ),
            ),
            resolved_revision=_SHA,
            source_uri="service-manifest",
        )
    )
    specific_source = _FakeRunbookSource(
        catalog=RunbookCatalog(
            source_name=specific_config.name,
            entries=(
                RunbookCatalogEntry(
                    document_id="checkout-production",
                    path="runbooks/checkout-production.md",
                    match=RunbookMatch(
                        alertname="CheckoutDown",
                        labels=(("environment", "production"),),
                    ),
                ),
            ),
            resolved_revision=_SHA,
            source_uri="alert-manifest",
        )
    )
    sources = {
        generic_config.name: generic_source,
        specific_config.name: specific_source,
    }
    monkeypatch.setattr(
        runbook_tool_module,
        "load_runbook_sources",
        lambda: (generic_config, specific_config),
    )
    monkeypatch.setattr(
        runbook_tool_module,
        "resolve_runbook_source",
        lambda config, _integrations: sources[config.name],
    )

    result = load_runbook_guidance(
        alertname="CheckoutDown",
        service="checkout",
        labels={"environment": "production"},
        context=_context(),
    )

    assert result["status"] == "loaded"
    assert result["runbook"]["source_name"] == specific_config.name
    assert result["runbook"]["document_id"] == "checkout-production"
    assert generic_source.fetched_reference is None
    assert specific_source.fetched_reference is not None


def test_untrusted_url_is_rejected_without_fallback_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _FakeRunbookSource()
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(
        runbook_url="https://example.test/runbook.md",
        alertname="CheckoutDown",
        context=_context(),
    )

    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert "accepts this URL" in result["message"]


def test_retrieval_failure_does_not_expose_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/acme/operations/blob/main/runbooks/checkout.md"
    source = _FakeRunbookSource(
        accepted_url=url,
        document_error=RuntimeError("token ghp_secret rejected by private repository"),
    )
    _install_source(monkeypatch, source)

    result = load_runbook_guidance(runbook_url=url, context=_context())

    assert result["available"] is False
    assert result["status"] == "unavailable"
    assert "ghp_secret" not in str(result)


def test_loaded_runbook_becomes_citeable_evidence() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "status": "loaded",
        "runbook": {
            "title": "Checkout latency",
            "path": "runbooks/checkout.md",
            "revision": _SHA,
            "url": f"https://github.com/acme/operations/blob/{_SHA}/runbooks/checkout.md",
        },
    }

    map_runbook_guidance(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "load_runbook_guidance",
            "label": "Runbook Guidance",
            "summary": f"Checkout latency: runbooks/checkout.md@{_SHA}",
            "url": output["runbook"]["url"],
            "snippet": None,
        }
    ]


def test_registered_github_provider_runs_manifest_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = """
version: 1
runbooks:
  - id: checkout-high-latency
    title: Checkout high latency
    document: docs/snippets/runbooks/checkout-high-latency.md
    match:
      alertname: CheckoutHighLatency
      service: checkout
      labels:
        severity: critical
""".strip()

    def github_file_contents(**kwargs: Any) -> dict[str, Any]:
        path = str(kwargs["path"])
        content = manifest if path.endswith("demo-manifest.yaml") else "# Checkout high latency"
        return {
            "available": True,
            "file": {
                "uri": f"repo://acme/operations/sha/{_SHA}/contents/{path}",
                "content": content,
            },
            "content": [],
        }

    demo_config = _CONFIG.model_copy(
        update={"manifest": "docs/snippets/runbooks/demo-manifest.yaml"}
    )
    monkeypatch.setattr(runbook_tool_module, "load_runbook_sources", lambda: (demo_config,))
    monkeypatch.setattr(
        "integrations.github.runbooks.source.get_github_file_contents",
        github_file_contents,
    )
    monkeypatch.setattr(
        "integrations.github.runbooks.source.list_github_commits",
        lambda **_kwargs: {"available": True, "commits": [{"sha": _SHA}]},
    )

    result = load_runbook_guidance(
        alertname="CheckoutHighLatency",
        service="checkout",
        labels={"severity": "critical"},
        context=_context(),
    )

    assert result["status"] == "loaded"
    assert result["runbook"]["document_id"] == "checkout-high-latency"
    assert result["runbook"]["revision"] == _SHA
    assert result["runbook"]["content"] == "# Checkout high latency"
