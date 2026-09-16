"""Tests for GitHub workspace identity helpers."""

from __future__ import annotations

import pytest

from integrations.github.identity import workspace_public_repository_source


@pytest.mark.parametrize(
    ("workspace_repo", "expected"),
    [
        ("Tracer-Cloud/opensre", ("Tracer-Cloud", "opensre")),
        ("https://github.com/Tracer-Cloud/opensre.git", ("Tracer-Cloud", "opensre")),
        ("git@github.com:Tracer-Cloud/opensre.git", ("Tracer-Cloud", "opensre")),
        ("", None),
        ("owner-only", None),
        ("owner/repo/extra", None),
        ("./repo", None),
        ("../repo", None),
        ("foo\\bar/baz", None),
        ("git@gitlab.com:acme/infra", None),
        ("git@bitbucket.org:team/svc", None),
    ],
)
def test_workspace_public_repository_source_accepts_only_owner_repo(
    workspace_repo: str,
    expected: tuple[str, str] | None,
) -> None:
    source = workspace_public_repository_source({"workspace_repo": workspace_repo})

    if expected is None:
        assert source == {}
    else:
        assert source == {
            "github": {
                "connection_verified": False,
                "public_repository": True,
                "owner": expected[0],
                "repo": expected[1],
            }
        }
