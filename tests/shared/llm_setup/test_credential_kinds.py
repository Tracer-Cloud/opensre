"""The wizard credential vocabulary stays distinct from the runtime catalog.

``WizardCredentialKind`` (how onboarding *prompts*) and the catalog
``CredentialKind`` (how the runtime *authenticates*) overlap on ``api_key`` and
``cli`` but diverge on the rest. These tests guard the translation so a future
"just merge the two enums" refactor cannot silently relabel a local Ollama host
as ambient environment credentials.
"""

from __future__ import annotations

from config.llm_auth.provider_catalog import CredentialKind
from surfaces.shared.llm_setup.catalog import (
    PROVIDER_BY_VALUE,
    WIZARD_TO_CATALOG_KIND,
    WizardCredentialKind,
)


def test_wizard_kind_values() -> None:
    assert {kind.value for kind in WizardCredentialKind} == {
        "api_key",
        "host",
        "cli",
        "none",
        "subscription",
    }
    assert WizardCredentialKind("host") is WizardCredentialKind.HOST


def test_translation_map_is_total() -> None:
    assert set(WIZARD_TO_CATALOG_KIND) == set(WizardCredentialKind)


def test_host_and_none_do_not_collapse_into_catalog_lookalikes() -> None:
    # The whole reason the two enums stay separate: these must NOT be identity.
    assert WIZARD_TO_CATALOG_KIND[WizardCredentialKind.HOST] is CredentialKind.LOCAL
    assert WIZARD_TO_CATALOG_KIND[WizardCredentialKind.NONE] is CredentialKind.AMBIENT
    # The shared members do translate as identity.
    assert WIZARD_TO_CATALOG_KIND[WizardCredentialKind.API_KEY] is CredentialKind.API_KEY
    assert WIZARD_TO_CATALOG_KIND[WizardCredentialKind.CLI] is CredentialKind.CLI


def test_ollama_is_a_host_in_the_wizard_and_local_in_the_catalog() -> None:
    ollama = PROVIDER_BY_VALUE["ollama"]
    assert ollama.credential_kind is WizardCredentialKind.HOST
    assert WIZARD_TO_CATALOG_KIND[ollama.credential_kind] is CredentialKind.LOCAL
