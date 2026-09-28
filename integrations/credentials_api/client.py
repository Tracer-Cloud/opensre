"""Validated client for hydrating the local store from the credentials API."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator


class CredentialsApiError(RuntimeError):
    """Raised with a generic message when remote credential retrieval fails."""


class IntegrationInstanceV2(BaseModel):
    """One strictly validated integration-store v2 instance."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    tags: dict[str, str]
    credentials: dict[str, JsonValue]

    @field_validator("name")
    @classmethod
    def _name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


class IntegrationRecordV2(BaseModel):
    """One strictly validated integration-store v2 record."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    status: str = Field(min_length=1)
    instances: list[IntegrationInstanceV2]

    @field_validator("id", "service", "status")
    @classmethod
    def _field_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value


class IntegrationStoreV2(BaseModel):
    """Exact version-two payload returned by the credentials API."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[2]
    integrations: list[IntegrationRecordV2]

    def as_store_data(self) -> dict[str, Any]:
        """Return JSON-compatible data suitable for ``integrations.store``."""
        return self.model_dump(mode="json")


class AgentVaultRecord(BaseModel):
    """One decrypted integration returned by the existing webapp vault."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    status: str = Field(min_length=1)
    name: str = Field(min_length=1)
    credentials: dict[str, JsonValue]


class AgentVaultResponse(BaseModel):
    """Current ``app.opensre.com/api/agent/integrations`` response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    success: Literal[True]
    data: list[AgentVaultRecord]

    def as_store_v2(self) -> IntegrationStoreV2:
        """Adapt the vault export to the canonical local integration store."""
        return IntegrationStoreV2(
            version=2,
            integrations=[
                IntegrationRecordV2(
                    id=record.id,
                    service=record.service,
                    status=record.status,
                    instances=[
                        IntegrationInstanceV2(
                            name=record.name,
                            tags={},
                            credentials=record.credentials,
                        )
                    ],
                )
                for record in self.data
            ],
        )


def validate_integration_store_v2(payload: object) -> IntegrationStoreV2:
    """Validate either supported credentials API response without exposing it."""
    try:
        return IntegrationStoreV2.model_validate(payload)
    except ValidationError:
        try:
            return AgentVaultResponse.model_validate(payload).as_store_v2()
        except ValidationError:
            raise CredentialsApiError(
                "Credentials API returned an invalid credential set"
            ) from None


class CredentialsApiClient:
    """Synchronous credentials API client used during Gateway startup."""

    def __init__(
        self,
        *,
        base_url: str,
        bootstrap_credential: str,
        timeout_seconds: float = 10.0,
        endpoint_template: str = ("/api/agent/integrations?organizationId={organization_id}"),
        http_client: httpx.Client | None = None,
    ) -> None:
        if not base_url.lower().startswith("https://"):
            raise ValueError("Credentials API URL must use HTTPS")
        if not bootstrap_credential:
            raise ValueError("bootstrap_credential must not be empty")
        if "{organization_id}" not in endpoint_template:
            raise ValueError("endpoint_template must contain {organization_id}")
        self._endpoint_template = endpoint_template
        self._authorization_header = f"Bearer {bootstrap_credential}"
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    def fetch(self, organization_id: str) -> IntegrationStoreV2:
        """Fetch and strictly validate one organization's credential set."""
        if not organization_id.strip():
            raise ValueError("organization_id must not be blank")
        path = self._endpoint_template.format(organization_id=quote(organization_id, safe=""))
        try:
            response = self._client.get(
                path,
                headers={"Authorization": self._authorization_header},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise CredentialsApiError("Unable to retrieve organization credentials") from None
        return validate_integration_store_v2(payload)

    def close(self) -> None:
        """Close the internally-created HTTP client."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CredentialsApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def hydrate_integration_store(
    *,
    client: CredentialsApiClient,
    organization_id: str,
) -> IntegrationStoreV2:
    """Retrieve credentials and atomically materialize the local v2 store."""
    validated = client.fetch(organization_id)
    materialize_integration_store(validated)
    return validated


def materialize_integration_store(validated: IntegrationStoreV2) -> None:
    """Atomically replace the local v2 store with an already-fetched credential set."""
    from integrations.store import replace_integrations

    store_data = validated.as_store_data()
    integrations = store_data["integrations"]
    if not isinstance(integrations, list):
        raise CredentialsApiError("Credentials API returned an invalid credential set")
    replace_integrations(integrations)
