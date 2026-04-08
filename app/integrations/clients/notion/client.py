"""Notion API client for creating and updating investigation report pages."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionConfig(StrictConfigModel):
    api_key: str
    database_id: str

    @field_validator("api_key", "database_id", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }


class NotionClient:
    """Client for posting investigation reports and incident pages to Notion."""

    def __init__(self, config: NotionConfig) -> None:
        self.config = config

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key and self.config.database_id)

    def _get_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=_NOTION_API_BASE,
            headers=self.config.headers,
            timeout=_DEFAULT_TIMEOUT,
        )

    def create_investigation_page(
        self,
        title: str,
        root_cause: str,
        evidence: str,
        timeline: str,
        suggested_actions: str,
        severity: str = "unknown",
    ) -> dict[str, Any]:
        """Create a new Notion page in the configured database with investigation findings."""
        payload = {
            "parent": {"database_id": self.config.database_id},
            "properties": {
                "Title": {
                    "title": [{"text": {"content": title}}]
                },
                "Severity": {
                    "rich_text": [{"text": {"content": severity}}]
                },
            },
            "children": [
                _heading("Root Cause"),
                _paragraph(root_cause),
                _heading("Evidence"),
                _paragraph(evidence),
                _heading("Timeline"),
                _paragraph(timeline),
                _heading("Suggested Actions"),
                _paragraph(suggested_actions),
            ],
        }

        try:
            with self._get_client() as client:
                resp = client.post("/pages", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "success": True,
                    "page_id": data.get("id"),
                    "url": data.get("url"),
                }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[notion] Failed to create page status=%s",
                e.response.status_code,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[notion] Request error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def update_page(
        self,
        page_id: str,
        content: str,
    ) -> dict[str, Any]:
        """Append content blocks to an existing Notion page."""
        payload = {
            "children": [_paragraph(content)],
        }
        try:
            with self._get_client() as client:
                resp = client.patch(f"/blocks/{page_id}/children", json=payload)
                resp.raise_for_status()
                return {"success": True, "page_id": page_id}
        except httpx.HTTPStatusError as e:
            logger.warning("[notion] Failed to update page status=%s", e.response.status_code)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[notion] Update error: %s", e)
            return {"success": False, "error": str(e)}


def _heading(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        },
    }
