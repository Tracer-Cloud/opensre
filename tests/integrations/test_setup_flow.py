"""Behavior of the shared integration setup flow.

The contract worth protecting here is tier coverage: whatever surface collected
the values, a successful setup must land in the integration store *and* the
keyring *and* ``.env``. Divergence there is invisible locally — runtime resolves
the store first — and only shows up in the deploy preflight, which reads env
vars.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow

_ENV_PATH = Path("/tmp/opensre-test/.env")

_FIELDS = (
    setup_flow.SetupField(
        name="api_token", label="Demo API token", env_var="DEMO_API_TOKEN", secret=True
    ),
    setup_flow.SetupField(name="room", label="Demo room", env_var="DEMO_ROOM"),
    setup_flow.SetupField(name="note", label="Demo note", required=False),
)


def _passing(_source: str, _config: dict[str, str]) -> dict[str, str]:
    return {"status": "passed", "detail": "Demo connected."}


_SPEC = setup_flow.IntegrationSetupSpec(service="demo", fields=_FIELDS, verify=_passing)

# A picker spec: ``room`` is always collected; ``api_token`` and ``note`` each
# belong to one mode.
_MODED_SPEC = dataclasses.replace(
    _SPEC,
    mode_prompt="Demo setup:",
    modes=(
        setup_flow.SetupMode(value="token", label="API token", fields=("api_token",)),
        setup_flow.SetupMode(value="note", label="Just a note", fields=("note",)),
    ),
)


def test_collectable_fields_without_modes_returns_every_field() -> None:
    assert _SPEC.collectable_fields("anything") == _FIELDS


def test_collectable_fields_returns_always_fields_plus_chosen_mode() -> None:
    names = [f.name for f in _MODED_SPEC.collectable_fields("token")]
    # ``room`` is in no mode (always asked); ``api_token`` is the chosen mode;
    # ``note`` belongs to the other mode and is dropped.
    assert names == ["api_token", "room"]


def test_collectable_fields_for_unknown_mode_keeps_only_always_fields() -> None:
    names = [f.name for f in _MODED_SPEC.collectable_fields(None)]
    assert names == ["room"]


class _Recorder:
    """Captures every write the flow performs."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.keyring: list[tuple[str, str]] = []
        self.env_values: list[dict[str, str]] = []
        # Optional hooks so a test can observe write *ordering*, not just content.
        self.on_store: Callable[[], None] = lambda: None
        self.on_env: Callable[[], None] = lambda: None


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()

    def _sync_env_values(values: dict[str, str], **_kwargs: Any) -> Path:
        rec.env_values.append(dict(values))
        rec.on_env()
        return _ENV_PATH

    def _upsert(service: str, payload: dict[str, Any]) -> None:
        rec.saved.append((service, payload))
        rec.on_store()

    monkeypatch.setattr(setup_flow, "upsert_integration", _upsert)
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: rec.keyring.append((key, value))
    )
    monkeypatch.setattr(setup_flow, "sync_env_values", _sync_env_values)
    return rec


def test_success_writes_store_keyring_and_env(recorder: _Recorder) -> None:
    outcome = setup_flow.apply_setup(_SPEC, {"api_token": "tok-1", "room": "ops", "note": "hi"})

    assert outcome.ok is True
    assert outcome.env_path == _ENV_PATH
    assert recorder.saved == [
        ("demo", {"credentials": {"api_token": "tok-1", "room": "ops", "note": "hi"}})
    ]
    # Routing is by env var name: the token is a secret, the room is not, and
    # the store-only field reaches neither tier.
    assert recorder.keyring == [("DEMO_API_TOKEN", "tok-1")]
    assert recorder.env_values == [{"DEMO_ROOM": "ops"}]


def test_missing_required_field_fails_before_any_write(recorder: _Recorder) -> None:
    outcome = setup_flow.apply_setup(_SPEC, {"api_token": "tok-1", "room": "  "})

    assert outcome.ok is False
    assert outcome.detail == "Demo room is required."
    assert (recorder.saved, recorder.keyring, recorder.env_values) == ([], [], [])


def test_optional_field_left_blank_is_stored_as_none(recorder: _Recorder) -> None:
    setup_flow.apply_setup(_SPEC, {"api_token": "tok-1", "room": "ops"})

    assert recorder.saved[0][1]["credentials"]["note"] is None


def test_blank_field_falls_back_to_its_default(recorder: _Recorder) -> None:
    """The default applies in the flow, not only as a prompt prefill.

    A surface that never prompts — an agent filling fields from a conversation —
    must land on the same credentials as someone pressing enter at the CLI.
    """
    spec = dataclasses.replace(
        _SPEC,
        fields=(
            _FIELDS[0],
            setup_flow.SetupField(
                name="room", label="Demo room", env_var="DEMO_ROOM", default="general"
            ),
        ),
    )

    setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": ""})

    assert recorder.saved[0][1]["credentials"]["room"] == "general"
    assert recorder.env_values == [{"DEMO_ROOM": "general"}]


def test_a_submitted_value_wins_over_the_default(recorder: _Recorder) -> None:
    spec = dataclasses.replace(
        _SPEC,
        fields=(
            _FIELDS[0],
            setup_flow.SetupField(
                name="room", label="Demo room", env_var="DEMO_ROOM", default="general"
            ),
        ),
    )

    setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "incidents"})

    assert recorder.saved[0][1]["credentials"]["room"] == "incidents"


def test_a_required_field_with_a_default_is_never_missing(recorder: _Recorder) -> None:
    spec = dataclasses.replace(
        _SPEC,
        fields=(
            _FIELDS[0],
            setup_flow.SetupField(
                name="room",
                label="Demo room",
                env_var="DEMO_ROOM",
                default="general",
                required=True,
            ),
        ),
    )

    outcome = setup_flow.apply_setup(spec, {"api_token": "tok-1"})

    assert outcome.ok is True


def test_constant_field_is_persisted_and_ignores_submitted_values(recorder: _Recorder) -> None:
    """A constant is never prompted for and cannot be overridden by the caller."""
    spec = dataclasses.replace(
        _SPEC,
        fields=(
            _FIELDS[0],
            _FIELDS[1],
            setup_flow.SetupField(
                name="mode",
                label="Demo mode",
                env_var="DEMO_MODE",
                constant="stdio",
            ),
        ),
    )

    outcome = setup_flow.apply_setup(
        spec, {"api_token": "tok-1", "room": "ops", "mode": "streamable-http"}
    )

    assert outcome.ok is True
    assert recorder.saved[0][1]["credentials"]["mode"] == "stdio"
    assert recorder.env_values == [{"DEMO_ROOM": "ops", "DEMO_MODE": "stdio"}]


def test_constant_empty_string_is_kept(recorder: _Recorder) -> None:
    """Empty constants stay empty rather than becoming None."""
    spec = dataclasses.replace(
        _SPEC,
        fields=(
            _FIELDS[0],
            _FIELDS[1],
            setup_flow.SetupField(name="url", label="Demo URL", constant=""),
        ),
    )

    setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "ops"})

    assert recorder.saved[0][1]["credentials"]["url"] == ""


def test_failed_verification_persists_nothing(recorder: _Recorder) -> None:
    def _rejecting(_source: str, _config: dict[str, str]) -> dict[str, str]:
        return {"status": "failed", "detail": "Demo rejected the token."}

    spec = dataclasses.replace(_SPEC, verify=_rejecting)

    outcome = setup_flow.apply_setup(spec, {"api_token": "bad", "room": "ops"})

    assert outcome.ok is False
    assert outcome.detail == "Demo rejected the token."
    assert (recorder.saved, recorder.keyring, recorder.env_values) == ([], [], [])


def test_resolve_step_rewrites_credentials_before_they_are_stored(recorder: _Recorder) -> None:
    spec = dataclasses.replace(
        _SPEC,
        resolve=lambda creds: setup_flow.ResolvedCredentials(
            credentials={**creds, "room": "-100999"}, note="Delivering to Ops (channel)."
        ),
    )

    outcome = setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "@ops"})

    assert outcome.ok is True
    assert outcome.detail == "Demo connected. Delivering to Ops (channel)."
    # The resolved value, not the typed one, reaches both the store and .env.
    assert recorder.saved[0][1]["credentials"]["room"] == "-100999"
    assert recorder.env_values == [{"DEMO_ROOM": "-100999"}]


def test_resolve_failure_aborts_setup(recorder: _Recorder) -> None:
    spec = dataclasses.replace(
        _SPEC,
        resolve=lambda _creds: setup_flow.ResolvedCredentials(
            credentials={}, error="Cannot reach @ops."
        ),
    )

    outcome = setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "@ops"})

    assert outcome.ok is False
    assert outcome.detail == "Cannot reach @ops."
    assert (recorder.saved, recorder.keyring, recorder.env_values) == ([], [], [])


def test_clearing_an_optional_field_clears_every_tier(recorder: _Recorder) -> None:
    """Blank values are written through, not skipped.

    Skipping would leave the old value in ``.env`` while the store recorded
    ``None`` — and credential resolution falls back to the environment when the
    store is empty, so the cleared value would keep resolving.
    """
    spec = setup_flow.IntegrationSetupSpec(
        service="demo",
        fields=(
            setup_flow.SetupField(
                name="api_token", label="Token", env_var="DEMO_API_TOKEN", secret=True
            ),
            setup_flow.SetupField(name="room", label="Room", env_var="DEMO_ROOM", required=False),
        ),
        verify=_passing,
    )

    setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": ""})

    assert recorder.saved[0][1]["credentials"]["room"] is None
    assert recorder.env_values == [{"DEMO_ROOM": ""}]


def test_clearing_an_optional_secret_clears_the_keyring(recorder: _Recorder) -> None:
    spec = setup_flow.IntegrationSetupSpec(
        service="demo",
        fields=(
            setup_flow.SetupField(
                name="api_token",
                label="Token",
                env_var="DEMO_API_TOKEN",
                secret=True,
                required=False,
            ),
        ),
        verify=_passing,
    )

    setup_flow.apply_setup(spec, {"api_token": ""})

    assert recorder.keyring == [("DEMO_API_TOKEN", "")]


def test_env_is_written_before_the_store(recorder: _Recorder) -> None:
    """Ordering matters: a store-only write is the state this module prevents."""
    order: list[str] = []
    recorder.on_store = lambda: order.append("store")
    recorder.on_env = lambda: order.append("env")

    setup_flow.apply_setup(_SPEC, {"api_token": "tok-1", "room": "ops"})

    assert order == ["env", "store"]


def test_unwritable_env_persists_nothing_and_reports_the_failure(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
) -> None:
    """An unwritable .env must not leave credentials in the store alone."""

    def _boom(_values: dict[str, str], **_kwargs: Any) -> Path:
        raise PermissionError("Cannot write to /etc/.env: permission denied.")

    monkeypatch.setattr(setup_flow, "sync_env_values", _boom)

    outcome = setup_flow.apply_setup(_SPEC, {"api_token": "tok-1", "room": "ops"})

    assert outcome.ok is False
    assert "permission denied" in outcome.detail
    assert recorder.saved == []


def test_spec_without_a_verifier_still_configures(recorder: _Recorder) -> None:
    """An integration with nothing to verify against must not be unconfigurable."""
    spec = dataclasses.replace(_SPEC, verify=None)

    outcome = setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "ops"})

    assert outcome.ok is True
    assert outcome.detail == ""
    assert recorder.saved != []


def test_finalize_hook_runs_after_persist_and_appends_its_note(recorder: _Recorder) -> None:
    saved_before_finalize: list[bool] = []

    def _finalize(_credentials: dict[str, str | None]) -> str:
        # The store write has already happened by the time finalize runs.
        saved_before_finalize.append(bool(recorder.saved))
        return "Side effect done."

    spec = dataclasses.replace(_SPEC, finalize=_finalize)

    outcome = setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "ops"})

    assert outcome.ok is True
    assert outcome.detail == "Demo connected. Side effect done."
    assert saved_before_finalize == [True]


def test_finalize_note_is_omitted_when_empty(recorder: _Recorder) -> None:
    spec = dataclasses.replace(_SPEC, finalize=lambda _credentials: "")

    outcome = setup_flow.apply_setup(spec, {"api_token": "tok-1", "room": "ops"})

    assert outcome.detail == "Demo connected."


def test_instance_setup_keeps_other_instances_and_skips_env_and_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: a real store; env, keyring and vault writes are recorded.
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    env_writes: list[dict[str, str]] = []
    keyring: list[tuple[str, str]] = []
    pushed: list[str] = []
    monkeypatch.setattr(
        setup_flow, "sync_env_values", lambda values, **_: env_writes.append(values) or _ENV_PATH
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: keyring.append((key, value))
    )
    monkeypatch.setattr(
        setup_flow, "push_webapp_org_integration", lambda service, _c: pushed.append(service)
    )
    spec = setup_flow.IntegrationSetupSpec(
        service="demo_servers",
        instance_field="name",
        fields=(
            setup_flow.SetupField(name="name", label="Name"),
            setup_flow.SetupField(name="url", label="URL"),
        ),
        verify=_passing,
    )

    # Act
    first = setup_flow.apply_setup(spec, {"name": "alpha", "url": "http://a"})
    second = setup_flow.apply_setup(spec, {"name": "beta", "url": "http://b"})
    updated = setup_flow.apply_setup(spec, {"name": "alpha", "url": "http://a2"})

    # Assert
    from integrations.store import get_integration

    record = get_integration("demo_servers") or {}
    assert first.ok and second.ok and updated.ok
    assert [(i["name"], i["credentials"]) for i in record["instances"]] == [
        ("alpha", {"url": "http://a2"}),
        ("beta", {"url": "http://b"}),
    ]
    assert keyring == [] and all(values == {} for values in env_writes)
    assert pushed == []
