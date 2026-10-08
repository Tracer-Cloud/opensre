"""Validated client for hydrating the local store from the credentials API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from config.constants.account import (
    INTEGRATION_IS_DEFAULT_TAG,
    INTEGRATION_OWNER_ID_TAG,
    INTEGRATION_OWNER_KIND_TAG,
)
from config.constants.github import GITHUB_CONNECTION_ORIGIN_TAG, GITHUB_PROVENANCE_PARAM


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

    def visible_to(self, *, user_id: str | None, organization_id: str | None) -> IntegrationStoreV2:
        """Keep only connections owned by ``user_id`` or ``organization_id``.

        Records left with no visible connection are dropped, so a personal
        grant never travels alongside a workspace one it happens to share a
        record with.
        """
        integrations: list[IntegrationRecordV2] = []
        for record in self.integrations:
            instances = [
                instance
                for instance in record.instances
                if connection_visible(
                    instance.tags, user_id=user_id, organization_id=organization_id
                )
            ]
            if instances or not record.instances:
                integrations.append(record.model_copy(update={"instances": instances}))
        return self.model_copy(update={"integrations": integrations})


def connection_visible(
    tags: Mapping[str, object], *, user_id: str | None, organization_id: str | None
) -> bool:
    """Whether a connection's owner tags admit this caller.

    Untagged connections predate ownership and stay server-authorized. A tagged
    one is visible only to the Clerk user or organization it names; an unknown
    owner kind fails closed.
    """
    kind = tags.get(INTEGRATION_OWNER_KIND_TAG)
    if not kind:
        return True
    owner_id = tags.get(INTEGRATION_OWNER_ID_TAG)
    if kind == "user":
        return user_id is not None and owner_id == user_id
    if kind == "organization":
        return organization_id is not None and owner_id == organization_id
    return False


class IntegrationOwner(BaseModel):
    """Principal that owns one hosted integration connection."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["user", "organization"]
    id: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("id must not be blank")
        return value


class AgentVaultRecord(BaseModel):
    """One decrypted connection returned by the webapp account vault."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    status: str = Field(min_length=1)
    name: str = Field(min_length=1)
    credentials: dict[str, JsonValue]
    owner: IntegrationOwner | None = None
    is_default: bool = False
    connection_origin: Literal["webapp", "cli", "unknown"] = "unknown"

    def tags(self) -> dict[str, str]:
        """Owner and default flag as store-instance tags."""
        tags: dict[str, str] = {}
        if self.service == "github":
            tags[GITHUB_CONNECTION_ORIGIN_TAG] = self.connection_origin
        if self.owner is not None:
            tags[INTEGRATION_OWNER_KIND_TAG] = self.owner.kind
            tags[INTEGRATION_OWNER_ID_TAG] = self.owner.id
        if self.is_default:
            tags[INTEGRATION_IS_DEFAULT_TAG] = "true"
        return tags


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
                            tags=record.tags(),
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
        path = str(httpx.URL(path).copy_merge_params({GITHUB_PROVENANCE_PARAM: "1"}))
        try:
            response = self._client.get(
                path,
                headers={"Authorization": self._authorization_header},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise CredentialsApiError("Unable to retrieve organization credentials") from None
        # A silo serves the whole organization; a member's personal grant must
        # never be materialized into its shared store.
        return validate_integration_store_v2(payload).visible_to(
            user_id=None, organization_id=organization_id
        )

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
