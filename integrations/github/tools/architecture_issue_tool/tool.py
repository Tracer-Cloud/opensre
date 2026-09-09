"""Action-surface architecture audit tools (clone, cleanup, save observations)."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.github.tools.architecture_issue_tool.repo_workspace import (
    WorkspaceError,
    architecture_workspace_dir,
    cleanup_architecture_workspace,
    clone_github_repo,
)
from integrations.github.tools.architecture_issue_tool.report_persistence import (
    ReportPersistenceError,
    save_architecture_observations,
)
from integrations.github.tools.github_cli.credentials import (
    GITHUB_CLI_INJECTED_PARAMS,
    github_creds,
    github_source_available,
    resolve_github_token,
)


def _github_clone_available(sources: dict[str, dict]) -> bool:
    return bool(github_source_available(sources) or resolve_github_token(None))


def _github_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    payload: dict[str, Any] = {**github_creds(gh)}
    if gh.get("owner"):
        payload["owner"] = gh.get("owner")
    if gh.get("repo"):
        payload["repo"] = gh.get("repo")
    return payload


def _always_available(_sources: dict[str, dict]) -> bool:
    return True


def _session_id_from_runtime(context: Any, explicit: str = "") -> str:
    sid = (explicit or "").strip()
    if sid:
        return sid
    if context is None:
        return ""
    try:
        action_ctx = action_context_from_agent_context(context)
    except RuntimeError:
        session = getattr(context, "session", None)
        return str(getattr(session, "session_id", "") or "").strip()
    return str(getattr(action_ctx.session, "session_id", "") or "").strip()


@tool(
    name="architecture_clone_repo",
    source="github",
    description=(
        "Shallow-clone a GitHub repository into "
        "opensre/workspace under the system temp directory "
        "for an architecture audit. "
        "Always call architecture_cleanup_repo when finished, then "
        "architecture_save_observations before the final report."
    ),
    use_cases=[
        "Preparing a local clone before architecture shell heuristic passes",
        "Architecture audit workflow: clone then shell passes then cleanup",
    ],
    anti_examples=[
        "Leaving the clone on disk after the audit",
        "Cloning outside the architecture workspace",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {
                "type": "string",
                "description": "GitHub repository owner or organization.",
            },
            "repo": {
                "type": "string",
                "description": "GitHub repository name.",
            },
            "ref": {
                "type": "string",
                "description": "Optional branch, tag, or commit SHA to clone.",
            },
            "github_token": {
                "type": "string",
                "description": "Optional GitHub token override for private clones.",
            },
        },
        "required": ["owner", "repo"],
        "additionalProperties": False,
    },
    is_available=_github_clone_available,
    extract_params=_github_extract_params,
    injected_params=GITHUB_CLI_INJECTED_PARAMS,
)
def architecture_clone_repo(
    owner: str,
    repo: str,
    ref: str = "",
    github_token: str | None = None,
    local_path: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Clone owner/repo into the fixed architecture workspace."""
    try:
        workspace = clone_github_repo(
            owner,
            repo,
            ref=ref,
            token=resolve_github_token(github_token) or None,
            local_path=local_path,
        )
    except WorkspaceError as exc:
        return {
            "ok": False,
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "error": str(exc),
            "workspace_root": "",
        }
    return {
        "ok": True,
        "owner": workspace.owner,
        "repo": workspace.repo,
        "ref": workspace.ref,
        "workspace_root": str(workspace.root),
        "error": "",
    }


@tool(
    name="architecture_cleanup_repo",
    source="github",
    description=(
        "Delete opensre/workspace under the system temp directory "
        "after an architecture audit. "
        "Refuses paths outside that directory."
    ),
    use_cases=["Cleanup after architecture_clone_repo"],
    anti_examples=["Deleting arbitrary paths outside the architecture workspace"],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "workspace_root": {
                "type": "string",
                "description": "Optional path; must be under the architecture workspace.",
            }
        },
        "required": [],
        "additionalProperties": False,
    },
    is_available=_always_available,
)
def architecture_cleanup_repo(
    workspace_root: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Remove the architecture clone workspace."""
    try:
        removed = cleanup_architecture_workspace(
            path=workspace_root or architecture_workspace_dir()
        )
    except WorkspaceError as exc:
        return {"ok": False, "removed_path": "", "error": str(exc)}
    return {"ok": True, "removed_path": str(removed), "error": ""}


@tool(
    name="architecture_save_observations",
    source="github",
    description=(
        "Save the full architecture-audit observation list to "
        "~/.opensre/{session_id}/{repo_name}-architecture-audit-{uuid}.md. "
        "Call after architecture_cleanup_repo and before the final summarized "
        "report. Pass the complete untruncated findings from all four shell "
        "passes (import, placement, size, shim), not the short user report."
    ),
    use_cases=[
        "Persisting full architecture audit evidence after shell passes",
        "Writing the complete observation list alongside the summarized report",
    ],
    anti_examples=[
        "Saving only the short user-facing report template",
        "Skipping this save after an architecture audit",
    ],
    requires=["repo_name", "observations"],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    input_schema={
        "type": "object",
        "properties": {
            "repo_name": {
                "type": "string",
                "description": "Repository name or owner/repo slug used in the filename.",
            },
            "observations": {
                "type": "string",
                "description": (
                    "Full markdown list of observations from all four audit "
                    "passes (untruncated findings)."
                ),
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Optional session id override. Defaults to the active "
                    "interactive-shell session."
                ),
            },
        },
        "required": ["repo_name", "observations"],
        "additionalProperties": False,
    },
    is_available=_always_available,
)
def architecture_save_observations(
    repo_name: str,
    observations: str,
    session_id: str = "",
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Persist the full architecture audit observation list for this session."""
    resolved_session_id = _session_id_from_runtime(context, session_id)
    if not resolved_session_id:
        return {
            "ok": False,
            "path": "",
            "session_id": "",
            "repo_name": repo_name,
            "error": "session_id is unavailable; cannot save architecture observations",
        }
    try:
        path = save_architecture_observations(
            session_id=resolved_session_id,
            repo_name=repo_name,
            observations=observations,
        )
    except ReportPersistenceError as exc:
        return {
            "ok": False,
            "path": "",
            "session_id": resolved_session_id,
            "repo_name": repo_name,
            "error": str(exc),
        }
    return {
        "ok": True,
        "path": str(path),
        "session_id": resolved_session_id,
        "repo_name": repo_name,
        "error": "",
    }
