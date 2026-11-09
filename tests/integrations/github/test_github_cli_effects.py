"""Only ``gh`` calls that cannot change anything are reported as reads."""

from __future__ import annotations

import pytest

from integrations.github.tools.github_cli.effects import gh_call_only_reads


@pytest.mark.parametrize(
    "args, reads",
    [
        (["pr", "view", "6555", "--json", "title"], True),
        (["run", "list", "--limit", "5"], True),
        (["search", "prs", "is:open"], True),
        (["api", "repos/o/r/pulls"], True),
        (["api", "-X", "GET", "search/issues", "-f", "q=is:open"], True),
        (["api", "graphql", "-f", "query=query { viewer { login } }"], True),
        (["pr", "comment", "6555", "--body", "Needs a decision."], False),
        (["pr", "merge", "6555", "--squash"], False),
        (["api", "repos/o/r/issues/1/comments", "-f", "body=hi"], False),
        (["api", "--method=PATCH", "repos/o/r/pulls/1"], False),
        (["api", "-XPOST", "repos/o/r/dispatches"], False),
        (
            ["api", "graphql", "-f", "query=mutation { addStar(input: {}) { clientMutationId } }"],
            False,
        ),
        (["api", "graphql", "-F", "query=@mutation.graphql"], False),
    ],
)
def test_a_gh_call_is_a_read_only_when_it_cannot_change_anything(
    args: list[str], reads: bool
) -> None:
    assert gh_call_only_reads(args) is reads
