"""Central registry for integration metadata and verification dispatch."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class IntegrationSpec:
    """Canonical metadata for one integration service."""

    service: str
    aliases: tuple[str, ...] = ()
    family_members: tuple[str, ...] = ()
    classifier: Any | None = None
    env_loader: Any | None = None
    effective_resolver: Any | None = None
    has_verifier: bool = False
    direct_effective: bool = False
    skip_classification: bool = False
    core_verify: bool = False
    setup_order: int | None = None
    verify_order: int | None = None


_BUILTIN_SPECS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        service="grafana",
        family_members=("grafana_local",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=5,
        verify_order=2,
    ),
    IntegrationSpec(
        service="aws",
        aliases=("eks", "amazon eks"),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=1,
        verify_order=7,
    ),
    IntegrationSpec(
        service="datadog",
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=4,
        verify_order=3,
    ),
    IntegrationSpec(
        service="groundcover",
        aliases=("gc",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=35,
        verify_order=46,
    ),
    IntegrationSpec(
        service="honeycomb",
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=6,
        verify_order=4,
    ),
    IntegrationSpec(
        service="coralogix",
        aliases=("carologix",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=3,
        verify_order=5,
    ),
    IntegrationSpec(
        service="github",
        aliases=("github_mcp",),
        has_verifier=True,
        direct_effective=True,
        setup_order=14,
        verify_order=10,
    ),
    IntegrationSpec(
        service="sentry",
        has_verifier=True,
        direct_effective=True,
        setup_order=16,
        verify_order=11,
    ),
    IntegrationSpec(
        service="gitlab",
        has_verifier=True,
        direct_effective=True,
        setup_order=15,
        verify_order=52,
    ),
    IntegrationSpec(
        service="jenkins",
        has_verifier=True,
        direct_effective=True,
        setup_order=24,
        verify_order=36,
    ),
    IntegrationSpec(
        service="mongodb",
        aliases=("mongo",),
        has_verifier=True,
        direct_effective=True,
        setup_order=17,
        verify_order=12,
    ),
    IntegrationSpec(
        service="postgresql",
        aliases=("postgres",),
        has_verifier=True,
        direct_effective=True,
        setup_order=19,
        verify_order=13,
    ),
    IntegrationSpec(
        service="mongodb_atlas",
        aliases=("atlas",),
        has_verifier=True,
        direct_effective=True,
        setup_order=8,
        verify_order=15,
    ),
    IntegrationSpec(
        service="mariadb",
        has_verifier=True,
        direct_effective=True,
        setup_order=7,
        verify_order=16,
    ),
    IntegrationSpec(
        service="rabbitmq",
        aliases=("amqp",),
        has_verifier=True,
        direct_effective=True,
        verify_order=17,
    ),
    IntegrationSpec(
        service="dagster",
        has_verifier=True,
        direct_effective=True,
        setup_order=29,
        verify_order=40,
    ),
    IntegrationSpec(
        service="redis",
        aliases=("valkey",),
        has_verifier=True,
        direct_effective=True,
        setup_order=30,
        verify_order=41,
    ),
    IntegrationSpec(
        service="betterstack",
        aliases=("better stack",),
        has_verifier=True,
        direct_effective=True,
        setup_order=2,
        verify_order=18,
    ),
    IntegrationSpec(
        service="vercel",
        has_verifier=True,
        direct_effective=True,
        setup_order=13,
        verify_order=20,
    ),
    IntegrationSpec(
        service="opsgenie",
        has_verifier=True,
        direct_effective=True,
        verify_order=21,
    ),
    IntegrationSpec(
        service="incident_io",
        aliases=("incident.io", "incidentio"),
        has_verifier=True,
        direct_effective=True,
        setup_order=22,
        verify_order=22,
    ),
    IntegrationSpec(
        service="jira",
        has_verifier=True,
        direct_effective=True,
        verify_order=50,
    ),
    IntegrationSpec(
        service="servicenow",
        aliases=("service now", "service-now"),
        has_verifier=True,
        direct_effective=True,
        setup_order=42,
        verify_order=56,
    ),
    IntegrationSpec(
        service="discord",
        has_verifier=True,
        direct_effective=True,
        setup_order=18,
        verify_order=25,
    ),
    IntegrationSpec(
        service="telegram",
        has_verifier=True,
        direct_effective=True,
        setup_order=26,
        verify_order=26,
    ),
    IntegrationSpec(
        service="rocketchat",
        aliases=("rocket.chat", "rocket chat"),
        has_verifier=True,
        direct_effective=True,
        setup_order=41,
        verify_order=55,
    ),
    IntegrationSpec(
        service="buzz",
        has_verifier=True,
        direct_effective=True,
        setup_order=52,
        verify_order=58,
    ),
    IntegrationSpec(
        service="whatsapp",
        has_verifier=True,
        direct_effective=True,
        setup_order=27,
        verify_order=27,
    ),
    IntegrationSpec(
        service="twilio",
        has_verifier=True,
        direct_effective=True,
        setup_order=20,
        verify_order=28,
    ),
    IntegrationSpec(
        service="posthog_mcp",
        aliases=("posthog mcp", "posthog-mcp"),
        has_verifier=True,
        direct_effective=True,
        setup_order=33,
        verify_order=44,
    ),
    IntegrationSpec(
        service="sentry_mcp",
        aliases=("sentry mcp", "sentry-mcp"),
        has_verifier=True,
        direct_effective=True,
        setup_order=34,
        verify_order=45,
    ),
    IntegrationSpec(
        service="x_mcp",
        aliases=("x mcp", "x-mcp", "twitter", "twitter_mcp", "twitter-mcp"),
        has_verifier=True,
        direct_effective=True,
        setup_order=39,
        verify_order=49,
    ),
    IntegrationSpec(
        service="mysql",
        has_verifier=True,
        direct_effective=True,
        setup_order=28,
        verify_order=38,
    ),
    IntegrationSpec(
        service="azure_sql",
        has_verifier=True,
        direct_effective=True,
        setup_order=21,
        verify_order=14,
    ),
    IntegrationSpec(service="bitbucket", has_verifier=True, verify_order=24),
    IntegrationSpec(
        service="snowflake",
        has_verifier=True,
        direct_effective=True,
        verify_order=29,
    ),
    IntegrationSpec(
        service="azure",
        aliases=("azure monitor", "azure_monitor"),
        has_verifier=True,
        direct_effective=True,
        setup_order=44,
        verify_order=30,
    ),
    IntegrationSpec(
        service="openobserve",
        aliases=("open observe",),
        has_verifier=True,
        direct_effective=True,
        verify_order=31,
    ),
    IntegrationSpec(
        service="observe",
        aliases=("observeinc", "observe inc"),
        has_verifier=True,
        direct_effective=True,
        setup_order=55,
        verify_order=101,
    ),
    IntegrationSpec(
        service="opensearch",
        aliases=("open search",),
        has_verifier=True,
        direct_effective=True,
        setup_order=10,
        verify_order=32,
    ),
    IntegrationSpec(
        service="alertmanager",
        has_verifier=True,
        direct_effective=True,
        setup_order=0,
        verify_order=0,
    ),
    IntegrationSpec(
        service="splunk",
        has_verifier=True,
        direct_effective=True,
        verify_order=33,
    ),
    IntegrationSpec(
        service="airflow",
        aliases=("apache airflow",),
        direct_effective=True,
        verify_order=None,
    ),
    IntegrationSpec(
        service="argocd",
        has_verifier=True,
        direct_effective=True,
        verify_order=1,
    ),
    IntegrationSpec(
        service="helm",
        has_verifier=True,
        direct_effective=True,
        setup_order=38,
        verify_order=34,
    ),
    IntegrationSpec(
        service="victoria_logs",
        aliases=("victorialogs",),
        has_verifier=True,
        direct_effective=True,
        verify_order=6,
    ),
    IntegrationSpec(
        service="slack",
        has_verifier=True,
        direct_effective=True,
        setup_order=9,
        verify_order=8,
    ),
    IntegrationSpec(
        service="railway",
        has_verifier=True,
        direct_effective=True,
        setup_order=43,
        verify_order=57,
    ),
    IntegrationSpec(
        service="smtp",
        has_verifier=True,
        direct_effective=True,
        setup_order=36,
        verify_order=47,
    ),
    IntegrationSpec(
        service="tracer",
        has_verifier=True,
        setup_order=25,
        verify_order=9,
    ),
    IntegrationSpec(
        service="google_docs",
        has_verifier=True,
        setup_order=55,
        verify_order=19,
    ),
    IntegrationSpec(service="kafka", has_verifier=True, verify_order=37),
    IntegrationSpec(service="clickhouse", has_verifier=True, verify_order=23),
    IntegrationSpec(service="alicloud", direct_effective=True),
    IntegrationSpec(service="prefect", has_verifier=True, direct_effective=True, verify_order=51),
    IntegrationSpec(
        service="posthog",
        has_verifier=True,
        direct_effective=True,
        setup_order=40,
        verify_order=54,
    ),
    IntegrationSpec(service="trello"),
    IntegrationSpec(service="rds", setup_order=11),
    IntegrationSpec(
        service="supabase",
        has_verifier=True,
        verify_order=99,
    ),
    IntegrationSpec(
        service="signoz",
        has_verifier=True,
        direct_effective=True,
        setup_order=23,
        verify_order=35,
    ),
    IntegrationSpec(
        service="tempo",
        has_verifier=True,
        direct_effective=True,
        setup_order=32,
        verify_order=43,
    ),
    IntegrationSpec(
        service="pagerduty",
        has_verifier=True,
        direct_effective=True,
        setup_order=31,
        verify_order=42,
    ),
    IntegrationSpec(
        service="temporal",
        has_verifier=True,
        direct_effective=True,
        setup_order=37,
        verify_order=48,
    ),
    IntegrationSpec(
        service="kubernetes",
        aliases=("k8s",),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=51,
        verify_order=53,
    ),
    IntegrationSpec(
        service="new_relic",
        aliases=("newrelic", "new relic"),
        has_verifier=True,
        direct_effective=True,
        core_verify=True,
        setup_order=53,
        verify_order=59,
    ),
    IntegrationSpec(
        service="yandex_cloud",
        # ``yc`` is what the vendor's own CLI is called, so it is what people
        # type; ``yandex`` catches the rest.
        aliases=("yc", "yandex"),
        has_verifier=True,
        direct_effective=True,
        setup_order=54,
        verify_order=100,
    ),
)

#: Specs contributed by out-of-tree packages through
#: :func:`register_integration_spec`. Kept apart from the built-ins so a plugin
#: can add itself without the module literal having to know about it.
_EXTERNAL_SPECS: list[IntegrationSpec] = []

#: Serialises registration so two plugins importing concurrently cannot
#: interleave the conflict check with the table rebuild. Mirrors the lock
#: ``tools.registry`` takes around external tool-package registration.
_REGISTRATION_LOCK = threading.Lock()


def normalize_service_key(key: str) -> str:
    """Return *key* in the form the lookups are read with.

    ``service_key`` and the classification path both lower-case and strip before
    looking a service up, so a spec registered as ``"GitHub"`` would be stored
    under a key nothing ever queries - and would slip past a conflict check that
    compared raw strings.
    """
    return key.strip().lower()


def _builtin_claimed_keys() -> frozenset[str]:
    """Return every key the built-in specs answer to.

    A spec owns its service name, its aliases and its family members: all three
    end up as keys in the derived lookups, so all three have to be off limits to
    a plugin.
    """
    claimed: set[str] = set()
    for spec in _BUILTIN_SPECS:
        claimed.add(normalize_service_key(spec.service))
        claimed.update(normalize_service_key(alias) for alias in spec.aliases)
        claimed.update(normalize_service_key(member) for member in spec.family_members)
    return frozenset(claimed)


_BUILTIN_CLAIMED_KEYS = _builtin_claimed_keys()

#: Every spec, built-in first.
#:
#: This and the tables below are filled by :func:`_rebuild_registry` and then
#: updated **in place** on every later registration. That matters: readers use
#: ``from integrations.registry import SUPPORTED_VERIFY_SERVICES``, which binds
#: the object, not the name — rebinding here would leave every one of them
#: holding the pre-registration snapshot. Mutating in place mirrors how
#: ``tools.registry.register_external_tool_package`` extends its own list.
INTEGRATION_SPECS: list[IntegrationSpec] = []

INTEGRATION_SPECS_BY_SERVICE: dict[str, IntegrationSpec] = {}
SERVICE_KEY_MAP: dict[str, str] = {}
SKIP_CLASSIFIED_SERVICES: set[str] = set()
SERVICE_FAMILY_MAP: dict[str, str] = {}
DIRECT_CLASSIFIED_EFFECTIVE_SERVICES: list[str] = []
SUPPORTED_VERIFY_SERVICES: list[str] = []
SUPPORTED_SETUP_SERVICES: list[str] = []
CORE_VERIFY_SERVICES: set[str] = set()


def _refill_mapping(target: dict[Any, Any], source: dict[Any, Any]) -> None:
    """Make *target* match *source* without it ever going empty.

    Clearing first leaves a window where a concurrent reader sees nothing:
    ``service_key`` falls back to the identity on a miss, so an alias would
    quietly resolve to itself and route to the wrong integration. Writing the
    new entries before dropping the departed ones keeps every surviving key
    continuously present.
    """
    target.update(source)
    for key in [key for key in target if key not in source]:
        del target[key]


def _refill_set(target: set[Any], source: set[Any]) -> None:
    """Make *target* match *source* without it ever going empty."""
    target.update(source)
    for item in list(target - source):
        target.discard(item)


def _rebuild_registry() -> None:
    """Recompute every lookup derived from the spec list.

    The derived tables used to be computed once at import. A plugin registers
    after import, so they are rebuilt on each registration instead.

    Every table is updated **in place**. Readers import these names directly,
    which binds the object rather than the name, so replacing a table would
    leave them on the snapshot taken before the plugin registered.
    """
    INTEGRATION_SPECS[:] = (*_BUILTIN_SPECS, *_EXTERNAL_SPECS)

    # Built-ins are written last so that they win any key collision. Registration
    # rejects a colliding spec outright, so this only matters if that check is
    # ever bypassed - but the failure it prevents is a plugin silently taking
    # over a shipped integration's setup, verification and classification.
    by_precedence = (*_EXTERNAL_SPECS, *_BUILTIN_SPECS)

    _refill_mapping(INTEGRATION_SPECS_BY_SERVICE, {spec.service: spec for spec in by_precedence})

    service_keys: dict[str, str] = {spec.service: spec.service for spec in by_precedence}
    for spec in by_precedence:
        for alias in spec.aliases:
            service_keys[alias] = spec.service
    for spec in _BUILTIN_SPECS:
        service_keys[spec.service] = spec.service
    _refill_mapping(SERVICE_KEY_MAP, service_keys)

    _refill_set(
        SKIP_CLASSIFIED_SERVICES,
        {spec.service for spec in INTEGRATION_SPECS if spec.skip_classification},
    )

    families: dict[str, str] = {spec.service: spec.service for spec in by_precedence}
    for spec in by_precedence:
        for member in spec.family_members:
            families[member] = spec.service
    for spec in _BUILTIN_SPECS:
        families[spec.service] = spec.service
    _refill_mapping(SERVICE_FAMILY_MAP, families)

    DIRECT_CLASSIFIED_EFFECTIVE_SERVICES[:] = [
        spec.service for spec in INTEGRATION_SPECS if spec.direct_effective
    ]

    SUPPORTED_VERIFY_SERVICES[:] = [
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.has_verifier),
            key=lambda candidate: (
                candidate.verify_order if candidate.verify_order is not None else 10_000
            ),
        )
    ]

    SUPPORTED_SETUP_SERVICES[:] = [
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.setup_order is not None),
            key=lambda candidate: (
                candidate.setup_order if candidate.setup_order is not None else 10_000
            ),
        )
    ]

    _refill_set(
        CORE_VERIFY_SERVICES, {spec.service for spec in INTEGRATION_SPECS if spec.core_verify}
    )


def register_integration_spec(spec: IntegrationSpec) -> None:
    """Register an integration shipped outside this repository.

    The extension point for packages that add an integration without being part
    of the tree, mirroring ``register_external_tool_package`` for tools.
    Registering the same service twice replaces the earlier entry, so a reload
    does not accumulate duplicates.

    Raises ``ValueError`` when the spec claims a service name, alias or family
    member that another integration already answers to - built-in or a plugin
    registered earlier. Those keys index the derived lookups, so accepting one
    would quietly point setup, verification, classification or family bucketing
    at the wrong integration. Refusing at registration makes it a plugin bug with
    a clear message rather than a misrouted investigation later.

    Two packages installed together are the case worth naming: whichever
    imported last would otherwise win every key they share, and nothing would
    report it.
    """
    service = normalize_service_key(spec.service)
    if not service:
        raise ValueError("An integration spec needs a non-empty service name.")

    aliases = tuple(normalize_service_key(alias) for alias in spec.aliases)
    family_members = tuple(normalize_service_key(member) for member in spec.family_members)
    if not all(aliases) or not all(family_members):
        raise ValueError(
            f"Integration {service!r} has an empty alias or family member. Every key has "
            "to be a name something can be looked up by."
        )

    # Stored normalized, so the tables only ever hold keys in the form the
    # readers query with.
    normalized = replace(spec, service=service, aliases=aliases, family_members=family_members)
    claimed = {service, *aliases, *family_members}

    conflicts = sorted(claimed & _BUILTIN_CLAIMED_KEYS)
    if conflicts:
        raise ValueError(
            f"Integration {service!r} cannot claim {conflicts}: already used by a "
            "built-in integration as a service name, alias or family member. Pick keys "
            "unique to this integration."
        )

    with _REGISTRATION_LOCK:
        # Re-registering the same service replaces its entry, so its own keys are
        # not a conflict with itself.
        others = [entry for entry in _EXTERNAL_SPECS if entry.service != service]
        owner_by_key: dict[str, str] = {}
        for entry in others:
            for key in (entry.service, *entry.aliases, *entry.family_members):
                owner_by_key.setdefault(key, entry.service)

        taken = sorted(claimed & owner_by_key.keys())
        if taken:
            owners = ", ".join(f"{key!r} (registered by {owner_by_key[key]!r})" for key in taken)
            raise ValueError(
                f"Integration {service!r} cannot claim {owners}: another registered "
                "integration already answers to those keys. Pick keys unique to this "
                "integration."
            )

        _EXTERNAL_SPECS[:] = [*others, normalized]
        _rebuild_registry()


def external_integration_services() -> set[str]:
    """Return the services registered from outside this repository.

    Callers that validate against a fixed set of known integrations need this
    to avoid discarding a plugin's service as unrecognised.
    """
    return {spec.service for spec in _EXTERNAL_SPECS}


def builtin_claimed_keys() -> frozenset[str]:
    """Return every key a built-in integration answers to.

    Other registration hooks validate against this so a plugin cannot attach
    itself to a shipped integration's key by a route that does not go through
    :func:`register_integration_spec`.
    """
    return _BUILTIN_CLAIMED_KEYS


_rebuild_registry()


def family_key(service_key: str) -> str:
    """Return the family key used for multi-instance sibling buckets."""
    return SERVICE_FAMILY_MAP.get(service_key, service_key)


# Wire the concrete resolver into the platform-level seam so callers in
# ``tools/`` can normalize service keys without importing from
# ``integrations/`` directly, keeping ``infrastructure/`` free of a reverse
# dependency on ``integrations/``.
# Kept at import time so any consumer that has already imported the
# ``integrations`` package (every CLI entry point does so during startup)
# sees the real mapping instead of the identity fallback.
def _install_family_key_resolver() -> None:
    from infrastructure.service_families.families import register_family_key_resolver

    register_family_key_resolver(family_key)


_install_family_key_resolver()


def service_key(service_name: str) -> str:
    """Normalize an incoming service label to its canonical registry key."""
    lowered = service_name.strip().lower()
    return SERVICE_KEY_MAP.get(lowered, lowered)


# Aliases that apply only to the integration-management commands (setup, verify,
# show, remove). These intentionally diverge from `service_key` / `SERVICE_KEY_MAP`
# when a user-facing label should map to a different canonical handler. Like
# Sentry, bare ``posthog`` is the REST credentials integration and ``posthog_mcp``
# is the separate MCP flow — they are not aliased to each other.
MANAGEMENT_SERVICE_ALIASES: dict[str, str] = {}


def resolve_management_service(service_name: str) -> str:
    """Resolve a service token for the integration-management CLI commands.

    Layers management-only aliases on top of the global `service_key`
    normalization when a CLI label should map to a different canonical handler.
    """
    lowered = service_name.strip().lower()
    aliased = MANAGEMENT_SERVICE_ALIASES.get(lowered)
    if aliased is not None:
        return aliased
    return service_key(lowered)
