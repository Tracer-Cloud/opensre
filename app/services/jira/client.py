"""Jira REST API v3 client for creating and updating incident tickets."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.integrations.models import JiraIntegrationConfig

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30

class JiraClient:
    """Client for filing and updating Jira issues from investigation findings."""

    def __init__(self, config: JiraIntegrationConfig) -> None:
        self.config = config

    @property
    def is_configured(self) -> bool:
        return bool(
            self.config.base_url
            and self.config.email
            and self.config.api_token
        )

    def _get_client(self) -> httpx.Client:
        return httpx.Client(
            auth=(self.config.email, self.config.api_token),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=_DEFAULT_TIMEOUT,
        )

    def create_issue(
        self,
        summary: str,
        description: str,
        issue_type: str = "Bug",
        priority: str = "High",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new Jira issue with investigation findings."""
        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": self.config.project_key},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                },
                "issuetype": {"name": issue_type},
                "priority": {"name": priority},
            }
        }
        if labels:
            payload["fields"]["labels"] = labels

        try:
            with self._get_client() as client:
                resp = client.post(
                    f"{self.config.api_base}/issue",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "success": True,
                    "issue_key": data.get("key"),
                    "issue_id": data.get("id"),
                    "url": f"{self.config.base_url}/browse/{data.get('key')}",
                }
        except httpx.HTTPStatusError as e:
            logger.warning("[jira] Failed to create issue status=%s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[jira] Create issue error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def update_issue(
        self,
        issue_key: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Update fields on an existing Jira issue."""
        try:
            with self._get_client() as client:
                resp = client.put(
                    f"{self.config.api_base}/issue/{issue_key}",
                    json={"fields": fields},
                )
                resp.raise_for_status()
                return {"success": True, "issue_key": issue_key}
        except httpx.HTTPStatusError as e:
            logger.warning("[jira] Failed to update issue=%s status=%s", issue_key, e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[jira] Update issue error: %s", e)
            return {"success": False, "error": str(e)}

    def add_comment(
        self,
        issue_key: str,
        body: str,
    ) -> dict[str, Any]:
        """Append an investigation summary as a comment on an existing Jira issue."""
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        }
        try:
            with self._get_client() as client:
                resp = client.post(
                    f"{self.config.api_base}/issue/{issue_key}/comment",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return {"success": True, "comment_id": data.get("id")}
        except httpx.HTTPStatusError as e:
            logger.warning("[jira] Failed to add comment issue=%s status=%s", issue_key, e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[jira] Add comment error: %s", e)
            return {"success": False, "error": str(e)}

    def search_issues(
        self,
        jql: str = "",
        max_results: int = 20,
    ) -> dict[str, Any]:
        """Search Jira issues via JQL to find related incidents, bugs, or tasks."""
        if not jql and self.config.project_key:
            jql = f"project = {self.config.project_key} ORDER BY updated DESC"
        elif not jql:
            jql = "ORDER BY updated DESC"

        payload: dict[str, Any] = {
            "jql": jql,
            "maxResults": min(max_results, 100),
            "fields": [
                "summary",
                "status",
                "priority",
                "labels",
                "created",
                "updated",
                "assignee",
            ],
        }

        try:
            with self._get_client() as client:
                resp = client.post(
                    f"{self.config.api_base}/issue/search",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                issues = []
                for item in data.get("issues", []):
                    fields = item.get("fields", {})
                    assignee = fields.get("assignee") or {}
                    issues.append(
                        {
                            "issue_key": item.get("key", ""),
                            "summary": fields.get("summary", ""),
                            "status": (fields.get("status") or {}).get("name", ""),
                            "priority": (fields.get("priority") or {}).get("name", ""),
                            "labels": fields.get("labels", []),
                            "assignee": assignee.get("displayName", ""),
                            "created": fields.get("created", ""),
                            "updated": fields.get("updated", ""),
                        }
                    )

                return {
                    "success": True,
                    "issues": issues,
                    "total": data.get("total", len(issues)),
                }
        except httpx.HTTPStatusError as e:
            logger.warning("[jira] Search issues HTTP failure status=%s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[jira] Search issues error: %s", e)
            return {"success": False, "error": str(e)}

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch an existing Jira issue to pull context into the investigation."""
        try:
            with self._get_client() as client:
                resp = client.get(f"{self.config.api_base}/issue/{issue_key}")
                resp.raise_for_status()
                data = resp.json()
                fields = data.get("fields", {})
                return {
                    "success": True,
                    "issue_key": data.get("key"),
                    "summary": fields.get("summary", ""),
                    "status": (fields.get("status") or {}).get("name", ""),
                    "priority": (fields.get("priority") or {}).get("name", ""),
                    "description": fields.get("description", ""),
                    "labels": fields.get("labels", []),
                }
        except httpx.HTTPStatusError as e:
            logger.warning("[jira] Failed to get issue=%s status=%s", issue_key, e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[jira] Get issue error: %s", e)
            return {"success": False, "error": str(e)}


def make_jira_client(
    base_url: str | None,
    email: str | None,
    api_token: str | None,
    project_key: str | None = None,
) -> JiraClient | None:
    """Create a JiraClient if valid credentials are provided."""
    url = (base_url or "").strip()
    mail = (email or "").strip()
    token = (api_token or "").strip()
    if not (url and mail and token):
        return None
    try:
        config = JiraIntegrationConfig(
            base_url=url,
            email=mail,
            api_token=token,
            project_key=(project_key or "").strip(),
        )
        return JiraClient(config)
    except Exception:
        return None
