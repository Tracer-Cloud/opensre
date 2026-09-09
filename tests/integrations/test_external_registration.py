"""Tests for out-of-tree integration spec registration."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from integrations import registry
from integrations.catalog import resolve_effective_integrations
from integrations.registry import IntegrationSpec, register_integration_spec

SERVICE = "acme_external"


@pytest.fixture
def registered_integration() -> Iterator[str]:
    """Register an external spec and remove it after the test."""
    register_integration_spec(
        IntegrationSpec(
            service=SERVICE,
            has_verifier=True,
            setup_order=99,
            direct_effective=True,
        )
    )

    yield SERVICE

    registry._EXTERNAL_SPECS[:] = [
        spec for spec in registry._EXTERNAL_SPECS if spec.service != SERVICE
    ]
    registry._rebuild_registry()


def test_registration_reaches_a_module_that_imported_the_tables_earlier(
    registered_integration: str,
) -> None:
    """``integrations.verify`` binds the service lists at import time.

    It is imported long before a plugin registers, so a registration that
    replaced the tables instead of updating them would never be seen here.
    """
    import integrations.verify as verify

    assert registered_integration in verify.SUPPORTED_VERIFY_SERVICES


def test_registration_reaches_a_second_hand_re_export(registered_integration: str) -> None:
    """``integrations.app`` re-imports the list from ``integrations.verify``."""
    import integrations.app as app

    assert registered_integration in app.SUPPORTED_VERIFY_SERVICES


def test_external_service_survives_effective_resolution(registered_integration: str) -> None:
    """The resolver preserves records for services registered at runtime."""
    effective = resolve_effective_integrations(
        store_integrations=[],
        env_integrations=[
            {
                "service": registered_integration,
                "status": "active",
                "source": "local env",
                "config": {"api_key": "test"},
            }
        ],
    )

    assert registered_integration in effective


def test_built_in_integrations_are_unaffected(registered_integration: str) -> None:
    import integrations.verify as verify

    assert "github" in verify.SUPPORTED_VERIFY_SERVICES
    assert len(verify.SUPPORTED_VERIFY_SERVICES) > 1


@pytest.fixture
def registered_with_setup_handler(registered_integration: str) -> Iterator[list[str]]:
    """Add the ``_HANDLERS`` entry a plugin appends, and record invocations."""
    import integrations.cli as cli

    calls: list[str] = []

    def _handler() -> None:
        calls.append(registered_integration)

    cli._HANDLERS[registered_integration] = _handler

    yield calls

    cli._HANDLERS.pop(registered_integration, None)


def test_setup_offers_a_registered_integration(registered_with_setup_handler: list[str]) -> None:
    """``setup_services`` was a tuple built at import from the registry and the
    handler map, so it could not see a plugin that arrives after either."""
    import integrations.cli as cli

    assert SERVICE in cli.setup_services()


def test_setup_dispatches_to_a_registered_integration(
    registered_with_setup_handler: list[str],
) -> None:
    """The gate in ``cmd_setup`` rejected an unknown service with ``_die``."""
    import integrations.cli as cli

    assert cli.cmd_setup(SERVICE) == SERVICE
    assert registered_with_setup_handler == [SERVICE]


def test_help_text_lists_a_registered_integration(
    registered_with_setup_handler: list[str],
) -> None:
    import integrations.cli as cli

    assert SERVICE in ", ".join(cli.setup_services())


@pytest.mark.parametrize(
    ("label", "spec"),
    [
        ("service name", IntegrationSpec(service="github", setup_order=999)),
        ("alias", IntegrationSpec(service="acme_alias", aliases=("github_mcp",))),
        (
            "family member",
            IntegrationSpec(service="acme_family", family_members=("grafana_local",)),
        ),
        ("alias over a service", IntegrationSpec(service="acme_over", aliases=("datadog",))),
    ],
)
def test_a_spec_cannot_claim_a_built_in_key(label: str, spec: IntegrationSpec) -> None:
    """Service names, aliases and family members all index the derived lookups.

    Letting a plugin claim any of them would point setup, verification,
    classification or family bucketing at the plugin instead of the built-in.
    """
    with pytest.raises(ValueError, match="built-in integration"):
        register_integration_spec(spec)


def test_a_rejected_spec_leaves_the_registry_untouched() -> None:
    before = dict(registry.INTEGRATION_SPECS_BY_SERVICE)

    with pytest.raises(ValueError):
        register_integration_spec(IntegrationSpec(service="github", setup_order=999))

    assert before == registry.INTEGRATION_SPECS_BY_SERVICE
    assert registry.INTEGRATION_SPECS_BY_SERVICE["github"].setup_order != 999
    assert "github" not in {spec.service for spec in registry._EXTERNAL_SPECS}


def test_built_in_lookups_still_resolve_after_a_registration(registered_integration: str) -> None:
    from integrations.registry import family_key, service_key

    assert service_key("github_mcp") == "github"
    assert family_key("grafana_local") == "grafana"


@pytest.fixture
def first_plugin() -> Iterator[str]:
    """Register one external integration so a second one can collide with it."""
    register_integration_spec(
        IntegrationSpec(
            service="plugin_a", aliases=("shared_alias",), family_members=("shared_kid",)
        )
    )

    yield "plugin_a"

    registry._EXTERNAL_SPECS[:] = [
        spec for spec in registry._EXTERNAL_SPECS if spec.service != "plugin_a"
    ]
    registry._rebuild_registry()


@pytest.mark.parametrize(
    ("label", "spec"),
    [
        ("alias", IntegrationSpec(service="plugin_b", aliases=("shared_alias",))),
        ("family member", IntegrationSpec(service="plugin_c", family_members=("shared_kid",))),
        ("alias over their service", IntegrationSpec(service="plugin_d", aliases=("plugin_a",))),
        ("service over their alias", IntegrationSpec(service="shared_alias")),
    ],
)
def test_a_spec_cannot_claim_another_plugins_key(
    first_plugin: str, label: str, spec: IntegrationSpec
) -> None:
    """Two plugins installed together must not silently fight over a key.

    Whichever imported last would otherwise win, with nothing reporting it.
    """
    with pytest.raises(ValueError, match="another registered integration"):
        register_integration_spec(spec)


def test_re_registering_the_same_service_still_replaces_it(first_plugin: str) -> None:
    """A reload re-registers the same spec; its own keys are not a self-conflict."""
    from integrations.registry import service_key

    register_integration_spec(
        IntegrationSpec(service=first_plugin, aliases=("shared_alias",), setup_order=5)
    )

    assert service_key("shared_alias") == first_plugin
    assert registry.INTEGRATION_SPECS_BY_SERVICE[first_plugin].setup_order == 5
    assert [spec.service for spec in registry._EXTERNAL_SPECS].count(first_plugin) == 1


def test_the_cli_command_offers_a_registered_integration(
    registered_with_setup_handler: list[str],
) -> None:
    """``click.Choice`` captured the service list when the decorator ran.

    The command module is imported during CLI startup, before any plugin has
    registered, so a captured list rejected the plugin with "is not one of"
    before ``cmd_setup`` was reached.
    """
    from surfaces.cli.commands.integrations import integrations as integrations_group

    setup_command = integrations_group.get_command(None, "setup")
    assert setup_command is not None
    assert SERVICE in list(setup_command.params[0].type.choices)


def test_the_cli_command_still_rejects_an_unknown_service() -> None:
    from click.testing import CliRunner

    from surfaces.cli.commands.integrations import integrations as integrations_group

    result = CliRunner().invoke(integrations_group, ["setup", "definitely_not_a_service"])

    assert result.exit_code == 2


@pytest.mark.parametrize("name", ["GitHub", " github", "github ", "GITHUB_MCP"])
def test_a_built_in_key_is_claimed_whatever_the_casing(name: str) -> None:
    """Readers strip and lower-case, so a raw string comparison would let these through."""
    with pytest.raises(ValueError, match="built-in integration"):
        register_integration_spec(IntegrationSpec(service=name, has_verifier=True))


@pytest.mark.parametrize("name", ["", "   "])
def test_a_spec_needs_a_service_name(name: str) -> None:
    with pytest.raises(ValueError, match="non-empty service name"):
        register_integration_spec(IntegrationSpec(service=name))


def test_keys_are_stored_in_the_form_the_lookups_are_queried_with() -> None:
    from integrations.registry import service_key

    register_integration_spec(IntegrationSpec(service="  MixedCase  ", aliases=("Some Alias ",)))
    try:
        assert service_key("MIXEDCASE") == "mixedcase"
        assert service_key("some alias") == "mixedcase"
        assert "mixedcase" in registry.INTEGRATION_SPECS_BY_SERVICE
    finally:
        registry._EXTERNAL_SPECS[:] = [
            spec for spec in registry._EXTERNAL_SPECS if spec.service != "mixedcase"
        ]
        registry._rebuild_registry()


def test_a_rebuilt_table_never_drops_a_surviving_key() -> None:
    """A reader must not catch the tables mid-rebuild.

    ``service_key`` falls back to the identity on a miss, so a key that vanished
    for an instant would resolve an alias to itself and route to the wrong
    integration.
    """
    observed: list[bool] = []
    table = {"keep": "keep", "drop": "drop"}

    class _Watcher(dict[str, str]):
        def __delitem__(self, key: str) -> None:
            observed.append("keep" in self)
            super().__delitem__(key)

    watched = _Watcher(table)
    registry._refill_mapping(watched, {"keep": "keep", "added": "added"})

    assert all(observed), "a surviving key disappeared while the table was rebuilt"
    assert watched == {"keep": "keep", "added": "added"}
