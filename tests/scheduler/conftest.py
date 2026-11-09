"""Shared fixtures for :mod:`infrastructure.scheduling.scheduler` tests.

The scheduler no longer reads its runners from a module global; a host builds a
:class:`~infrastructure.scheduling.scheduler.runners.SchedulerRunners` bundle and
passes it into ``execute_task`` / ``build_message`` / ``run_task_now``. Tests use
the helpers in :mod:`tests.scheduler._bundle` to build that bundle, so no fixture
is needed here.
"""

from __future__ import annotations
