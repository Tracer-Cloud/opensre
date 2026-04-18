import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

DEFAULT_TRELLO_BASE_URL = "https://api.trello.com/1"


class TrelloConfig(BaseModel):
    """Normalized Trello connection settings."""

    base_url: str = DEFAULT_TRELLO_BASE_URL
    api_key: str = ""
    token: str = ""
    board_id: str = ""
    list_id: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_TRELLO_BASE_URL).strip()
        return normalized or DEFAULT_TRELLO_BASE_URL

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass(frozen=True)
class TrelloValidationResult:
    """Result of validating a Trello integration."""

    ok: bool
    detail: str


def build_trello_config(raw: dict[str, Any] | None) -> TrelloConfig:
    """Build a normalized Trello config object from env/store data."""
    return TrelloConfig.model_validate(raw or {})


def trello_config_from_env() -> TrelloConfig | None:
    """Load a Trello config from env vars."""
    api_key = os.getenv("TRELLO_API_KEY", "").strip()
    token = os.getenv("TRELLO_TOKEN", "").strip()
    if not api_key or not token:
        return None

    return build_trello_config({
        "base_url": os.getenv("TRELLO_BASE_URL", DEFAULT_TRELLO_BASE_URL).strip()
        or DEFAULT_TRELLO_BASE_URL,
        "api_key": api_key,
        "token": token,
        "board_id": os.getenv("TRELLO_BOARD_ID", "").strip(),
        "list_id": os.getenv("TRELLO_LIST_ID", "").strip(),
    })


def _request_json(
    config: TrelloConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    request_params: list[tuple[str, str | int | float | bool | None]] = [
        ("key", config.api_key),
        ("token", config.token),
    ]
    if params:
        request_params.extend(params)

    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        params=request_params,
        json=json,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def validate_trello_connection(
    *,
    config: TrelloConfig,
) -> dict[str, Any]:
    """Validate Trello connection with a lightweight member query."""
    payload = _request_json(config, "GET", "/members/me")
    return payload if isinstance(payload, dict) else {}


def validate_trello_config(config: TrelloConfig) -> TrelloValidationResult:
    """Validate Trello connectivity."""
    if not config.api_key:
        return TrelloValidationResult(ok=False, detail="Trello API key is required.")
    if not config.token:
        return TrelloValidationResult(ok=False, detail="Trello token is required.")

    try:
        member = validate_trello_connection(config=config)
        username = member.get("username", "unknown")
        return TrelloValidationResult(
            ok=True,
            detail=f"Trello connectivity successful. Authenticated as @{username}",
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return TrelloValidationResult(ok=False, detail=f"Trello validation failed: {detail}")
    except Exception as err:  # noqa: BLE001
        return TrelloValidationResult(ok=False, detail=f"Trello validation failed: {err}")


def create_trello_card(
    *,
    config: TrelloConfig,
    name: str,
    desc: str,
    list_id: str | None = None,
) -> dict[str, Any]:
    """Create a Trello card."""
    target_list_id = (list_id or config.list_id).strip()
    if not target_list_id:
        raise ValueError("A list_id must be provided either via argument or config.")

    payload = _request_json(
        config,
        "POST",
        "/cards",
        params=[
            ("idList", target_list_id),
        ],
        json={
            "name": name,
            "desc": desc,
        },
    )
    return payload if isinstance(payload, dict) else {}
