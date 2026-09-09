# Terminal Metrics Runbook

This runbook defines how to interpret the interactive-terminal analytics emitted
by the CLI.

## Event Groups

- Interactive terminal behavior: `terminal_actions_planned`,
  `terminal_actions_executed`, `terminal_turn_summarized`
- Agent-loop reliability: `react_turn_completed`, `$ai_generation`
- Gateway reliability: `gateway_turn_started`, `gateway_turn_completed`,
  `gateway_turn_failed`

## Core KPIs

- `terminal_action_execution_success_rate`: successful deterministic action executions
- `terminal_fallback_rate`: share of turns that required LLM fallback

## Operational Guidance

- High `terminal_fallback_rate` with low `planned_count` indicates missing deterministic
  action coverage; improve action recognizers before changing LLM prompts.
- High `planned_count` but low execution success suggests command execution reliability
  issues (shell failures, missing dependencies, timeout thresholds).

## Data Contract Source of Truth

- Event enum: `infrastructure/analytics/events.py`
- Capture helpers and KPI query specs: `infrastructure/analytics/capture.py`
- Event property builders: `infrastructure/analytics/event_properties.py`
- Provider type constraints and coercion: `infrastructure/analytics/provider.py`
