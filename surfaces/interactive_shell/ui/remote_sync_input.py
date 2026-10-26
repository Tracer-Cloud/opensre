"""Editable remote-sync setup for terminal slash commands."""

from __future__ import annotations

from functools import cache

from prompt_toolkit.application import Application

from config.constants.filestorage import DEFAULT_REMOTE_SYNC_PREFIX, DEFAULT_REMOTE_SYNC_PROVIDER
from config.local_settings import LocalSettingsError, read_section
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.providers.registry import (
    SetupExtraField,
    credential_hint_for_provider,
    provider_extra_fields,
    registered_providers,
)
from infrastructure.filestorage.setup import (
    RemoteSyncSetupError,
    RemoteSyncSetupRequest,
    validate_remote_sync_settings,
)
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)


def setup_request(values: dict[str, str]) -> RemoteSyncSetupRequest:
    """Validate a complete draft with the same rules as direct setup commands."""
    return validate_remote_sync_settings(
        RemoteSyncSetupRequest(
            bucket=values.get("bucket", ""),
            provider=values.get("provider", DEFAULT_REMOTE_SYNC_PROVIDER),
            prefix=values.get("prefix", DEFAULT_REMOTE_SYNC_PREFIX),
            region=values.get("region", ""),
            profile=values.get("profile", ""),
            enabled=values.get("enabled", "true") == "true",
        )
    )


def build_remote_sync_form(options: dict[str, str]) -> Application[RemoteSyncSetupRequest | None]:
    """Prefill stored settings once; editing and validation never persist or connect."""
    defaults = {
        "provider": DEFAULT_REMOTE_SYNC_PROVIDER,
        "bucket": "",
        "prefix": DEFAULT_REMOTE_SYNC_PREFIX,
        "region": "",
        "profile": "",
        "enabled": "true",
    }
    try:
        stored = read_section("remote_sync")
    except LocalSettingsError as exc:
        raise RemoteSyncConfigError(str(exc)) from exc
    values = dict(defaults)
    for name in defaults:
        value = stored.get(name)
        if value is None:
            continue
        if not isinstance(value, str | bool | int | float):
            raise RemoteSyncConfigError(f"remote_sync.{name} must be a single value")
        values[name] = str(value).strip()
    values.update(options)
    values["provider"] = values["provider"].strip().lower() or DEFAULT_REMOTE_SYNC_PROVIDER
    values["enabled"] = (
        "true" if values["enabled"].lower() in {"true", "1", "yes", "on"} else "false"
    )
    original_provider = values["provider"]
    providers = registered_providers()

    @cache
    def extras(provider: str) -> dict[str, SetupExtraField]:
        return {extra.field.value: extra for extra in provider_extra_fields(provider)}

    @cache
    def credentials(provider: str) -> str:
        return credential_hint_for_provider(provider)

    def fields(current: dict[str, str]) -> list[InputField]:
        provider = current["provider"]
        declared = extras(provider)
        result = [
            InputField(
                "provider",
                "Provider",
                status=f"default {DEFAULT_REMOTE_SYNC_PROVIDER}",
                description=credentials(provider),
                choices=tuple(InputChoice(name, name) for name in providers),
                searchable=True,
            ),
            InputField(
                "bucket",
                "Bucket/store",
                required=True,
                description="Name of an existing private bucket, container, or Blob store you own.",
            ),
            InputField(
                "prefix",
                "Prefix",
                status=f"default {DEFAULT_REMOTE_SYNC_PREFIX}",
                advanced=True,
                description="Use the same prefix on every machine sharing this history.",
            ),
        ]
        for name in ("region", "profile"):
            extra = declared.get(name)
            if extra is not None or (provider == original_provider and current[name]):
                result.append(
                    InputField(
                        name,
                        extra.prompt if extra else name.capitalize(),
                        status="optional · ambient credentials/config when empty",
                        advanced=True,
                        description=""
                        if extra
                        else "Not used by this provider; clear to continue.",
                    )
                )
        result.append(
            InputField(
                "enabled",
                "Enabled",
                summary="Yes" if current["enabled"] == "true" else "No",
                status="default Yes",
                advanced=True,
                description="Save whether remote sync is on. No files are transferred during setup.",
                choices=(InputChoice("true", "Yes"), InputChoice("false", "No")),
            )
        )
        return result

    def validate(current: dict[str, str]) -> RemoteSyncSetupRequest:
        draft = dict(current)
        if draft["provider"] != original_provider:
            declared = extras(draft["provider"])
            for name in ("region", "profile"):
                if name not in declared:
                    draft[name] = ""
        try:
            return setup_request(draft)
        except RemoteSyncSetupError as exc:
            raise InputFormError(str(exc), field=exc.field) from exc

    return build_input_form(
        values,
        title="Remote sync setup",
        heading="Set up remote sync · settings only",
        submit_label="Save settings",
        advanced_label="Optional settings",
        fields=fields,
        validate=validate,
        initial_field="provider" if not values["bucket"] else None,
        confirmation="Use a private store. Sessions and memory may contain sensitive incident details. "
        "Credentials stay local. Environment variables override saved settings.",
        compact_confirmation="Private store only · credentials stay local · env vars override settings",
    )
