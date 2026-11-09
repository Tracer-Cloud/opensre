from __future__ import annotations

from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    PROVIDER_BY_VALUE,
    PROVIDER_SPECS,
)
from integrations.llm_cli.registry import CLI_PROVIDER_REGISTRY
from surfaces.shared.llm_setup.catalog import SUPPORTED_PROVIDERS


def test_provider_catalog_values_are_unique() -> None:
    values = [spec.value for spec in PROVIDER_SPECS]

    assert len(values) == len(set(values))


def test_wizard_provider_options_match_catalog() -> None:
    wizard_values = {provider.value for provider in SUPPORTED_PROVIDERS}

    assert wizard_values == set(PROVIDER_BY_VALUE)


def test_cli_registry_matches_catalog_cli_providers() -> None:
    cli_values = {spec.value for spec in PROVIDER_SPECS if spec.credential_kind == "cli"}

    assert set(CLI_PROVIDER_REGISTRY) == cli_values


def test_api_key_env_map_contains_only_managed_api_key_providers() -> None:
    expected = {
        spec.value: spec.api_key_env for spec in PROVIDER_SPECS if spec.credential_kind == "api_key"
    }

    assert expected == API_KEY_PROVIDER_ENVS
