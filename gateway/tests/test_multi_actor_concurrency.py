"""Concurrency: many teammates on one org must not corrupt shared indexes or mix sessions.

The safety claim for silo Slack is:

* **Shared** — one ``bindings.json`` (and one ``integrations.json``) per org.
* **Private** — each actor's session JSONL under ``users/<U>/sessions/``.
* **Compute** — fresh agent per turn; several turns may run on threads at once.

These tests hammer the shared binding document and per-actor session writers
the way a busy gateway does (several ``ThreadPoolExecutor`` workers), and check
for lost rows, unreadable JSON, and cross-actor session bleed.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session.persistence.paths import session_path, sessions_dir
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from gateway.core.storage.session.file_bindings import FileBindingStore
from infrastructure.turn_host.concurrency import AT_CAPACITY_MESSAGE, TurnConcurrencyGate
from tests.shared.default_headless_build_stub import default_headless_build_stub
from tests.shared.fake_agent import fake_agent
from tests.shared.session_file import assert_session_file_integrity

ACME = Principal.org("org_acme")
ALICE = "U_ALICE"
BOB = "U_BOB"
_PLATFORM = "slack"
_THREAD = "C_SHARED:1.0"


class _SessionStub:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.started_at = time.time()


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "host"
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", root)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setenv(ORGANIZATION_ID_ENV, ACME.id)
    return root


@pytest.fixture
def bindings_path(host: Path) -> Path:
    path = host / "orgs" / ACME.id / "gateway" / "bindings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _scope(actor_id: str) -> StorageScope:
    return StorageScope(principal=ACME, actor=Actor(id=actor_id))


def _join(threads: list[threading.Thread], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"threads still running: {alive}"


def _read_bindings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── Shared bindings.json ────────────────────────────────────────────────────


def test_concurrent_alice_bob_bind_same_thread_keeps_both_rows(bindings_path: Path) -> None:
    """Two actors binding the same Slack thread at once must both survive."""
    store = FileBindingStore(bindings_path)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(actor: str, session_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            store.bind(
                platform=_PLATFORM,
                chat_id=_THREAD,
                session_id=session_id,
                principal=ACME,
                actor=actor,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(ALICE, "sess-alice"), name="alice"),
        threading.Thread(target=worker, args=(BOB, "sess-bob"), name="bob"),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    assert (
        store.get_session_id(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=ALICE)
        == "sess-alice"
    )
    assert (
        store.get_session_id(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=BOB)
        == "sess-bob"
    )
    data = _read_bindings(bindings_path)
    assert isinstance(data.get("bindings"), list)
    keys = {
        (r.get("principal_id"), r.get("actor_id"), r.get("session_id"))
        for r in data["bindings"]
        if isinstance(r, dict)
    }
    assert (ACME.id, ALICE, "sess-alice") in keys
    assert (ACME.id, BOB, "sess-bob") in keys


def test_concurrent_many_actors_all_bindings_survive(bindings_path: Path) -> None:
    """Fan-out like a busy channel: N actors, one shared index, no lost rows."""
    store = FileBindingStore(bindings_path)
    n = 12
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        actor = f"U_{idx:03d}"
        try:
            barrier.wait(timeout=5)
            store.bind(
                platform=_PLATFORM,
                chat_id=_THREAD,
                session_id=f"sess-{idx}",
                principal=ACME,
                actor=actor,
            )
            # Interleave reads the way status / has_any_actor_binding does.
            assert store.has_any_actor_binding(platform=_PLATFORM, chat_id=_THREAD, principal=ACME)
            got = store.get_session_id(
                platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=actor
            )
            assert got == f"sess-{idx}"
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), name=f"u{i}") for i in range(n)]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    data = _read_bindings(bindings_path)
    actors = {
        r.get("actor_id")
        for r in data["bindings"]
        if isinstance(r, dict) and r.get("chat_id") == _THREAD
    }
    assert actors == {f"U_{i:03d}" for i in range(n)}


def test_concurrent_rotate_same_actor_leaves_valid_document(bindings_path: Path) -> None:
    """Contended writes for one key must not produce unreadable JSON."""
    store = FileBindingStore(bindings_path)
    store.bind(
        platform=_PLATFORM,
        chat_id=_THREAD,
        session_id="seed",
        principal=ACME,
        actor=ALICE,
    )
    n = 8
    barrier = threading.Barrier(n)
    errors: list[Exception] = []
    winners: list[str] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            new_id = store.rotate(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=ALICE)
            winners.append(new_id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, name=f"rot{i}") for i in range(n)]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    data = _read_bindings(bindings_path)  # must parse
    alice_rows = [
        r
        for r in data["bindings"]
        if isinstance(r, dict) and r.get("actor_id") == ALICE and r.get("chat_id") == _THREAD
    ]
    assert len(alice_rows) == 1
    final = store.get_session_id(platform=_PLATFORM, chat_id=_THREAD, principal=ACME, actor=ALICE)
    assert final in winners
    assert final == alice_rows[0]["session_id"]


def _mp_bind_worker(bindings: str, actor: str, session_id: str) -> None:
    store = FileBindingStore(Path(bindings))
    store.bind(
        platform=_PLATFORM,
        chat_id=_THREAD,
        session_id=session_id,
        principal=ACME,
        actor=actor,
    )


def test_multiprocess_alice_bob_bind_no_corrupt_json(bindings_path: Path) -> None:
    """Two OS processes (closer to two writers) must leave a valid document."""
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_mp_bind_worker, args=(str(bindings_path), ALICE, "sess-alice-mp")),
        ctx.Process(target=_mp_bind_worker, args=(str(bindings_path), BOB, "sess-bob-mp")),
    ]
    for p in procs:
        p.start()
    deadline = time.monotonic() + 30
    for p in procs:
        p.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [p.pid for p in procs if p.is_alive()]
    for p in procs:
        if p.is_alive():
            p.terminate()
            p.join(5)
    assert not alive, f"processes hung: {alive}"
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    data = _read_bindings(bindings_path)
    by_actor = {
        r["actor_id"]: r["session_id"]
        for r in data["bindings"]
        if isinstance(r, dict) and r.get("chat_id") == _THREAD
    }
    assert by_actor[ALICE] == "sess-alice-mp"
    assert by_actor[BOB] == "sess-bob-mp"


# ── Per-actor session JSONL ─────────────────────────────────────────────────


@pytest.mark.usefixtures("host")
def test_concurrent_alice_bob_session_appends_do_not_mix() -> None:
    """Scoped writers append into different trees; neither sees the other's lines."""
    storage = JsonlSessionStore()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []
    paths_seen: dict[str, str] = {}

    def worker(actor: str, session_id: str, marker: str) -> None:
        try:
            with bound_storage_scope(_scope(actor)):
                barrier.wait(timeout=5)
                stub = _SessionStub(session_id)
                storage.open_session(stub)
                for i in range(20):
                    storage.append_message(
                        session_id,
                        role="user",
                        content=f"{marker}-{i}",
                        metadata={"kind": "chat", "actor": actor},
                    )
                path = session_path(session_id)
                paths_seen[actor] = str(path)
                assert actor in str(path)
                rows = assert_session_file_integrity(path)
                texts = [r.get("content") for r in rows if r.get("type") == "message"]
                assert all(isinstance(t, str) and t.startswith(marker) for t in texts)
                assert len(texts) == 20
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(ALICE, "alice-sess", "alice"), name="alice"),
        threading.Thread(target=worker, args=(BOB, "bob-sess", "bob"), name="bob"),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    assert paths_seen[ALICE] != paths_seen[BOB]
    alice_text = Path(paths_seen[ALICE]).read_text(encoding="utf-8")
    bob_text = Path(paths_seen[BOB]).read_text(encoding="utf-8")
    assert "alice-" in alice_text and "bob-" not in alice_text
    assert "bob-" in bob_text and "alice-" not in bob_text
    # Org integrations root stays shared; session homes stay private.
    with bound_storage_scope(_scope(ALICE)):
        alice_sessions = sessions_dir()
    with bound_storage_scope(_scope(BOB)):
        bob_sessions = sessions_dir()
    assert alice_sessions != bob_sessions
    assert ALICE in str(alice_sessions)
    assert BOB in str(bob_sessions)


@pytest.mark.usefixtures("host")
def test_same_session_concurrent_appends_remain_line_parseable() -> None:
    """If two threads append the same file (lock skipped), lines must stay JSON.

    Production serializes per conversation, so this should not happen for one
    actor. The test pins whether the append-only writer is still safe under
    accidental double-entry — a regression tripwire for data corruption.
    """
    storage = JsonlSessionStore()
    session_id = "shared-sess"
    with bound_storage_scope(_scope(ALICE)):
        storage.open_session(_SessionStub(session_id))
        path = session_path(session_id)

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(tag: str) -> None:
        try:
            with bound_storage_scope(_scope(ALICE)):
                barrier.wait(timeout=5)
                for i in range(40):
                    storage.append_message(
                        session_id,
                        role="assistant",
                        content=f"{tag}-{i}",
                        metadata={"kind": "chat"},
                    )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("a",), name="a"),
        threading.Thread(target=worker, args=("b",), name="b"),
    ]
    for t in threads:
        t.start()
    _join(threads)

    assert not errors, errors
    rows = assert_session_file_integrity(path)
    messages = [r for r in rows if r.get("type") == "message"]
    assert len(messages) == 80
    contents = {r.get("content") for r in messages}
    assert {f"a-{i}" for i in range(40)} | {f"b-{i}" for i in range(40)} <= contents


# ── Transport fan-out capacity ────────────────────────────────────────────────


_FANOUT_TRANSPORTS = ("slack", "telegram", "discord")


class _TransportSink:
    """Stub chat-transport output that exercises each transport's real text rendering.

    Instead of recording the ``finalize`` argument verbatim, each sink runs the
    answer through the same text-formatting functions the production transport
    output uses (Slack mrkdwn + truncation, Discord markdown tightening + split,
    Telegram HTML + truncation) before recording the user-visible text. A
    transport-specific formatter that altered the capacity sentence would fail
    the verbatim assertion, not just a missing one.
    """

    def __init__(self, *, transport: str, actor: str) -> None:
        self.transport = transport
        self.actor = actor
        self.finalized: str | None = None
        # ``TurnRunner`` reads ``tool_hooks`` onto the turn binding; ``None`` is
        # the chat default (no approval gate).
        self.tool_hooks = None

    def print(self, message: str = "") -> None:
        pass

    def render_response_header(self, label: str) -> None:
        pass

    def render_error(self, message: str) -> None:
        pass

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        return "".join(str(chunk) for chunk in chunks)

    def set_tool_status(self, status: str) -> None:
        pass

    def finalize(self, answer: str) -> None:
        self.finalized = self._render(answer)

    def _render(self, answer: str) -> str:
        """Apply the transport's real ``finalize`` text formatting and return it."""
        if self.transport == "slack":
            return self._render_slack(answer)
        if self.transport == "discord":
            return self._render_discord(answer)
        if self.transport == "telegram":
            return self._render_telegram(answer)
        return answer

    @staticmethod
    def _render_slack(answer: str) -> str:
        from gateway.transports.slack.client import SLACK_MAX_MESSAGE_CHARS
        from infrastructure.text.truncation import truncate
        from integrations.slack import markdown_to_slack_mrkdwn

        return truncate(markdown_to_slack_mrkdwn(answer), SLACK_MAX_MESSAGE_CHARS, suffix="…")

    @staticmethod
    def _render_discord(answer: str) -> str:
        from gateway.transports.discord.client import split_discord_content
        from infrastructure.text.markdown import tighten_markdown_emphasis

        body = tighten_markdown_emphasis((answer or "").strip())
        return "".join(split_discord_content(body))

    @staticmethod
    def _render_telegram(answer: str) -> str:
        from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
        from infrastructure.text.truncation import truncate
        from integrations.telegram.delivery import truncate_for_telegram_html
        from integrations.telegram.formatting import markdown_to_telegram_html

        return truncate_for_telegram_html(
            markdown_to_telegram_html(answer), MAX_MESSAGE_SIZE, suffix="…"
        ) or truncate(answer, MAX_MESSAGE_SIZE, suffix="…")


@dataclass
class _ActorSpec:
    transport: str
    actor_id: str
    chat_id: str
    session_id: str
    sink: _TransportSink
    message: str


def test_fanout_three_transports_one_capacity_sentence_and_bindings_survive(
    bindings_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12 actors across Slack/Telegram/Discord, limit 4: one capacity sentence, no binding lost, no bleed.

    The process gate owns the at-capacity sentence: every transport rejected
    under load must receive ``AT_CAPACITY_MESSAGE`` verbatim (imported, never
    retyped) through the transport's real text rendering, saturation must not
    corrupt the actor-to-session mapping, and one actor's reply must never land
    in another transport's sink.
    """
    import sys
    import types

    # ``turn_memory`` imports the Unix-only ``resource`` module unconditionally;
    # stub it on Windows so the gate path can be exercised locally. On Linux the
    # real module is already imported and this is a no-op.
    if sys.platform == "win32" and "resource" not in sys.modules:
        _resource_stub = types.ModuleType("resource")
        _resource_stub.RUSAGE_SELF = 0
        _resource_stub.getrusage = lambda _who: types.SimpleNamespace(ru_maxrss=0)
        _resource_stub.getpagesize = lambda: 4096
        monkeypatch.setitem(sys.modules, "resource", _resource_stub)

    from infrastructure.turn_host.turn_runner import TurnRunner

    store = FileBindingStore(bindings_path)
    n_actors = 12
    limit = 4

    # Arrange: 4 actors per transport, each pre-bound to its own session. The
    # worker resolves the session from the binding store at turn time (as a
    # transport dispatcher does), not from a pre-built object, so concurrent
    # resolution under saturation is exercised — not just pre/post reads.
    actors: list[_ActorSpec] = []
    for i in range(n_actors):
        transport = _FANOUT_TRANSPORTS[i // 4]
        actor_id = f"U_{i:03d}"
        chat_id = f"{transport}:chat:{i:03d}"
        session_id = f"sess-{i:03d}"
        store.bind(
            platform=transport,
            chat_id=chat_id,
            session_id=session_id,
            principal=ACME,
            actor=actor_id,
        )
        actors.append(
            _ActorSpec(
                transport=transport,
                actor_id=actor_id,
                chat_id=chat_id,
                session_id=session_id,
                sink=_TransportSink(transport=transport, actor=actor_id),
                message=f"hello-{actor_id}",
            )
        )

    # Hold admitted turns in dispatch until every worker has attempted the
    # gate. An Event (not Barrier) signals peak; releasing before late workers
    # call try_acquire frees slots and lets extra turns through (CI flake).
    release = threading.Event()
    peak_reached = threading.Event()
    attempts = {"count": 0}
    attempts_cv = threading.Condition()
    inflight = {"count": 0}
    inflight_lock = threading.Lock()

    def _stub_dispatch(message: str) -> TurnResult:
        with inflight_lock:
            inflight["count"] += 1
            if inflight["count"] >= limit:
                peak_reached.set()
        try:
            assert release.wait(timeout=60), "timed out waiting for release"
        finally:
            with inflight_lock:
                inflight["count"] -= 1
        return TurnResult(
            final_intent="cli_agent_handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
                response_text=f"reply-{message}",
            ),
        )

    def _build(**_kwargs: Any) -> Any:
        agent = fake_agent()
        agent.dispatch.side_effect = _stub_dispatch
        return agent

    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(_build),
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_started", lambda **_: None
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_completed", lambda **_: None
    )
    monkeypatch.setattr(
        "infrastructure.turn_host.turn_runner.capture_gateway_turn_failed", lambda **_: None
    )

    base_gate = TurnConcurrencyGate(limit)

    class _CountingGate:
        """Count try_acquire calls so main can wait until every actor has raced."""

        def __init__(self) -> None:
            self.limit = base_gate.limit

        def try_acquire(self) -> bool:
            ok = base_gate.try_acquire()
            with attempts_cv:
                attempts["count"] += 1
                attempts_cv.notify_all()
            return ok

        def acquire(self, *, timeout: float | None = None) -> bool:
            return base_gate.acquire(timeout=timeout)

        def release(self) -> None:
            base_gate.release()

    runner = TurnRunner(
        console=Console(force_terminal=False),
        gate=_CountingGate(),  # type: ignore[arg-type]
    )

    errors: list[Exception] = []

    def _worker(spec: _ActorSpec) -> None:
        try:
            # Resolve the session from the binding store the way a transport
            # dispatcher does — concurrently with every other worker — so a
            # binding misroute or store corruption under saturation surfaces
            # here, not only in the post-turn check.
            resolved = store.get_session_id(
                platform=spec.transport,
                chat_id=spec.chat_id,
                principal=ACME,
                actor=spec.actor_id,
            )
            assert resolved == spec.session_id, (
                f"pre-turn resolution mismatch for {spec.transport}/{spec.actor_id}: {resolved!r}"
            )
            session = SessionCore(store=InMemorySessionStore(), session_id=resolved)
            runner(
                spec.message,
                session,
                spec.sink,
                logging.getLogger("test.fanout"),
            )
            # Post-turn: the binding must still resolve to the same session.
            post = store.get_session_id(
                platform=spec.transport,
                chat_id=spec.chat_id,
                principal=ACME,
                actor=spec.actor_id,
            )
            assert post == spec.session_id, (
                f"post-turn binding lost for {spec.transport}/{spec.actor_id}: {post!r}"
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(spec,), name=f"fanout-{spec.actor_id}")
        for spec in actors
    ]
    for t in threads:
        t.start()

    assert peak_reached.wait(timeout=60), f"timed out waiting for {limit} concurrent admitted turns"
    with attempts_cv:
        deadline = time.monotonic() + 60.0
        while attempts["count"] < n_actors:
            remaining = deadline - time.monotonic()
            assert remaining > 0, (
                f"timed out waiting for all {n_actors} gate attempts (saw {attempts['count']})"
            )
            attempts_cv.wait(timeout=remaining)
    with inflight_lock:
        peak = inflight["count"]
    release.set()
    _join(threads, timeout=60.0)

    assert not errors, [repr(e) for e in errors]
    assert peak == limit, f"expected {limit} turns in flight at the peak, got {peak}"

    # Every rejected actor received AT_CAPACITY_MESSAGE verbatim through the
    # transport's real text rendering — the imported constant, never retyped.
    ran = [a for a in actors if a.sink.finalized and a.sink.finalized != AT_CAPACITY_MESSAGE]
    rejected = [a for a in actors if a.sink.finalized == AT_CAPACITY_MESSAGE]
    assert len(ran) == limit
    assert len(rejected) == n_actors - limit
    for a in rejected:
        assert a.sink.finalized == AT_CAPACITY_MESSAGE, (
            f"{a.transport}/{a.actor_id} rendered a different capacity sentence"
        )

    # No binding lost: every actor still resolves to its own session.
    for a in actors:
        got = store.get_session_id(
            platform=a.transport, chat_id=a.chat_id, principal=ACME, actor=a.actor_id
        )
        assert got == a.session_id, f"binding lost for {a.transport}/{a.actor_id}: got {got!r}"

    # No cross-actor bleed: no sink holds another actor's reply or message.
    for a in actors:
        text = a.sink.finalized or ""
        for other in actors:
            if other is a:
                continue
            assert other.message not in text, (
                f"bleed: {other.actor_id}'s message in {a.actor_id}'s sink ({a.transport})"
            )
            assert f"reply-{other.message}" not in text, (
                f"bleed: {other.actor_id}'s reply in {a.actor_id}'s sink ({a.transport})"
            )
