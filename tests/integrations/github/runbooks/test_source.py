from __future__ import annotations

from unittest.mock import patch

import pytest

from config.constants.runbooks import RUNBOOK_MANIFEST_MAX_CHARS
from config.runbook_sources import RunbookSourceConfig
from core.domain.runbooks import RunbookReference
from integrations.github.runbooks.source import (
    GitHubRunbookSource,
    RunbookRetrievalError,
)

_SOURCE = RunbookSourceConfig(
    name="platform-runbooks",
    provider="github",
    repository="acme/operations",
    ref="main",
    manifest=".opensre/runbooks.yaml",
)
_GITHUB = {
    "connection_verified": True,
    "url": "https://mcp.example.test",
    "auth_token": "secret",
}


def _file_payload(path: str, content: str, *, sha: str = "abc123") -> dict[str, object]:
    return {
        "available": True,
        "file": {
            "uri": f"repo://acme/operations/sha/{sha}/contents/{path}",
            "content": content,
        },
        "content": [],
    }


def _commits_payload(sha: str) -> dict[str, object]:
    return {"available": True, "commits": [{"sha": sha}]}


def test_resolve_reference_accepts_only_configured_repository_and_ref() -> None:
    source = GitHubRunbookSource(_SOURCE, _GITHUB)

    reference = source.resolve_reference(
        "https://github.com/acme/operations/blob/main/runbooks/checkout.md"
    )

    assert reference == RunbookReference(
        source_name="platform-runbooks",
        document_id="checkout",
        path="runbooks/checkout.md",
        requested_revision="main",
        canonical_url="https://github.com/acme/operations/blob/main/runbooks/checkout.md",
    )
    assert (
        source.resolve_reference(
            "https://github.com/other/operations/blob/main/runbooks/checkout.md"
        )
        is None
    )
    assert (
        source.resolve_reference("https://github.com/acme/operations/blob/dev/runbooks/checkout.md")
        is None
    )
    sha_reference = source.resolve_reference(
        f"https://github.com/acme/operations/blob/{'a' * 40}/runbooks/checkout.md"
    )
    assert sha_reference is not None
    assert sha_reference.requested_revision == "a" * 40
    assert (
        source.resolve_reference(
            "https://github.com/acme/operations/blob/main/runbooks/%00checkout.md"
        )
        is None
    )


def test_resolve_reference_accepts_a_url_at_an_explicitly_pinned_sha() -> None:
    revision = "a" * 40
    source = GitHubRunbookSource(
        RunbookSourceConfig(
            name="pinned-runbook",
            provider="github",
            repository="acme/operations",
            ref=revision,
        ),
        _GITHUB,
    )

    reference = source.resolve_reference(
        f"https://github.com/acme/operations/blob/{revision}/runbooks/checkout.md"
    )

    assert reference is not None
    assert reference.requested_revision == revision


def test_fetch_document_rejects_sha_outside_configured_ref_history() -> None:
    candidate_revision = "a" * 40
    trusted_revision = "b" * 40
    source = GitHubRunbookSource(_SOURCE, _GITHUB)
    reference = source.resolve_reference(
        f"https://github.com/acme/operations/blob/{candidate_revision}/runbooks/checkout.md"
    )

    assert reference is not None
    with (
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload(trusted_revision),
        ),
        patch(
            "integrations.github.runbooks.source.is_github_revision_reachable",
            return_value=False,
        ) as reachable,
        pytest.raises(RunbookRetrievalError, match="not reachable from the configured ref"),
    ):
        source.fetch_document(reference)

    reachable.assert_called_once_with(
        owner="acme",
        repo="operations",
        trusted_revision=trusted_revision,
        candidate_revision=candidate_revision,
        auth_token="secret",
    )


def test_fetch_document_accepts_sha_in_configured_ref_history() -> None:
    candidate_revision = "a" * 40
    trusted_revision = "b" * 40
    source = GitHubRunbookSource(_SOURCE, _GITHUB)
    reference = source.resolve_reference(
        f"https://github.com/acme/operations/blob/{candidate_revision}/runbooks/checkout.md"
    )

    assert reference is not None
    with (
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload(trusted_revision),
        ),
        patch(
            "integrations.github.runbooks.source.is_github_revision_reachable",
            return_value=True,
        ),
        patch(
            "integrations.github.runbooks.source.get_github_file_contents",
            return_value=_file_payload(
                "runbooks/checkout.md", "# Checkout", sha=candidate_revision
            ),
        ),
    ):
        document = source.fetch_document(reference)

    assert document.resolved_revision == candidate_revision


def test_catalog_revision_pins_document_fetch() -> None:
    manifest_sha = "a" * 40
    manifest = """
version: 1
runbooks:
  - id: checkout-high-latency
    title: Checkout latency
    document: runbooks/checkout-high-latency.md
    match:
      alertname: CheckoutHighLatency
      labels:
        service: checkout
""".strip()
    source = GitHubRunbookSource(_SOURCE, _GITHUB)

    with (
        patch(
            "integrations.github.runbooks.source.get_github_file_contents",
            side_effect=(
                _file_payload(".opensre/runbooks.yaml", manifest, sha=manifest_sha),
                _file_payload(
                    "runbooks/checkout-high-latency.md",
                    "# Checkout latency\n\nCheck the deployment.",
                    sha=manifest_sha,
                ),
            ),
        ) as fetch,
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload(manifest_sha),
        ) as commits,
    ):
        catalog = source.fetch_catalog()
        entry = catalog.entries[0]
        document = source.fetch_document(
            RunbookReference(
                source_name=_SOURCE.name,
                document_id=entry.document_id,
                path=entry.path,
                requested_revision=catalog.resolved_revision,
            )
        )

    assert catalog.resolved_revision == manifest_sha
    assert entry.match.alertname == "CheckoutHighLatency"
    assert entry.match.labels == (("service", "checkout"),)
    assert document.resolved_revision == manifest_sha
    assert document.content.startswith("# Checkout latency")
    assert commits.call_args.kwargs["sha"] == "main"
    assert fetch.call_args_list[0].kwargs["sha"] == manifest_sha
    assert fetch.call_args_list[1].kwargs["sha"] == manifest_sha


def test_invalid_manifest_is_reported() -> None:
    source = GitHubRunbookSource(_SOURCE, _GITHUB)

    with (
        patch(
            "integrations.github.runbooks.source.get_github_file_contents",
            return_value=_file_payload(
                ".opensre/runbooks.yaml",
                "version: 1\nrunbooks:\n  - id: unsafe\n    document: ../secret.md",
                sha="a" * 40,
            ),
        ),
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload("a" * 40),
        ),
        pytest.raises(RunbookRetrievalError, match="manifest is invalid"),
    ):
        source.fetch_catalog()


def test_oversized_manifest_is_rejected_before_parsing() -> None:
    source = GitHubRunbookSource(_SOURCE, _GITHUB)

    with (
        patch(
            "integrations.github.runbooks.source.get_github_file_contents",
            return_value=_file_payload(
                ".opensre/runbooks.yaml",
                "x" * (RUNBOOK_MANIFEST_MAX_CHARS + 1),
                sha="a" * 40,
            ),
        ),
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload("a" * 40),
        ),
        pytest.raises(RunbookRetrievalError, match="manifest is invalid"),
    ):
        source.fetch_catalog()


def test_fetch_document_truncates_bounded_content() -> None:
    source = GitHubRunbookSource(_SOURCE, _GITHUB)
    reference = RunbookReference(
        source_name=_SOURCE.name,
        document_id="long",
        path="runbooks/long.md",
        requested_revision="main",
    )

    with (
        patch(
            "integrations.github.runbooks.source.get_github_file_contents",
            return_value=_file_payload("runbooks/long.md", "x" * 30_000, sha="a" * 40),
        ),
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload("a" * 40),
        ),
    ):
        document = source.fetch_document(reference)

    assert len(document.content) == 24_000
    assert document.truncated is True


def test_fetch_failure_uses_stable_error_without_provider_detail() -> None:
    source = GitHubRunbookSource(_SOURCE, _GITHUB)
    reference = RunbookReference(
        source_name=_SOURCE.name,
        document_id="missing",
        path="runbooks/missing.md",
    )

    with (
        patch(
            "integrations.github.runbooks.source.get_github_file_contents",
            return_value={
                "available": False,
                "error": "token ghp_secret cannot access private repository",
                "file": {},
            },
        ),
        patch(
            "integrations.github.runbooks.source.list_github_commits",
            return_value=_commits_payload("a" * 40),
        ),
        pytest.raises(RunbookRetrievalError) as raised,
    ):
        source.fetch_document(reference)

    assert "ghp_secret" not in str(raised.value)


def test_verify_checks_access_for_sha_pinned_source_without_manifest() -> None:
    revision = "a" * 40
    source = GitHubRunbookSource(
        RunbookSourceConfig(
            name="pinned-runbook",
            provider="github",
            repository="acme/operations",
            ref=revision,
            manifest="",
        ),
        _GITHUB,
    )

    with patch(
        "integrations.github.runbooks.source.list_github_commits",
        return_value={"available": False, "error": "repository not found"},
    ) as commits:
        verified, message = source.verify()

    assert verified is False
    assert message == "GitHub could not verify access to the configured repository."
    assert commits.call_args.kwargs["sha"] == revision
