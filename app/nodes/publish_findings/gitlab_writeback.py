"""GitLab MR write-back helper for the publish_findings node."""

import logging
import os

from app.integrations.gitlab import build_gitlab_config, post_gitlab_mr_note
from app.state import InvestigationState

logger = logging.getLogger(__name__)


def _build_mr_note(report: str) -> str:
    body = report.strip()
    if len(body) > 4000:
        body = body[:3997] + "..."
    return f"### RCA Finding\n\n<details>\n<summary>Investigation summary</summary>\n\n{body}\n\n</details>"


def post_gitlab_mr_writeback(state: InvestigationState, report: str) -> None:
    """Post an RCA summary as a GitLab MR note if write-back is enabled.

    No-ops when:
    - GITLAB_MR_WRITEBACK env var is not set to a truthy value
    - merge_request_iid or project_id are absent from state
    Failures are logged as warnings and never propagate to the caller.
    """
    if os.getenv("GITLAB_MR_WRITEBACK", "").lower() not in ("true", "1", "yes"):
        return

    gl = (state.get("available_sources") or {}).get("gitlab", {})
    mr_iid = gl.get("merge_request_iid", "")
    project_id = gl.get("project_id", "")

    if not mr_iid or not project_id:
        return

    try:
        gl_config = build_gitlab_config(
            {
                "base_url": gl.get("gitlab_url", ""),
                "auth_token": gl.get("gitlab_token", ""),
            }
        )
        post_gitlab_mr_note(
            config=gl_config,
            project_id=project_id,
            mr_iid=mr_iid,
            body=_build_mr_note(report),
        )
        logger.info("[publish] GitLab MR note posted: project=%s mr_iid=%s", project_id, mr_iid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[publish] GitLab MR write-back failed: %s", exc)
