---
name: <verb-ing>-<object>
description: >-
  <State what this workflow does, for whom, and when the agent should load it.>
metadata:
  owner: <original author>
  last_changed_by: <person making this edit>
  last_changed_at: YYYY-MM-DD
  version: "1.0"
  usecases:
    - <Intended user and concrete workflow.>
  requires:
    - <Required tool, credential/access level, or execution environment.>
# Optional boolean eligibility; the scheduled task supplies cron and timezone.
# recurring: true
# Optional demo membership; the master menu derives its choices from these fields.
# getting_started: <Menu label>
# demo_order: 1
# Optional local instruction inclusion, distinct from on-demand references/ files.
# includes:
#   - common/ask_once.md
---

# <Workflow title>

<State the concrete outcome.>

## Plan

Use `update_plan` to track the numbered workflow steps below. Keep already
satisfied steps and mark them completed. Update each status when its completion
condition is met.

- [ ] Step 1. <Resolve the required inputs using the owning tool.>
- [ ] Step 2. <Perform the work with the named tool.>
- [ ] Step 3. <Deliver the verified outcome.>

## Workflow

### 1. <Resolve inputs>

<Use known scope, ask only for missing decisions, and name the tool that checks
prerequisites. List the conditions under which this step is already satisfied.>

Complete when <observable input and prerequisite condition>.

### 2. <Perform the work>

<Name the execution tool and the required arguments. Put branch-specific
reference material behind an explicit link and state when to read it.>

Complete when <observable tool result>.

### 3. <Deliver the outcome>

<State the output format and evidence required to support the result.>

Complete when <the user has received the result>.

<!-- Authoring notes: copy to skills/<name>/SKILL.md, replace placeholders,
     set the actual unquoted change date, and remove these notes. Follow
     ../AGENTS.md. Single-call tool-usage cards use their separate schema.
     Add an optional pre_execute ask_user_choice menu only when the host must
     open it on entry. Describe mid-flow questions in the numbered workflow.
     Run the raw-card validator test and relevant workflow tests before release. -->
