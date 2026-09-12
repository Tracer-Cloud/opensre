# Main skill contract review

Reviewed 2026-09-12. This describes OpenSRE's main workflow cards under
`core/agent_harness/prompts/skills/`. Tool-usage cards have a separate contract.
The audit checks implementation and references; it does not establish which
workflows users actually invoke, because usage telemetry was not examined.

## Agreed contract

The executable schema is [validation.py](/Users/janvincentfranciszek/opensre/core/agent_harness/prompts/skills/validation.py).
The maintained authoring instructions and starter card are
[AGENTS.md](/Users/janvincentfranciszek/opensre/core/agent_harness/prompts/skills/AGENTS.md)
and [SKILL_TEMPLATE.md](/Users/janvincentfranciszek/opensre/core/agent_harness/prompts/skills/_template/SKILL_TEMPLATE.md).

| Field | Decision and purpose |
| --- | --- |
| `name` | Keep. Required, unique lowercase kebab-case identity used by discovery, loading, and scheduled tasks. |
| `description` | Keep. Required discovery text explaining behavior and activation conditions. |
| `getting_started` | Keep. Optional menu label; requires `demo_order`. |
| `demo_order` | Keep. Positive integer ordering the generated menu. Labels and orders are unique. |
| `pre_execute` | Keep. At most one `ask_user_choice` entry call; a batched `questions` payload can ask several questions. It cannot execute arbitrary tools. |
| `includes` | Rename from frontmatter `references`. Local Markdown instructions appended automatically. Missing files, paths outside the skills tree, and another `SKILL.md` are rejected. |
| `recurring` | Keep as a boolean, default `false`. Marks eligibility for the recurring-skill runner; cron and timezone belong to the task. |
| `metadata.owner` | Keep required. Original author; human ownership. |
| `metadata.last_changed_by` | Keep required. Person responsible for the latest edit. |
| `metadata.last_changed_at` | Keep required. Unquoted ISO date no later than today. |
| `metadata.version` | Keep required as a string. Editorial version; scheduler revision pins still use a hash of the loaded body. |
| `metadata.usecases` | Keep required. Nonempty list of strings naming intended users and concrete scenarios. |
| `metadata.requires` | Keep required. Nonempty list of strings naming tools, credentials, access, and execution prerequisites. Missing, empty, or malformed prerequisites fail CI. |

The six metadata fields retain the ownership, use-case, and prerequisite content
the project wants in its NVIDIA-style cards. This exact YAML schema is OpenSRE's
representation, rather than a claim that NVIDIA prescribes these key names.
Design references: [NVIDIA skill cards](https://docs.nvidia.com/skills/skill-cards)
and [release checklist](https://docs.nvidia.com/skills/release-checklist).

Removed fields and mechanisms:

- `metadata.type`, `metadata.dependencies`, and `metadata.prerequisite_for`.
- Main-workflow `tools` and its partial tool filtering on follow-up menu answers.
  Required capabilities remain in `metadata.requires`; tool-usage cards retain
  their own required `tools` field.
- `after_tool`, `options_from`, `options_extra`, and their unused hook state
  and repository-picker machinery.
- Handwritten master menu options and handoff mappings. Both now derive from
  child metadata, followed by Skip. Generated menus are validated too.

Optional `references/<slug>.md` files still load on demand through `skill_view`.
Markdown citations remain Markdown links. Neither mechanism is an automatic
`includes` entry.

## Main workflow inventory

| Skill | Verdict |
| --- | --- |
| `onboarding-github-ci` | Keep: entry menu for the implemented workflows. |
| `analyzing-github-ci-performance` | Keep: historical CI metrics. Its scheduling handoff contains stale prose. |
| `scheduling-github-ci-fixes` | Keep: recurring repair setup and private demo. Current v5 instructions have a checkout mismatch described below. |
| `connecting-slack` | Keep: Slack setup and handoffs; now the third demo. |
| `fixing-github-ci` | Keep: one-off repair. Tool-call details overlap its tool-usage card; consolidate those details when that workflow is next edited. |
| `fixing-github-security-alerts` | Keep: supported GitHub security remediation. Similar tool-guidance overlap does not make the workflow obsolete. |
| `investigating-incidents-with-runbooks` | Keep: supported runbook investigation. Its formatting and absent default plan need alignment with authoring guidance. |
| `reporting-github-ci-failures` | Keep: current failure reporting and schedule discovery. Some unattended execution prose is redundant. |
| `delivering-morning-briefings` | Keep the use case; update delivery assumptions before relying on every configured Slack mode. |
| `delegating-github-ci-fixes` | Removed: explicitly unimplemented managed-service placeholder. |

The three implemented onboarding children now use their reusable canonical
names without `onboarding-` prefixes. Lookup aliases retain the old names.
Contributor `AGENTS.md` is excluded from skill discovery.

## Workflow findings outside the metadata cleanup

1. **Scheduled repair workspace:** the concurrently revised v5 card says the
   fixer clones a repository and omits `workspace` from its saved tool calls.
   The fixer actually requires an existing checkout with a matching origin.
   The demo's temporary clone must be carried into the saved call, or the host
   must already default to that checkout. Correct the runtime statement and
   pass the established workspace. Evidence:
   [card](/Users/janvincentfranciszek/opensre/core/agent_harness/prompts/skills/onboarding-github-ci/b-scheduling-github-ci-fixes/SKILL.md:51),
   [workspace validation](/Users/janvincentfranciszek/opensre/integrations/github/tools/ci_fix/runner.py:59).
2. **Analytics handoff:** the card says the scheduling workflow has an analysis
   step that reuses today's report. The current scheduling workflow repairs
   PRs. Its handoff should carry the selected repository and follow the loaded
   repair plan. Evidence:
   [handoff](/Users/janvincentfranciszek/opensre/core/agent_harness/prompts/skills/onboarding-github-ci/a-analyzing-github-ci-performance/SKILL.md:144).
3. **Morning delivery:** the card directs Slack posting without a user request,
   assumes every Slack connection has a webhook-bound destination, and points
   to a nonexistent fallback section. The Slack bot-token path requires a
   channel. Clarify authorized delivery, configured destination, and the
   response when no delivery tool is available. Evidence:
   [briefing](/Users/janvincentfranciszek/opensre/core/agent_harness/prompts/skills/delivering-morning-briefings/SKILL.md:102),
   [Slack delivery](/Users/janvincentfranciszek/opensre/integrations/slack/tools/slack_send_message_tool/tool.py:108).
4. **Recurring CI report prose:** its scheduled runner produces the report
   directly, so instructions for an unattended agent to recreate that report
   are redundant. Retain the card's discovery and scheduling role. Evidence:
   [runner](/Users/janvincentfranciszek/opensre/integrations/scheduled_skill_runner.py:61).

These findings identify targeted workflow maintenance. They do not justify
deleting the nine retained use cases. Concurrent changes to the scheduling
workflow and GitHub repair implementation were preserved.

## Enforcement and verification

Runtime discovery excludes an invalid card with a diagnostic and continues
loading valid cards. CI validates raw cards before filtering, so exclusions
cannot hide a broken release. Tests cover missing prerequisites, unsupported
fields, invalid YAML/dates/encoding, duplicate names and keys, invalid includes,
unrenderable or ambiguous menus, real entry behavior, and generated handoffs.

Validation uses `make lint`, `make format-check`, `make typecheck`, and focused
tests for skill loading, prompts, session state, action turns, interactive menus,
scheduled skills, and colocated workflow tests. No live GitHub demo, push,
deployment, or external message was performed as part of this audit.
