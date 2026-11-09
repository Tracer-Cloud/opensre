"""One GraphQL read of the viewer's organizations for a CI repair demo."""

from __future__ import annotations

from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient

GRAPHQL_PATH = "/graphql"
_ORGS_PAGE_SIZE = 100
_MAX_ORG_PAGES = 10

#: Membership role and state come from the organization node: GitHub's
#: organizations connection has no per-edge role field. ``viewerCanAdminister``
#: is admin, otherwise member. ``viewerIsAMember`` is an active membership.
REPAIR_ACCESS_QUERY = f"""
query RepairAccess($after: String) {{
  viewer {{
    login
    organizations(first: {_ORGS_PAGE_SIZE}, after: $after) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        login
        viewerCanAdminister
        viewerCanCreateRepositories
        viewerIsAMember
      }}
    }}
  }}
}}
"""


def fetch_organization_nodes(client: GitHubRestClient) -> list[dict[str, Any]]:
    """Every organization from one ``organizations`` query, following its cursor."""
    nodes: list[dict[str, Any]] = []
    after: str | None = None
    for _page in range(_MAX_ORG_PAGES):
        page, cursor = _organizations_page(post_graphql(client, {"after": after}))
        nodes.extend(page)
        if cursor is None or cursor == after:
            return nodes
        after = cursor
    return nodes


def owner_entries(
    login: str,
    nodes: list[dict[str, Any]],
    *,
    login_can_create: bool,
) -> list[dict[str, Any]]:
    """The viewer, then one entry per organization."""
    owners: list[dict[str, Any]] = [{"login": login, "can_create_repositories": login_can_create}]
    for node in nodes:
        org = str(node.get("login") or "").strip()
        if not org:
            continue
        owners.append(
            {
                "login": org,
                "can_create_repositories": node.get("viewerCanCreateRepositories") is True,
                "role": "admin" if node.get("viewerCanAdminister") is True else "member",
                "state": "active" if node.get("viewerIsAMember") is True else "pending",
            }
        )
    return owners


def post_graphql(client: GitHubRestClient, variables: dict[str, Any]) -> dict[str, Any]:
    """POST the repair-access document and return its ``data`` object."""
    payload = client.request(
        "POST",
        GRAPHQL_PATH,
        body={"query": REPAIR_ACCESS_QUERY, "variables": variables},
    )
    if not isinstance(payload, dict):
        raise GitHubApiError(
            "GitHub GraphQL returned a non-object payload.",
            path=GRAPHQL_PATH,
            method="POST",
        )
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("viewer"), dict):
        raise GitHubApiError("GitHub GraphQL request failed.", path=GRAPHQL_PATH, method="POST")
    return data


def _organizations_page(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    viewer = data.get("viewer")
    connection = viewer.get("organizations") if isinstance(viewer, dict) else None
    if not isinstance(connection, dict):
        raise GitHubApiError("GitHub GraphQL request failed.", path=GRAPHQL_PATH, method="POST")
    raw_nodes = connection.get("nodes")
    nodes = (
        [node for node in raw_nodes if isinstance(node, dict)]
        if isinstance(raw_nodes, list)
        else []
    )
    page_info = connection.get("pageInfo")
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
        return nodes, None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor.strip():
        return nodes, None
    return nodes, cursor


__all__ = [
    "GRAPHQL_PATH",
    "REPAIR_ACCESS_QUERY",
    "fetch_organization_nodes",
    "owner_entries",
    "post_graphql",
]
