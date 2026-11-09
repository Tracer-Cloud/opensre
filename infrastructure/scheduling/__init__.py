"""Work the agent runs on a schedule or in the background.

``scheduler`` owns cron and agentic loop tasks and is hosted by the gateway
process; ``task_types`` / ``task_registry`` hold the in-flight task contract
and persistent registry (kept as leaf modules — a ``tasks/`` directory is
gitignored repo-wide).
"""
