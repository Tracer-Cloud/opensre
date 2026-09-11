# Skill Release Checklist 

### Background references**
**Claude recommendations on skill design**
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

**Nvidia release check list****
- https://docs.nvidia.com/skills/release-checklist

**Nvidia meta data guidelines**
- https://docs.nvidia.com/skills/skill-cards

## Narrow purpose

The skill has a narrow, concrete purpose.
SKILL.md describes when the skill should activate.
Tool, shell, network, file, environment, and MCP capabilities are declared when used.

## One action per step

Each workflow step does exactly one thing and has one observable completion
condition. Split distinct actions into separate numbered steps.

Execute steps and their tool calls sequentially. Finish the current action,
including delivering any user-facing output, before starting the next.
Do not batch or parallelize actions, or couple separate steps with wording
such as "alongside", "at the same time", or "in the same response".

Report delivery and asking what to do next are separate actions: first
respond with the report as Markdown text; only after it has been shown may
the next step open a menu. Saying "the report is ready" or updating the plan
does not deliver the report.

## Default recommended plan

Every workflow `SKILL.md` carries a `## Plan` section directly after its
purpose statement and before `## Workflow`. It is the default plan the agent
loads into the live plan via `update_plan` as soon as it reads the skill, so
the user sees the whole flow up front and progress is tracked per step.

The section has two parts:

1. A short paragraph telling the agent to call `update_plan` to create or
   revise the named live plan from the steps below, keep reporting and
   follow-up as separate plan items, mark already-satisfied steps
   `completed`, and update statuses as each step's completion condition is
   met.
2. A checklist with one `- [ ] Step N. …` line per numbered `## Workflow`
   heading, in the same order and with the same count. Each line names the
   step's outcome and the tool it uses (`scan_local_git_workspace`,
   `ask_user_choice`, …) in one sentence.

Example shape for a workflow with eight separate steps:

```markdown
## Plan

After reading this skill, use `update_plan` to create or revise the live
CI/CD Reliability Progress plan using the eight numbered workflow headings
below as its steps. Keep reporting and follow-up as separate plan items.
Mark already-satisfied steps `completed` and update statuses when each
step's completion condition is met.

- [ ] Step 1. Scan local repositories with scan_local_git_workspace.
- [ ] Step 2. Select a repository using ask_user_choice.
- [ ] Step 3. Read the metric and benchmark references with skill_view.
- [ ] Step 4. Collect complete 30-day workflow, rerun, and merged-PR history.
- [ ] Step 5. Calculate the required metrics from the collected history.
- [ ] Step 6. Validate the calculations against the coverage and consistency requirements.
- [ ] Step 7. Respond with the report as a Markdown table and its coverage notes.
- [ ] Step 8. After the report is shown, use ask_user_choice to offer the next step.
```

Rules:

- The plan and the `## Workflow` headings are one list in two places. Adding,
  removing, or reordering a workflow step changes the plan in the same edit;
  a skill whose plan and headings disagree is incomplete.
- Each workflow step states its completion condition ("Complete when …") so
  the agent can move the matching plan item to `completed` without guessing.
- Steps the flow may legitimately skip (for example a repository already
  named in the request) say so in the step body and tell the agent to mark
  the skipped plan items satisfied rather than delete them.
- Tool-usage cards describe one call, not a flow, and do not carry a plan.

## Colocated workflow tests

Major skills that orchestrate a multi-step workflow must keep an end-to-end
`test_*.py` beside their `SKILL.md`. Exercise the real agent loop with the
shipped skill, checking tool-call order, arguments, and user-choice pauses.
Script model responses and external tool I/O for offline CI; keep skill loading
and host hooks real. Include the skill directory in default pytest discovery
and a CI shard, and run its test when changing the workflow.

These files sit inside `core/`, so the layer contracts apply to them: never
import `tools`, `integrations`, `surfaces`, or `bootstrap`. Resolve real action
tools (`ask_user_choice`, `skill_view`) through
`core.agent_harness.tools.action_tools.get_action_tool`; the registry behind it
is installed around every test by `tests/harness_providers_plugin.py`
(loaded from `pytest.ini`), not by `tests/conftest.py`, which does not reach
this tree.


## Skill metadata ownership and change date

Every `SKILL.md` frontmatter `metadata` block records who owns the skill, who
touched it last, and when:

- `owner` — the person who created the skill. Use their name, never a team
  label such as `Tracer Team`. Set it once at creation and do not change it
  when someone else edits the skill later.
- `last_changed_by` — the name of the person who most recently changed the
  skill.
- `last_changed_at` — the calendar date of that change as an unquoted ISO
  date (`YYYY-MM-DD`). No times, no timezones, no "today" — a reader must be
  able to tell how stale the card is without opening `git log`.

`last_changed_by` and `last_changed_at` move together. Whoever edits a skill
(body or frontmatter) must update both lines in the same change; a skill edit
that leaves either one behind is incomplete. Do not backfill the date from
memory when you are not the one who made the change — take it from
`git log -1 --format=%ad --date=short -- <SKILL.md>`.

```yaml
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-09
```

`tests/core/agent_harness/prompts/test_skill_metadata.py` fails a card whose
`last_changed_at` is missing, not a date, or in the future.

When you touch a skill that still carries a team label in `owner`, replace it
with the original author's name if you know it; otherwise leave it and note it
in the PR description rather than guessing.

## Where a card lives

Two kinds of card, two homes, and a `name` exists in exactly one of them:

- **Workflow** — a multi-step flow the agent follows (when to activate, sibling
  carve-outs, ordered steps, a report template, a schedule offer). Lives here:
  `skills/<name>/SKILL.md`, with the `metadata` block above. It gets one line
  in the always-on skills index and its full body via `skill_view`. On a
  harness card `tools:` narrows the tool catalog for the skill's answer turns;
  declare it only when the flow's tool set is closed (the onboarding demos do —
  `tests/interactive_shell/runtime/test_demo_picker.py::
  test_demo_skills_keep_their_tool_contracts_after_moving`), and omit it when
  the skill should keep the full catalog.
- **Tool usage** — how to call one tool or one tool family correctly:
  parameter selection, refusals, what the tool owns so the agent does not run
  raw `gh`/`git` around it. Lives next to the tool package
  (`integrations/<vendor>/tools/…/SKILL.md`,
  `tools/system/…/skills/<name>/SKILL.md`) with `tools:` frontmatter, and is
  appended to those tools' descriptions at registry load
  (`tools/registry_skill_guidance.py`, 2,400-char cap). Register the path in
  `_skill_guidance_files()`.

The same tool may have both — `operating-github-ci-fixer` (tool usage, beside
`fix_github_pr_ci`) and `fixing-github-ci` (workflow, here) — but they are two
cards with two names and two jobs. Tool-level facts (fork PRs are refused,
merged PRs need `branch`) belong in the tool-usage card; activation phrases,
sibling carve-outs, and reply shape belong in the workflow. Never give two
cards the same `name`.

## Naming conventions

The `name` field is the skill's identity: `loader.py` dedups on it, the
scheduler pins on it, and users type it (`opensre cron add --skill …`). Pick it
once, following these rules, and treat a rename as a breaking change.

1. **Shape is `<verb-ing>-<object>`** — gerund first, then what it acts on,
   2–4 hyphenated lowercase words. The verb says what the agent does; the
   object says to what. `fixing-github-ci`, `summarizing-sentry-issues`.
2. **Vendor is an adjective on the object, never a prefix.**
   `fixing-github-ci`, not `github-ci-fix` or `github-fixing-ci`. Every GitHub
   skill stays searchable by `-github-` while the leading word still names
   the activity.
3. **No artifact suffixes.** Do not append `-demo`, `-agent`, `-tool`,
   `-service`, `-skill`, `-report`. Demo status lives in `getting_started` /
   `demo_order` frontmatter; recurrence lives in `recurring`. The name must
   survive the skill graduating out of the demo menu.
4. **Placeholders are named for the activity they will become**, not for
   their status ("coming soon", "remote", "placeholder").
5. **Disambiguate siblings by verb, not by qualifier.** Two skills over the
   same object must differ in what they do: `reporting-github-ci-failures`
   (what is red now) vs `analyzing-github-ci-performance` (trend over a
   period); `scheduling-github-ci-fixes` (a recurring local repair loop) vs
   `delegating-github-ci-fixes` (the same repairs, run by the managed
   service).
   If you need a "Not for X, use Y" sentence in the description, first check
   whether a better verb pair removes the need.
6. **Directory name equals `name`** (kebab-case) for a dedicated skill
   directory, e.g. `skills/fixing-github-ci/SKILL.md`. A tool-usage card
   inside a Python tool package (`integrations/github/tools/github_cli/`)
   keeps the package's snake_case directory; only its frontmatter `name`
   follows this convention. The sibling report template is
   `<directory>_report.md`. A child of a nested tree (the onboarding demos)
   is `<letter>-<name>/`, where the single lowercase letter is the menu
   position (`a` ⇔ `demo_order: 1`) and everything after the first hyphen
   equals `name`: `onboarding-github-ci/a-analyzing-github-ci-performance/`.
   The letter orders siblings on disk and in the skills index; `demo_order`
   orders the menu, and `tests/core/agent/prompts/test_skills_demo.py` fails
   when the two disagree or the suffix drifts from `name`.
7. **Scheduler and telemetry keys are not skill names.** Starter-loop slugs,
   telemetry event names, and task-store params may reference a skill but
   must not be derived from its `name`, so a rename does not break stored
   tasks or dashboards. Read skill names from one constant where product
   code branches on them.
8. **Renaming a skill adds its old slug to `LEGACY_SKILL_NAMES`** in
   `skills/naming.py`. Persisted recurring schedules store the `name` they
   were confirmed with; the map lets `find_action_skill` and `skill_view`
   resolve the old slug, and the scheduler re-pins such a task to the new
   name on its next tick. A tool-usage card beside a tool also needs its
   path updated in `_skill_guidance_files()` — `tests/tools/test_registry.py`
   fails on a stale path.

Sanctioned verbs (add a new one here before using it): `analyzing`,
`connecting`, `delegating`, `delivering`, `fixing`, `investigating`,
`measuring`, `onboarding`, `operating`, `querying`, `reporting`,
`scheduling`, `summarizing`, `tracking`.

Avoid: vague objects (`helper`, `utils`, `tools`, `data`, `files`), reserved
prefixes (`anthropic-`, `claude-`), and mixing patterns across the collection.

Current collection:

| Name | Kind | Where | `tools:` |
|------|------|-------|----------|
| `delivering-morning-briefings` | workflow | `skills/` | — |
| `fixing-github-ci` | workflow | `skills/` | — |
| `fixing-github-security-alerts` | workflow | `skills/` | — |
| `investigating-incidents-with-runbooks` | workflow | `skills/` | — |
| `reporting-github-ci-failures` | workflow | `skills/` | — |
| `onboarding-github-ci` | workflow (master menu) | `skills/onboarding-github-ci/` | — |
| `analyzing-github-ci-performance` | workflow (demo A) | `skills/onboarding-github-ci/a-…/` | closed set |
| `scheduling-github-ci-fixes` | workflow (demo B) | `skills/onboarding-github-ci/b-…/` | closed set |
| `delegating-github-ci-fixes` | workflow (demo C, placeholder) | `skills/onboarding-github-ci/c-…/` | — |
| `connecting-slack` | workflow (demo D) | `skills/onboarding-github-ci/d-…/` | `cli_exec`, `slash_invoke` |
| `operating-github-cli` | tool usage | `integrations/github/tools/github_cli/` | `github_cli` |
| `operating-github-ci-fixer` | tool usage | `integrations/github/tools/ci_fix/` | `fix_github_pr_ci` |
| `operating-github-security-fixer` | tool usage | `integrations/github/tools/security_fix/` | `fix_github_security_alert` |
| `tracking-github-work-status` | tool usage | `integrations/github/tools/workflow/` | GitHub workflow tools |
| `measuring-github-star-velocity` | tool usage | `tools/system/python_execution_tool/skills/…` | `execute_python_code` |
| `summarizing-posthog-analytics` | tool usage | `integrations/posthog/tools/skills/…` | PostHog MCP tools |
| `summarizing-sentry-issues` | tool usage | `integrations/sentry/tools/skills/…` | Sentry issue and uptime tools |
| `querying-yandex-cloud` | tool usage | `integrations/yandex_cloud/tools/` | `find_yc_api`, `execute_yc_operation` |

The onboarding tree's pre-convention slugs (`onboarding-cicd-fix`,
`cicd-analytics-demo`, `cicd-reliability-agent`, `remote-managed-service`,
`slack-handoff`) live on only in `LEGACY_SKILL_NAMES`; do not reuse them.


## Implementation of Feedback Loops:

### Example: 
**Content review process**
1. Draft your content following the guidelines in STYLE_GUIDE.md
2. Review against the checklist:
   - Check terminology consistency
   - Verify examples follow the standard format
   - Confirm all required sections are present
3. If issues found:
   - Note each issue with specific section reference
   - Revise the content
   - Review the checklist again
4. Only proceed when all requirements are met
5. Finalize and save the document


##
