---
name: delegating-github-ci-repairs
description: >-
  Runs the CI repair loop for one repository on the organization's hosted OpenSRE
  gateway instead of this machine. The gateway uses the organization's GitHub
  credential from the web app's Integrations page. Use for the startup demo option
  "Run CI/CD improvements with a managed service (coming soon)"; the label keeps
  "(coming soon)" until the team has reviewed and tested this flow. Local monitoring belongs to
  scheduling-github-ci-repairs. Multi-step; load before acting.
getting_started: Run CI/CD improvements with a managed service (coming soon)
demo_order: 3
metadata:
  owner: Vincent
  last_changed_by: Yauhen
  last_changed_at: 2026-09-23
  usecases:
    - For organization admins who want CI repairs to run remotely, with nothing on their laptop.
    - For checking whether the hosted gateway is up and what it is running.
  requires:
    - A signed-in OpenSRE account (`opensre account login`) whose user is an organization admin.
    - A hosted gateway provisioned for the organization.
    - The organization's GitHub token saved on the web app's Integrations page.
  version: "2.0"
includes:
  - common/ask_once.md
---

# Remote managed service

Set up the bounded CI repair loop for one repository on the organization's
hosted gateway, and show the user where it runs and how to read its result.
Nothing runs on this machine.

## Workflow rules

- Never call `schedule_ci_repair_loop` or `fix_github_pr_ci` here: the loop
  runs on the gateway, through `ask_hosted_gateway`.
- The gateway answers with the organization's credentials, not this machine's.
  When its answer names `github` among the failed integrations, stop and tell
  the user to add the GitHub token on the web app's Integrations page (the
  reply already carries the link).
- `ask_hosted_gateway` reaches an external service, so the shell may ask the
  user to allow it; a headless run (`opensre ask`) needs
  `--allowed-tool ask_hosted_gateway`.
- When the gateway stops to ask (`needs_input`), the question opens as a menu
  in this shell. After the user answers, call `ask_hosted_gateway` again with
  the same `prompt_id`; their selection is sent for you. Repeat until the state
  is `done` or `failed`.

## Plan

Track progress with `update_plan`, not with headers or prose:

- On entry, before the first tool call, call `update_plan` with these steps
  verbatim, the first `in_progress`, and a one-line `explanation`:
  `Check the hosted gateway` / `Pick the repository` / `Delegate the repair loop` /
  `Report where it runs`. Mark `Delegate the repair loop` with `verifies: true`.
- After a step's tool results, call `update_plan` marking it `completed` and
  the next `in_progress`, in the same response as the next step's tool call.

## Workflow

### 1. Check the hosted gateway

Call `check_hosted_gateway`.

- Not signed in: tell the user to run `opensre account login` and stop.
- Not provisioned or not an admin: say so, name the web app, and stop.
- Stopped: call `start_hosted_gateway` (it asks for approval), then call
  `check_hosted_gateway` again until it reports healthy.

### 2. Pick the repository

Use the repository the user named. Otherwise call `scan_github_ci_health` and
ask once with `ask_user_choice` titled "Repository for the hosted repair loop":
one option per repository with at least one failing pull request, most
failures first, at most six. There is no disposable demo repository here: the
loop runs where the organization's GitHub token has access.

### 3. Delegate the repair loop

One call to `ask_hosted_gateway` with a complete prompt and the facts:

- prompt: "Schedule the bounded CI repair loop for <owner/repo> with
  schedule_ci_repair_loop (pull request <n> when the user named one). Report
  the task id, the deadline and the next run. Do not create demo resources."
- facts: `repository`, and `pr_number` when known.

Wait for the result. The shell shows the gateway's progress lines while it
works. Handle `failed_integrations` and `needs_input` as the rules say.

### 4. Report where it runs

Respond as Markdown: repository and pull request, task id and deadline from the
gateway's answer, that the loop runs on the hosted gateway and keeps running
after this shell closes, and the `prompt_id` to read the result later with
`ask_hosted_gateway`. Then offer one `ask_user_choice`:
- "Check the loop's status on the gateway"
- "Set up local monitoring instead"
- "Exit demo"
