"""Event-driven background host for proactive judgements."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.proactive_messages.judgement import ProactiveJudgementRunner
from infrastructure.proactive_messages.models import ProactiveTrigger
from infrastructure.proactive_messages.session_records import latest_session_record_id

logger = logging.getLogger(__name__)


class ProactiveMessageService:
    """Queue persisted Slack interactions onto one ordered judgement worker."""

    def __init__(self, runner: ProactiveJudgementRunner) -> None:
        self._runner = runner
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ProactiveJudgement",
        )

    def capture_boundary(self, session_id: str) -> str | None:
        """Return the current durable session-record boundary."""
        return latest_session_record_id(session_id)

    def enqueue(
        self,
        *,
        scope: StorageScope,
        session_id: str,
        start_record_id: str | None,
        channel_id: str,
        thread_ts: str,
        user_id: str,
    ) -> bool:
        """Queue one completed record range; return before judgement work runs."""
        end_record_id = latest_session_record_id(session_id)
        if end_record_id is None or end_record_id == start_record_id:
            return False
        trigger = ProactiveTrigger(
            session_id=session_id,
            start_record_id=start_record_id,
            end_record_id=end_record_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
        )
        try:
            future = self._executor.submit(self._run_scoped, scope, trigger)
        except RuntimeError:
            logger.info("proactive judgement skipped because the service is stopping")
            return False
        future.add_done_callback(self._log_failure)
        return True

    def stop(self, *, timeout: float) -> bool:
        """Drain queued judgements within ``timeout`` and report whether they stopped."""
        waiter = threading.Thread(
            target=lambda: self._executor.shutdown(wait=True, cancel_futures=False),
            name="SlackProactiveShutdown",
            daemon=True,
        )
        waiter.start()
        waiter.join(max(0.0, timeout))
        return not waiter.is_alive()

    def _run_scoped(self, scope: StorageScope, trigger: ProactiveTrigger) -> None:
        with (
            bound_storage_scope(scope),
            bound_usage_context(
                surface=UsageSurface.SLACK,
                session_id=trigger.session_id,
                user_id=trigger.user_id,
            ),
        ):
            outcome = self._runner.run(trigger)
        logger.info(
            "proactive judgement status=%s session=%s interaction=%s",
            outcome.status,
            trigger.session_id[:8],
            outcome.interaction_id[:8],
        )

    @staticmethod
    def _log_failure(future: Future[None]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("proactive judgement failed; cursor was not advanced")


__all__ = ["ProactiveMessageService"]
