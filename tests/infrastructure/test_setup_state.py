"""Tests for the user-facing setup state surfaced to the assistant prompt."""

from __future__ import annotations

import infrastructure.setup_state as setup_state


class TestRenderSetupState:
    def test_reports_connected_integrations_and_schedule_count(self) -> None:
        # Arrange: a configured install with one live schedule.
        state = setup_state.SetupSnapshot(
            integrations=("posthog_mcp", "slack"),
            schedule_count=2,
            deliverable_count=2,
            last_delivery_ok=True,
        )

        # Act
        rendered = setup_state.render_setup_state(state)

        # Assert: the facts the assistant needs to ground a next step.
        assert "posthog_mcp" in rendered
        assert "slack" in rendered
        assert "2" in rendered

    def test_names_the_empty_install_explicitly(self) -> None:
        # Arrange: a first-run user — nothing connected, nothing scheduled.
        state = setup_state.SetupSnapshot(
            integrations=(), schedule_count=0, deliverable_count=0, last_delivery_ok=None
        )

        # Act
        rendered = setup_state.render_setup_state(state)

        # Assert: "none" must be stated, not left absent. An omitted line reads
        # as "unknown" and the model fills the gap by guessing.
        assert "none" in rendered.lower()
        assert "never" in rendered.lower()

    def test_states_facts_without_instructing_the_model(self) -> None:
        # Arrange: the block is a CONTEXT-tier fact sheet, not a rule block.
        state = setup_state.SetupSnapshot(
            integrations=("slack",), schedule_count=0, deliverable_count=0, last_delivery_ok=None
        )

        # Act
        rendered = setup_state.render_setup_state(state)

        # Assert: no imperative guidance leaks in. Instructions belong to the
        # STABLE persona; mixing them here means a per-turn fact change would
        # silently rewrite the rules.
        lowered = rendered.lower()
        for imperative in ("you should", "offer to", "suggest that", "always", "must"):
            assert imperative not in lowered

    def test_failed_last_delivery_is_distinguished_from_never_run(self) -> None:
        # Arrange: a schedule that ran and failed is not the same as one that
        # has never fired — conflating them hides a broken delivery.
        failed = setup_state.SetupSnapshot(
            integrations=("slack",), schedule_count=1, deliverable_count=1, last_delivery_ok=False
        )
        never = setup_state.SetupSnapshot(
            integrations=("slack",), schedule_count=1, deliverable_count=1, last_delivery_ok=None
        )

        # Act
        failed_text = setup_state.render_setup_state(failed).lower()
        never_text = setup_state.render_setup_state(never).lower()

        # Assert
        assert failed_text != never_text
        assert "failed" in failed_text
        assert "never" in never_text


class TestCollectSetupState:
    def test_pairs_caller_integrations_with_live_schedules(self, monkeypatch) -> None:
        # Arrange: the caller owns the integration list because the session
        # already holds the hydrated names.

        def _tasks() -> list[object]:
            return [object(), object()]

        monkeypatch.setattr(setup_state, "_scheduled_tasks", _tasks)
        monkeypatch.setattr(setup_state, "_latest_delivery_ok", lambda _tasks: True)

        # Act
        state = setup_state.collect_setup_state(("slack", "posthog_mcp"))

        # Assert
        assert state.integrations == ("slack", "posthog_mcp")
        assert state.schedule_count == 2
        assert state.last_delivery_ok is True

    def test_unreadable_scheduler_keeps_the_integrations_it_was_given(self, monkeypatch) -> None:
        # Arrange: a fresh install where the scheduler store does not exist yet.

        def _boom() -> list[object]:
            raise OSError("no store on a fresh install")

        monkeypatch.setattr(setup_state, "_scheduled_tasks", _boom)

        # Act: prompt assembly runs every turn, so this may not raise.
        state = setup_state.collect_setup_state(("slack",))

        # Assert: an unreadable scheduler must not erase a known integration --
        # reporting "none" would tell the agent nothing is connected.
        assert state.integrations == ("slack",)
        assert state.schedule_count == 0
        assert state.last_delivery_ok is None


class TestLatestDeliveryOrdering:
    def test_selects_the_run_that_finished_last_not_the_one_that_started_last(
        self, monkeypatch
    ) -> None:
        # Arrange: overlapping runs. The slow one started first but finished
        # last, so its outcome is the operator's most recent delivery.
        from infrastructure.scheduling.scheduler.types import TaskRun, TaskStatus

        slow_failed = TaskRun(
            task_id="slow",
            fire_time="2026-08-05T10:00:00Z",
            started_at="2026-08-05T10:00:00Z",
            finished_at="2026-08-05T10:30:00Z",
            status=TaskStatus.FAILED,
        )
        quick_ok = TaskRun(
            task_id="quick",
            fire_time="2026-08-05T10:05:00Z",
            started_at="2026-08-05T10:05:00Z",
            finished_at="2026-08-05T10:06:00Z",
            status=TaskStatus.SUCCESS,
        )
        latest = {"slow": slow_failed, "quick": quick_ok}
        monkeypatch.setattr(
            setup_state,
            "_latest_finished_run",
            lambda task_id: latest[task_id],
        )

        class _Task:
            def __init__(self, task_id: str) -> None:
                self.id = task_id

        # Act
        result = setup_state._latest_delivery_ok([_Task("slow"), _Task("quick")])

        # Assert: reporting the quick success would hide a later failure.
        assert result is False

    def test_finished_delivery_is_not_dropped_behind_newer_in_flight_starts(
        self, tmp_path, monkeypatch
    ) -> None:
        # Arrange: Greptile P1 — more than five newer RUNNING rows (by start)
        # must not hide an earlier-started finished delivery.
        from infrastructure.scheduling.scheduler.storage import database
        from infrastructure.scheduling.scheduler.types import TaskStatus

        db = tmp_path / "scheduler.db"
        monkeypatch.setattr(database, "default_run_database_path", lambda: db)

        with database.connection(db) as conn:
            conn.execute(
                "INSERT INTO task_runs "
                "(task_id, fire_time, started_at, finished_at, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "t1",
                    "2026-08-05T09:00:00Z",
                    "2026-08-05T09:00:00Z",
                    "2026-08-05T09:05:00Z",
                    TaskStatus.FAILED.value,
                ),
            )
            for i in range(6):
                conn.execute(
                    "INSERT INTO task_runs "
                    "(task_id, fire_time, started_at, status) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        "t1",
                        f"2026-08-05T10:{i:02d}:00Z",
                        f"2026-08-05T10:{i:02d}:00Z",
                        TaskStatus.RUNNING.value,
                    ),
                )
            conn.commit()

        class _Task:
            id = "t1"

        # Act
        result = setup_state._latest_delivery_ok([_Task()])

        # Assert: a start-ordered lookback of 5 would see only RUNNING rows.
        assert result is False


class TestSetupFingerprint:
    def test_changes_when_a_schedule_is_added(self, tmp_path, monkeypatch) -> None:
        # Arrange: a store file standing in for the scheduler task list.

        store = tmp_path / "scheduler_tasks.json"
        store.write_text("[]")
        db = tmp_path / "scheduler.db"
        monkeypatch.setattr(setup_state, "_store_paths", lambda: (store, db))
        before = setup_state.setup_state_fingerprint()

        # Act: the operator adds a schedule mid-session.
        store.write_text('[{"id": "t1"}]')

        # Assert: the cache key moves, so the next turn recomputes.
        assert setup_state.setup_state_fingerprint() != before

    def test_stable_while_nothing_changes(self, tmp_path, monkeypatch) -> None:
        # Arrange

        store = tmp_path / "scheduler_tasks.json"
        store.write_text("[]")
        db = tmp_path / "scheduler.db"
        monkeypatch.setattr(setup_state, "_store_paths", lambda: (store, db))

        # Act / Assert: an unchanged install must not re-read the stores.
        assert setup_state.setup_state_fingerprint() == setup_state.setup_state_fingerprint()

    def test_missing_stores_do_not_raise(self, tmp_path, monkeypatch) -> None:
        # Arrange: a fresh install with neither store created yet.

        monkeypatch.setattr(
            setup_state,
            "_store_paths",
            lambda: (tmp_path / "absent.json", tmp_path / "absent.db"),
        )

        # Act / Assert
        assert setup_state.setup_state_fingerprint() is not None


class TestFingerprintWatchesWalSidecar:
    def test_detects_a_delivery_recorded_only_in_the_wal_file(self, tmp_path, monkeypatch) -> None:
        # Arrange: the run database runs in WAL mode, so a finished delivery
        # lands in the sidecar while the main file keeps its size and mtime
        # until a checkpoint.
        tasks = tmp_path / "scheduler_tasks.json"
        tasks.write_text("[]")
        db = tmp_path / "scheduler.db"
        db.write_bytes(b"main")
        wal = tmp_path / "scheduler.db-wal"
        wal.write_bytes(b"")
        monkeypatch.setattr(setup_state, "_store_paths", lambda: (tasks, db, wal))
        before = setup_state.setup_state_fingerprint()

        # Act: only the sidecar changes, as a completed run would leave it.
        wal.write_bytes(b"a finished delivery row")

        # Assert: watching the main file alone would report stale delivery status.
        assert setup_state.setup_state_fingerprint() != before

    def test_real_store_paths_include_the_wal_sidecar(self) -> None:
        # Arrange / Act
        paths = setup_state._store_paths()

        # Assert: dropping the sidecar silently reintroduces stale delivery
        # status, which no other test would catch.
        assert any(path.name.endswith("-wal") for path in paths)


class TestCachedSetupState:
    def test_recomputes_only_when_the_fingerprint_moves(self, monkeypatch) -> None:
        # Arrange: prompt assembly runs on every turn, so the rendered block has
        # to be reused until the underlying stores actually change.
        renders: list[int] = []
        fingerprints = iter([((1, 1),), ((1, 1),), ((2, 2),)])

        def _count_render(_state: object) -> str:
            renders.append(1)
            return f"render {len(renders)}"

        def _moving_fingerprint() -> tuple[tuple[int, int], ...]:
            return next(fingerprints)

        monkeypatch.setattr(setup_state, "render_setup_state", _count_render)
        monkeypatch.setattr(setup_state, "setup_state_fingerprint", _moving_fingerprint)
        monkeypatch.setattr(setup_state, "_scheduled_tasks", lambda: [])
        monkeypatch.setattr(setup_state, "_latest_delivery_ok", lambda _tasks: None)
        setup_state.clear_setup_state_cache()

        # Act
        first = setup_state.cached_setup_state(("slack",))
        second = setup_state.cached_setup_state(("slack",))
        third = setup_state.cached_setup_state(("slack",))

        # Assert: two renders across three calls — the middle one is cached.
        assert first == second
        assert third != second
        assert len(renders) == 2

    def test_a_different_integration_set_is_not_served_from_cache(self, monkeypatch) -> None:
        # Arrange: connecting an integration must not be masked by a cache hit.
        monkeypatch.setattr(setup_state, "setup_state_fingerprint", lambda: ((1, 1),))
        monkeypatch.setattr(setup_state, "_scheduled_tasks", lambda: [])
        monkeypatch.setattr(setup_state, "_latest_delivery_ok", lambda _tasks: None)
        setup_state.clear_setup_state_cache()

        # Act
        one = setup_state.cached_setup_state(("slack",))
        two = setup_state.cached_setup_state(("slack", "sentry"))

        # Assert
        assert "sentry" not in one
        assert "sentry" in two


class TestDeliverableHealth:
    def test_render_names_how_many_can_actually_deliver(self) -> None:
        # Arrange: many tasks scheduled, none able to reach a destination.
        snapshot = setup_state.SetupSnapshot(
            integrations=("slack",),
            schedule_count=30,
            deliverable_count=0,
            last_delivery_ok=False,
        )

        # Act
        rendered = setup_state.render_setup_state(snapshot)

        # Assert: a bare schedule count reads as progress, so the rendered line
        # must expose how many tasks can actually deliver.
        assert "30" in rendered
        assert "0" in rendered
        lowered = rendered.lower()
        assert "deliver" in lowered

    def test_healthy_install_is_distinguishable_from_broken(self) -> None:
        # Arrange
        healthy = setup_state.SetupSnapshot(
            integrations=("slack",), schedule_count=3, deliverable_count=3, last_delivery_ok=True
        )
        broken = setup_state.SetupSnapshot(
            integrations=("slack",), schedule_count=3, deliverable_count=0, last_delivery_ok=True
        )

        # Act / Assert: identical counts, opposite health — the block must not
        # render them the same.
        assert setup_state.render_setup_state(healthy) != setup_state.render_setup_state(broken)

    def test_collect_counts_only_tasks_that_can_reach_a_destination(self, monkeypatch) -> None:
        # Arrange: two tasks, one with a channel and one without.
        class _Task:
            def __init__(self, task_id: str, chat_id: str) -> None:
                self.id = task_id
                self.chat_id = chat_id
                self.provider = "slack"
                self.params: dict[str, str] = {}

        monkeypatch.setattr(
            setup_state, "_scheduled_tasks", lambda: [_Task("a", "C1"), _Task("b", "")]
        )
        monkeypatch.setattr(setup_state, "_latest_delivery_ok", lambda _tasks: None)
        monkeypatch.setattr(
            setup_state, "_task_can_deliver", lambda task: bool(task.chat_id.strip())
        )

        # Act
        snapshot = setup_state.collect_setup_state(("slack",))

        # Assert
        assert snapshot.schedule_count == 2
        assert snapshot.deliverable_count == 1
