"""One place that turns collected credentials into a fully configured integration.

Setting an integration up happens on three surfaces — the onboarding wizard
(``opensre onboard``), ``opensre integrations setup <service>``, and the
interactive-shell action tools. Each one only differs in how it *collects*
values; what has to happen afterwards is identical:

1. every required field is present,
2. the credentials actually work (the integration's verifier — which is also
   where a rule spanning several fields belongs, so an incomplete combination
   is rejected by the same prober that runs on health checks),
3. references the user typed are resolved to what the runtime needs (optional,
   integration-specific — see :attr:`IntegrationSetupSpec.resolve`),
4. they are persisted to **every** tier that reads them — the integration
   store, the system keyring for secrets, and the project ``.env`` for the rest,
   and
5. any post-save side effect runs (optional — see
   :attr:`IntegrationSetupSpec.finalize`).

Before this module each surface reimplemented step 4, and they disagreed: the
wizard wrote all three tiers while ``integrations setup`` wrote only the store.
Runtime resolution hides that (it checks the store first), but anything reading
the environment — notably the deploy preflight in
``infrastructure/deployment/ecr_deploy/prep.py`` — sees a half-configured integration.

Callers now describe *what* the integration needs with an
:class:`IntegrationSetupSpec` and hand collected values to :func:`apply_setup`;
where each value lands is decided here, once.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from config.env_file import is_sensitive_env_key, sync_env_secret, sync_env_values
from integrations.store import upsert_integration
from integrations.verification import VerifierFn
from integrations.webapp_vault import push_webapp_org_integration


class SetupUI(Protocol):
    """Human interaction surface used by vendor-specific setup guides."""

    def say(self, message: str) -> None:
        """Show guidance without exposing credentials."""

    def step(self, number: str, title: str) -> None:
        """Announce one bounded setup stage."""

    def choose(self, message: str, choices: Sequence[tuple[str, str]]) -> str:
        """Ask the human to select one setup action."""

    def value(self, message: str, *, default: str = "", secret: bool = False) -> str:
        """Collect one value, masking it when it is secret."""


class GuideFn(Protocol):
    """Collect vendor credentials after explaining each required human action."""

    def __call__(self, ui: SetupUI, saved: Mapping[str, str]) -> dict[str, str]:
        """Return values ready for verification and persistence."""


@dataclass(frozen=True)
class ResolvedCredentials:
    """Output of an integration's optional resolve step.

    A non-empty *error* aborts setup; *note* is appended to the success detail
    so the user sees what a typed reference turned into.
    """

    credentials: dict[str, str | None]
    note: str = ""
    error: str = ""


ResolveFn = Callable[[dict[str, str | None]], ResolvedCredentials]

# Side effect run after the credentials are persisted, for setup that has to
# reach past the store (registering a webhook, a slash command, …). Returns an
# optional note appended to the success detail; it is best-effort by contract —
# the integration is already saved, so a failure here is surfaced, not unwound.
FinalizeFn = Callable[[dict[str, str | None]], str]


@dataclass(frozen=True)
class SetupMode:
    """One choice in an integration's setup picker, and the fields it collects.

    Some integrations offer mutually distinct ways to configure the same
    service — Slack's webhook / Socket Mode, an auth method that is bearer *or*
    basic. Rather than ask every field and hope the user leaves the irrelevant
    ones blank, a mode names the subset that applies to that choice. Fields not
    listed in the picked mode are cleared, not prompted.
    """

    value: str
    """Stable identifier returned by the picker."""

    label: str
    """Human-readable option text."""

    fields: tuple[str, ...] = ()
    """:attr:`SetupField.name` values collected when this mode is chosen.

    A mode with no fields is a valid choice that collects nothing beyond the
    integration's always-on fields — Alertmanager's "None (unauthenticated)".
    """

    required_fields: tuple[str, ...] = ()
    """Subset of :attr:`fields` that must be non-empty when this mode is chosen.

    A mode-gated field is declared ``required=False`` on the spec because it is
    unset under every *other* mode; this is where "required within this mode"
    lives, so a blank answer is refused at the prompt instead of surfacing later
    as a config-model validation error.
    """


@dataclass(frozen=True)
class SetupField:
    """One credential an integration needs, and where it is persisted."""

    name: str
    """Key under the integration store's ``credentials`` mapping."""

    label: str
    """Human-readable field name, used in "X is required" errors."""

    prompt: str = ""
    """Question text when collecting this field interactively; defaults to *label*.

    Kept separate so the question can carry guidance ("Default chat ID or
    @channelname") while the error stays terse ("Default chat ID is required.").
    """

    env_var: str | None = None
    """Env var this field mirrors, or ``None`` to keep it store-only.

    The tier is derived from the name — :func:`config.env_file.is_sensitive_env_key`
    routes ``*_TOKEN``/``*_KEY``/``*_PASSWORD`` to the keyring and everything
    else to ``.env``. Fields do not get to choose.
    """

    default: str = ""
    """Value to use when the field is submitted blank.

    Applied in :func:`apply_setup`, not just offered as a prompt prefill, so a
    surface that never prompts — the wizard reusing a stored value, an agent
    filling fields from a conversation — lands on the same credentials as
    someone pressing enter at the CLI. A field with a default is therefore
    never missing, whatever *required* says.
    """

    required: bool = True
    """When true, a blank value fails setup instead of being stored as ``None``."""

    secret: bool = False
    """Whether collection surfaces should mask this field while it is typed."""

    constant: str | None = None
    """Fixed value that is always persisted and never prompted.

    When set, collection surfaces skip this field and :func:`apply_setup`
    ignores any submitted value under *name*. Use for transport modes and
    other values the user must not choose.
    """

    validate: Callable[[str], str | None] | None = None
    """Shape check applied to a non-empty answer at the prompt.

    Returns an error message to show (the surface re-asks) or ``None`` when
    the value is acceptable. For the checks the config model would only reject
    later with a stack of validation errors — a role *ID* pasted where a role
    *ARN* belongs.
    """

    @property
    def question(self) -> str:
        """The text to prompt with."""
        return self.prompt or self.label

    @property
    def is_constant(self) -> bool:
        """True when this field is fixed rather than collected."""
        return self.constant is not None


@dataclass(frozen=True)
class IntegrationSetupSpec:
    """The full set of credentials one integration is configured with."""

    service: str
    fields: tuple[SetupField, ...]

    mode_prompt: str = ""
    """Question shown by the setup picker, or ``""`` for no picker (flat prompts).

    Set together with :attr:`modes` when a service is configured in mutually
    distinct ways (Slack's webhook / Socket Mode, an auth-method choice) and
    prompting every field at once would be confusing.
    """

    modes: tuple[SetupMode, ...] = ()
    """The picker's choices; empty when :attr:`mode_prompt` is unset.

    Collection surfaces show the picker, then prompt only the always-on fields
    (those in no mode) plus the chosen mode's fields — see
    :meth:`collectable_fields`. Persistence is unchanged: :func:`apply_setup`
    never sees the mode, only the resulting values.
    """

    verify: VerifierFn | None = None
    """The integration's verifier, or ``None`` to skip verification.

    Wired explicitly rather than looked up in the verifier registry: the caller
    already knows which integration it is configuring, and an explicit reference
    keeps the flow free of import-time registration ordering.
    """

    resolve: ResolveFn | None = None
    """Optional post-verification rewrite of the collected credentials.

    Runs after the verifier (so it can rely on the credentials being valid) and
    before anything is persisted. Use it when what the user can reasonably type
    is not what the runtime should store — Telegram's ``@channelname`` becoming
    a numeric chat id, for instance.
    """

    finalize: FinalizeFn | None = None
    """Optional side effect run after the credentials are persisted.

    For setup that has to reach past the store once the integration exists —
    Discord registering its ``/investigate`` slash command, for example. Runs
    last so it can assume a saved, verified integration, and is best-effort: it
    returns a note for the success detail and a failure is surfaced there rather
    than rolling back the save.
    """

    guide: GuideFn | None = None
    """Optional vendor guide for human hand-offs and discovery before verification."""

    def is_required(self, field: SetupField, mode: str | None) -> bool:
        """Whether *field* must be non-empty: spec-required, or required by *mode*."""
        if field.required:
            return True
        chosen = next((one for one in self.modes if one.value == mode), None)
        return chosen is not None and field.name in chosen.required_fields

    def collectable_fields(self, mode: str | None) -> tuple[SetupField, ...]:
        """Fields a collection surface should prompt for under *mode*.

        Without :attr:`modes`, that is every field. With modes it is the
        always-on fields (in no mode) plus the chosen mode's fields; a
        mode-gated field belonging to some *other* mode is omitted, so the
        surface clears it rather than prompting for it. An unknown or ``None``
        *mode* selects no gated fields — only the always-on ones.
        """
        if not self.modes:
            return self.fields
        gated = {name for one in self.modes for name in one.fields}
        chosen = next((one for one in self.modes if one.value == mode), None)
        selected = set(chosen.fields) if chosen else set()
        return tuple(f for f in self.fields if f.name not in gated or f.name in selected)


@dataclass(frozen=True)
class SetupOutcome:
    """What :func:`apply_setup` did, in a shape every surface can render."""

    ok: bool
    """True only when the credentials were verified *and* persisted."""

    detail: str
    """Renderable sentence for the user — the verifier's message, or why it failed."""

    env_path: Path | None = None
    """The ``.env`` written; always set when *ok*, never set otherwise."""


def _collect_credentials(
    spec: IntegrationSetupSpec, values: Mapping[str, str | None]
) -> tuple[dict[str, str | None], str]:
    """Normalize submitted values, or return the first missing required field.

    The spec is authoritative: fields it declares are the credentials that get
    stored, and anything else in *values* is ignored. Constant fields always
    take their fixed value, even when *values* supplies something else.
    """
    credentials: dict[str, str | None] = {}
    for field in spec.fields:
        if field.is_constant:
            # Keep "" as "" for optional constant fields.
            credentials[field.name] = field.constant
            continue
        value = (values.get(field.name) or "").strip() or field.default
        if not value and field.required:
            return {}, f"{field.label} is required."
        credentials[field.name] = value or None
    return credentials, ""


def _verify(spec: IntegrationSetupSpec, credentials: dict[str, str | None]) -> tuple[bool, str]:
    """Run the spec's verifier against *credentials*.

    An integration with no verifier is treated as verified — the alternative
    would be refusing to configure it at all. Verifiers take ``dict[str, str]``,
    so unset optional fields are dropped rather than passed as ``None``.
    """
    if spec.verify is None:
        return True, ""
    probe = {name: value for name, value in credentials.items() if value}
    outcome = spec.verify("setup", probe)
    return outcome["status"] == "passed", outcome["detail"]


def _persist_env(spec: IntegrationSetupSpec, credentials: dict[str, str | None]) -> Path:
    """Mirror env-backed fields into the credentials file / ``.env`` and return the ``.env`` path.

    ``.env`` is rewritten even when no field targets it so public keys stay in
    sync. Existing secret assignments are left in place.

    Raises whatever the writers raise — notably ``PermissionError`` from
    :func:`config.env_file.write_env_lines` on an unwritable ``.env``.
    """
    env_values: dict[str, str] = {}
    for field in spec.fields:
        if not field.env_var:
            continue
        # Blank values are written through rather than skipped, so clearing an
        # optional field clears every tier. Skipping would leave the previous
        # value in ``.env`` while the store recorded ``None``, and credential
        # resolution falls back to the environment when the store is empty — so
        # the value the user just cleared would keep resolving.
        value = credentials.get(field.name) or ""
        if is_sensitive_env_key(field.env_var):
            sync_env_secret(field.env_var, value)
        else:
            env_values[field.env_var] = value
    return sync_env_values(env_values)


def apply_setup(
    spec: IntegrationSetupSpec,
    values: Mapping[str, str | None],
) -> SetupOutcome:
    """Validate, verify, and persist an integration's credentials to every tier.

    Nothing is written unless verification passes, so a rejected credential
    never leaves a half-configured integration behind.
    """
    credentials, missing = _collect_credentials(spec, values)
    if missing:
        return SetupOutcome(ok=False, detail=missing)

    verified, detail = _verify(spec, credentials)
    if not verified:
        return SetupOutcome(ok=False, detail=detail)

    if spec.resolve is not None:
        resolved = spec.resolve(credentials)
        if resolved.error:
            return SetupOutcome(ok=False, detail=resolved.error)
        credentials = resolved.credentials
        detail = " ".join(part for part in (detail, resolved.note) if part)

    # Env/keyring before the store, so no failure can leave the store-only state
    # this module exists to prevent. If the env write fails nothing is persisted
    # at all; if the store write failed afterwards, credential resolution still
    # falls back to the environment, so the integration keeps working.
    try:
        env_path = _persist_env(spec, credentials)
    except (OSError, RuntimeError) as exc:
        # A local CLI/wizard surface, so the detail may name the actual cause
        # (usually an unwritable .env). Nothing has been persisted at this point.
        return SetupOutcome(ok=False, detail=f"Could not save {spec.service} credentials: {exc}")

    upsert_integration(spec.service, {"credentials": credentials})
    # Mirror the change up so a silo's edit reaches the org vault. No-op off a
    # silo, and never fatal: the local store is already the working copy.
    push_webapp_org_integration(spec.service, credentials)

    if spec.finalize is not None:
        note = spec.finalize(credentials)
        detail = " ".join(part for part in (detail, note) if part)

    return SetupOutcome(ok=True, detail=detail, env_path=env_path)


__all__ = [
    "FinalizeFn",
    "IntegrationSetupSpec",
    "ResolveFn",
    "ResolvedCredentials",
    "SetupField",
    "SetupMode",
    "SetupOutcome",
    "apply_setup",
]
