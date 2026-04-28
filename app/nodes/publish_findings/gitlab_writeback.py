"""GitLab MR write-back helper for the publish_findings node."""

import logging
import os

from app.integrations.gitlab import build_gitlab_config, post_gitlab_mr_note
from app.state import InvestigationState

logger = logging.getLogger(__name__)


def _build_mr_note(slack_message: str) -> str:
    body = slack_message.strip()
    if len(body) > 4000:
        body = body[:3997] + "..."
    return f"### RCA Finding\n\n<details>\n<summary>Investigation summary</summary>\n\n{body}\n\n</details>"


def post_gitlab_mr_writeback(state: InvestigationState, slack_message: str) -> None:
    """Post an RCA summary as a GitLab MR note if write-back is enabled.

    No-ops when:
    - GITLAB_MR_WRITEBACK env var is not set to a truthy value
    - merge_request_iid or project_id are absent from state
    Failures are logged as warnings and never propagate to the caller.
    """
    if os.getenv("GITLAB_MR_WRITEBACK", "").lower() not in ("true", "1", "yes"):
        return

    _gl = (state.get("available_sources") or {}).get("gitlab", {})
    _mr_iid = _gl.get("merge_request_iid", "")
    _project_id = _gl.get("project_id", "")

    if not _mr_iid or not _project_id:
        return

    try:
        _gl_config = build_gitlab_config(
            {
                "base_url": _gl.get("gitlab_url", ""),
                "auth_token": _gl.get("gitlab_token", ""),
            }
        )
        post_gitlab_mr_note(
            config=_gl_config,
            project_id=_project_id,
            mr_iid=_mr_iid,
            body=_build_mr_note(slack_message),
        )
        logger.info("[publish] GitLab MR note posted: project=%s mr_iid=%s", _project_id, _mr_iid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[publish] GitLab MR write-back failed: %s", exc)
