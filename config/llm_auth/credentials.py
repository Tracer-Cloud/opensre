"""Prompt-safe LLM auth status and request-scoped credential resolution."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from config.llm_auth.cli_auth import resolve_cli_auth_state
from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    ProviderSpec,
    provider_spec,
    require_provider_spec,
)
from config.llm_auth.records import (
    delete_provider_auth_record,
    resolve_provider_auth_record,
    save_provider_auth_record,
    save_provider_auth_record_values,
)
from config.secrets.store import SecretSaveResult


class CredentialSource(StrEnum):
    """Where a provider's credential resolves from, for prompt-safe status."""

    ENV = "env"
    #: The owner-only local credentials file (``~/.opensre/credentials.json``).
    #: Distinct from ``LOCAL``, which means a locally hosted model runtime (Ollama).
    FALLBACK = "fallback"
    METADATA = "metadata"
    SUBSCRIPTION = "subscription"
    CLI = "cli"
    AMBIENT = "ambient"
    LOCAL = "local"
    NONE = "none"
    UNKNOWN = "unknown"


class MissingLLMCredentialError(RuntimeError):
    """Raised when a selected provider lacks request-time credentials."""


@dataclass(frozen=True)
class CredentialStatus:
    """Prompt-safe status for a provider auth path."""

    provider: str
    configured: bool
    source: CredentialSource
    verified: bool
    stale: bool
    detail: str


@dataclass(frozen=True)
class _AmbientProbeResult:
    """Outcome of an actual (not env-presence-only) ambient credential check."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class CredentialResolution:
    """Request-time credential resolution for one provider."""

    provider: str
    api_key: str
    source: CredentialSource
    detail: str

    @property
    def ok(self) -> bool:
        return bool(self.api_key) or self.source in {
            CredentialSource.SUBSCRIPTION,
            CredentialSource.CLI,
            CredentialSource.AMBIENT,
            CredentialSource.LOCAL,
        }

    def __repr__(self) -> str:
        redacted = "<set>" if self.api_key else "<empty>"
        return (
            "CredentialResolution("
            f"provider={self.provider!r}, api_key={redacted}, "
            f"source={self.source!r}, detail={self.detail!r})"
        )


def _bool_record_value(record: dict[str, str], key: str, default: bool) -> bool:
    raw = record.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_source(raw: str | None, *, fallback: CredentialSource) -> CredentialSource:
    value = (raw or "").strip().lower()
    if value == "keyring":
        # Legacy metadata from the removed OS-keychain tier.
        return CredentialSource.FALLBACK
    try:
        return CredentialSource(value)
    except ValueError:
        return fallback


def _env_value(env_var: str) -> str:
    return os.getenv(env_var, "").strip()


def _probe_vertex_ai_ambient(spec: ProviderSpec) -> _AmbientProbeResult:
    """Attempt real Google ADC resolution; env vars alone do not prove ADC works."""
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    project_hint = _env_value(spec.project_env) if spec.project_env else ""
    location = _env_value(spec.location_env) if spec.location_env else ""
    location_detail = f", location {location}" if location else ""

    try:
        _credentials, discovered_project = google.auth.default()
    except DefaultCredentialsError as exc:
        return _AmbientProbeResult(
            ok=False,
            detail=(
                f"Google Application Default Credentials are not configured ({exc}). "
                "Run `gcloud auth application-default login` or set "
                "GOOGLE_APPLICATION_CREDENTIALS."
            ),
        )
    except Exception as exc:
        return _AmbientProbeResult(
            ok=False,
            detail=f"Could not resolve Google Application Default Credentials: {exc}",
        )

    project = project_hint or discovered_project
    if not project:
        return _AmbientProbeResult(
            ok=False,
            detail=(
                "Google Application Default Credentials resolved, but no GCP project "
                "could be determined (ADC did not discover one and VERTEX_AI_PROJECT is "
                "not set). Set VERTEX_AI_PROJECT — Vertex AI requests require a project."
            ),
        )
    return _AmbientProbeResult(
        ok=True,
        detail=(
            f"Google Application Default Credentials resolved (project {project}"
            f"{location_detail}); {spec.label} uses ADC."
        ),
    )


def _probe_bedrock_ambient(spec: ProviderSpec) -> _AmbientProbeResult:
    region = os.getenv("AWS_REGION", "").strip() or os.getenv("AWS_DEFAULT_REGION", "").strip()
    if not region:
        return _AmbientProbeResult(detail="AWS_REGION or AWS_DEFAULT_REGION is not set.", ok=False)
    return _AmbientProbeResult(
        ok=True,
        detail=f"AWS region is configured ({region}); {spec.label} uses the AWS credential chain.",
    )


_AMBIENT_PROBES: dict[str, Callable[[ProviderSpec], _AmbientProbeResult]] = {
    "vertex-ai": _probe_vertex_ai_ambient,
    "bedrock": _probe_bedrock_ambient,
}


def _source_status(provider: str, source: CredentialSource, detail: str) -> CredentialStatus:
    return CredentialStatus(
        provider=provider,
        configured=source not in {CredentialSource.NONE, CredentialSource.UNKNOWN},
        source=source,
        verified=source not in {CredentialSource.METADATA, CredentialSource.UNKNOWN},
        stale=False,
        detail=detail,
    )


def _record_status(spec: ProviderSpec, record: dict[str, str]) -> CredentialStatus:
    source = _normalize_source(record.get("source"), fallback=CredentialSource.METADATA)
    stale = _bool_record_value(record, "stale", False)
    verified = _bool_record_value(record, "verified", not stale)
    detail = record.get("detail") or (
        f"{spec.api_key_env} was previously saved; run `opensre auth verify {spec.value}` "
        "to confirm it is still available."
    )
    return CredentialStatus(
        provider=spec.value,
        configured=True,
        source=CredentialSource.METADATA if source is CredentialSource.FALLBACK else source,
        verified=verified and not stale,
        stale=stale,
        detail=detail,
    )


def status(provider: str) -> CredentialStatus:
    """Return prompt-safe provider auth status.

    This function must not read stored secrets. It may inspect environment,
    non-secret metadata, CLI adapter probes, and ambient/local config markers.
    """
    spec = provider_spec(provider)
    if spec is None:
        return CredentialStatus(
            provider=provider.strip().lower(),
            configured=False,
            source=CredentialSource.UNKNOWN,
            verified=False,
            stale=False,
            detail=f"Unsupported LLM provider: {provider}",
        )

    if spec.credential_kind == "subscription":
        record = resolve_provider_auth_record(spec.value)
        from config.secrets.store import keyring_is_disabled

        if record.get("source") == "subscription" and not keyring_is_disabled():
            return _record_status(spec, record)
        return CredentialStatus(
            spec.value,
            False,
            CredentialSource.NONE,
            False,
            False,
            record.get("detail") or "Run `opensre auth login chatgpt` to sign in.",
        )

    if spec.credential_kind == "api_key":
        if spec.api_key_env and _env_value(spec.api_key_env):
            return _source_status(
                spec.value,
                CredentialSource.ENV,
                f"{spec.api_key_env} is set in the environment.",
            )
        record = resolve_provider_auth_record(spec.value)
        if record:
            return _record_status(spec, record)
        return CredentialStatus(
            provider=spec.value,
            configured=False,
            source=CredentialSource.NONE,
            verified=False,
            stale=False,
            detail=f"{spec.api_key_env} is not configured.",
        )

    if spec.credential_kind == "cli":
        record = resolve_provider_auth_record(spec.value)
        record_source = _normalize_source(record.get("source"), fallback=CredentialSource.METADATA)
        stale = _bool_record_value(record, "stale", False)
        verified = _bool_record_value(record, "verified", False)
        if record and verified and not stale:
            return CredentialStatus(
                provider=spec.value,
                configured=True,
                source=CredentialSource.CLI
                if record_source
                in {CredentialSource.METADATA, CredentialSource.UNKNOWN, CredentialSource.NONE}
                else record_source,
                verified=True,
                stale=False,
                detail=record.get("detail") or f"{spec.label} auth metadata is present.",
            )
        try:
            cli_auth = resolve_cli_auth_state(spec.value)
        except Exception as exc:
            return CredentialStatus(
                spec.value,
                False,
                CredentialSource.UNKNOWN,
                False,
                False,
                f"CLI auth status could not be checked: {exc}",
            )
        if cli_auth is None:
            return CredentialStatus(
                spec.value,
                False,
                CredentialSource.NONE,
                False,
                False,
                "No CLI adapter registered.",
            )
        configured = cli_auth.installed and cli_auth.logged_in is True
        source: CredentialSource = CredentialSource.CLI if configured else CredentialSource.NONE
        return CredentialStatus(
            provider=spec.value,
            configured=configured,
            source=source,
            verified=configured,
            stale=False,
            detail=cli_auth.detail,
        )

    if spec.credential_kind == "ambient":
        ambient_probe = _AMBIENT_PROBES.get(spec.value)
        if ambient_probe is None:
            return CredentialStatus(
                spec.value,
                False,
                CredentialSource.UNKNOWN,
                False,
                False,
                "No ambient credential probe registered.",
            )
        try:
            result = ambient_probe(spec)
        except Exception as exc:
            return CredentialStatus(
                spec.value,
                False,
                CredentialSource.UNKNOWN,
                False,
                False,
                f"{spec.label} ambient credential check failed unexpectedly: {exc}",
            )
        return CredentialStatus(
            provider=spec.value,
            configured=result.ok,
            source=CredentialSource.AMBIENT if result.ok else CredentialSource.NONE,
            verified=result.ok,
            stale=False,
            detail=result.detail,
        )

    if spec.credential_kind == "local":
        host = os.getenv("OLLAMA_HOST", "").strip() or "http://localhost:11434"
        return CredentialStatus(
            provider=spec.value,
            configured=True,
            source=CredentialSource.LOCAL,
            verified=True,
            stale=False,
            detail=f"Ollama host: {host}.",
        )

    return CredentialStatus(
        spec.value, False, CredentialSource.UNKNOWN, False, False, "Unknown auth kind."
    )


def _mark_stale(spec: ProviderSpec, detail: str) -> None:
    record = resolve_provider_auth_record(spec.value)
    if not record:
        return
    save_provider_auth_record_values(
        spec.value,
        {
            **record,
            "source": record.get("source") or "metadata",
            "detail": detail,
            "verified": "false",
            "stale": "true",
        },
    )


def resolve_for_request(provider: str) -> CredentialResolution:
    """Resolve request-time auth for exactly one selected provider."""
    spec = require_provider_spec(provider)
    if spec.credential_kind == "subscription":
        state = status(spec.value)
        return CredentialResolution(
            spec.value,
            "",
            CredentialSource.SUBSCRIPTION if state.configured else CredentialSource.NONE,
            state.detail,
        )

    if spec.credential_kind == "api_key":
        env_value = _env_value(spec.api_key_env)
        if env_value:
            return CredentialResolution(
                provider=spec.value,
                api_key=env_value,
                source=CredentialSource.ENV,
                detail=f"{spec.api_key_env} resolved from environment.",
            )

        from config.secrets.store import lookup

        found = lookup(spec.api_key_env)
        if found.value and found.tier == "fallback":
            source = CredentialSource.FALLBACK
            detail = f"{spec.api_key_env} resolved from the local credentials file."
            save_provider_auth_record(
                provider=spec.value,
                auth_name=spec.value,
                kind="api_key",
                source=source,
                detail=detail,
                verified=True,
                stale=False,
                env_var=spec.api_key_env,
            )
            return CredentialResolution(
                provider=spec.value,
                api_key=found.value,
                source=source,
                detail=detail,
            )
        detail = (
            f"Missing credential for LLM provider '{spec.value}'. Set {spec.api_key_env} "
            f"or run `opensre auth login {spec.value}`."
        )
        _mark_stale(spec, detail)
        return CredentialResolution(spec.value, "", CredentialSource.NONE, detail)

    if spec.credential_kind == "cli":
        return CredentialResolution(
            provider=spec.value,
            api_key="",
            source=CredentialSource.CLI,
            detail=f"{spec.label} uses vendor CLI authentication.",
        )
    if spec.credential_kind == "ambient":
        return CredentialResolution(
            provider=spec.value,
            api_key="",
            source=CredentialSource.AMBIENT,
            detail=f"{spec.label} uses ambient credentials.",
        )
    if spec.credential_kind == "local":
        return CredentialResolution(
            provider=spec.value,
            api_key="",
            source=CredentialSource.LOCAL,
            detail=f"{spec.label} uses local runtime configuration.",
        )
    return CredentialResolution(
        spec.value, "", CredentialSource.UNKNOWN, "Unsupported provider auth kind."
    )


def require_for_request(provider: str) -> CredentialResolution:
    """Resolve request-time auth or raise an actionable error."""
    resolution = resolve_for_request(provider)
    if not resolution.ok:
        raise MissingLLMCredentialError(resolution.detail)
    return resolution


def resolve_api_key_env_for_request(env_var: str) -> str:
    """Resolve one API-key env var through the request-time provider boundary."""
    normalized = env_var.strip()
    for provider, provider_env in API_KEY_PROVIDER_ENVS.items():
        if provider_env == normalized:
            return resolve_for_request(provider).api_key
    from config.llm_credentials import resolve_env_credential

    return resolve_env_credential(normalized)


def save_api_key(provider: str, value: str, *, detail: str | None = None) -> SecretSaveResult:
    """Store an OpenSRE-managed API key and refresh prompt-safe metadata.

    Returns which tier accepted the write so onboarding can tell the user when
    the credential landed in the local credentials file.
    """
    spec = require_provider_spec(provider)
    if not spec.uses_open_sre_api_key:
        raise ValueError(f"{spec.value} does not use an OpenSRE-managed API key")
    from config.secrets.store import save_secret

    result = save_secret(spec.api_key_env, value)
    source = CredentialSource.FALLBACK
    save_provider_auth_record(
        provider=spec.value,
        auth_name=spec.value,
        kind="api_key",
        source=source,
        detail=detail or result.detail,
        verified=True,
        stale=False,
        env_var=spec.api_key_env,
    )
    return result


def delete(provider: str) -> None:
    """Delete OpenSRE-managed provider auth metadata and API key when applicable."""
    spec = require_provider_spec(provider)
    if spec.uses_open_sre_api_key:
        from config.secrets.store import delete_secret

        delete_secret(spec.api_key_env)
    delete_provider_auth_record(spec.value)


def verify(provider: str) -> CredentialStatus:
    """Intentionally check request-time credentials and update metadata."""
    resolution = resolve_for_request(provider)
    if resolution.ok:
        return CredentialStatus(
            provider=resolution.provider,
            configured=True,
            source=resolution.source,
            verified=True,
            stale=False,
            detail=resolution.detail,
        )
    return status(provider)


def source_for_api_key_env(env_var: str) -> CredentialSource:
    """Prompt-safe source lookup for legacy env-var-based callers."""
    normalized = env_var.strip()
    if _env_value(normalized):
        return CredentialSource.ENV
    for provider, provider_env in API_KEY_PROVIDER_ENVS.items():
        if provider_env == normalized:
            provider_status = status(provider)
            return provider_status.source if provider_status.configured else CredentialSource.NONE
    return CredentialSource.NONE


def has_api_key_env_status(env_var: str) -> bool:
    """Prompt-safe availability check for legacy env-var-based callers."""
    return source_for_api_key_env(env_var) != "none"


def llm_api_key_source(env_var: str) -> str:
    """Return where an LLM credential resolves from: ``env``, ``fallback``, or ``none``.

    Goes through the secret store (env, then the local credentials file).
    """
    from config.secrets.store import secret_source

    prompt_safe_source = source_for_api_key_env(env_var)
    if prompt_safe_source != "none":
        return prompt_safe_source
    if env_var.strip() in set(API_KEY_PROVIDER_ENVS.values()):
        return "none"
    if os.getenv(env_var, "").strip():
        return "env"
    tier = secret_source(env_var)
    return tier.value if hasattr(tier, "value") else str(tier)


def has_llm_api_key(env_var: str) -> bool:
    """Return True when an API key is available from env or secure local storage."""
    from config.secrets.store import secret_source

    if has_api_key_env_status(env_var):
        return True
    if env_var.strip() in set(API_KEY_PROVIDER_ENVS.values()):
        return False
    if os.getenv(env_var, "").strip():
        return True
    return secret_source(env_var) != "none"


__all__ = [
    "CredentialResolution",
    "CredentialSource",
    "CredentialStatus",
    "MissingLLMCredentialError",
    "delete",
    "has_api_key_env_status",
    "has_llm_api_key",
    "llm_api_key_source",
    "require_for_request",
    "resolve_api_key_env_for_request",
    "resolve_for_request",
    "save_api_key",
    "source_for_api_key_env",
    "status",
    "verify",
]
