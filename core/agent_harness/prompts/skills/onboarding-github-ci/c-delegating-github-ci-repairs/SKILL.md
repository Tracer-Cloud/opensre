---
name: delegating-github-ci-repairs
description: >-
  In the interactive shell, prepare the hosted gateway and delegate a bounded
  GitHub PR repair or remote demo. Use for remote CI repair onboarding.
getting_started: Run one repair in OpenSRE Cloud
demo_order: 3
metadata:
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-10-04
  usecases:
    - For interactive-shell users running a GitHub CI repair on their hosted gateway.
  requires:
    - A reachable hosted gateway with a GitHub integration and an authenticated coding agent.
    - An interactive shell and a signed-in OpenSRE account in the organization for hosted gateway access.
    - GitHub write access to the selected PR; demo mode also needs private-repository creation.
  version: "2.13"
---

# Delegate a remote CI repair

This runs one bounded repair that finishes on the gateway without the shell. It does not set up continuous repository monitoring.

## Roles

**Orchestrator (local shell):**

- Checks the gateway is ready, picks the target, delegates, verifies and reports.

**Executor (gateway):**

- Calls `run_ci_repair_demo` once and reports that result back to the shell.

## Plan

Use `update_plan` to create the live plan from the workflow headings below. Mark a step `in_progress` or `completed` in the same response as that step's tool call. A response that only calls `update_plan` is not progress.

**Inside the interactive shell:**

- [ ] Prepare the hosted gateway with check_hosted_gateway.
- [ ] Probe GitHub access on the gateway with one ask_hosted_gateway prompt.

**Inside the interactive shell:**

- [ ] Confirm the target and approval in one ask_user_choice question.
- [ ] Delegate execution with ask_hosted_gateway and retain its prompt ID.


**Remote Gateway agent:**

- [ ] Call `run_ci_repair_demo` once for the approved owner and repo, without loading a skill.
- [ ] Respond with that tool's outcome as Markdown. Nothing on GitHub is deleted; the demo repository is kept.

**Inside the interactive shell:**

- [ ] Verify the outcome by re-reading the delegated prompt ID (verifies: true).
- [ ] Show the remote outcome and evidence as Markdown.
- [ ] Offer the next step or blocker resolution with ask_user_choice.

## Success criteria

The workflow succeeds only when:

1. The hosted gateway is healthy with active GitHub permissions.
2. A seed demo repository is configured with a failing PR 
3. The CI failure is detected remotely.
4. The remote repair loop fixes the failure without intervention from the local shell.

## Workflow Notes

### Prepare the hosted gateway

**Shell only:**

- Run `check_hosted_gateway()`. If it is not running, call `start_hosted_gateway()` once and check again until it is ready.
- Do not send a test prompt. The probe below is the first prompt, and its answer proves the gateway takes prompts.

**Complete when:**

- `check_hosted_gateway()` reports the gateway running.

### Probe GitHub access

Send one `ask_hosted_gateway` prompt:

Report this gateway's GitHub access for a CI repair demo. Call `probe_github_repair_access` once and create nothing. Answer with the login, the token type and scopes, and one line per owner (the login plus each organization) from `owners`, saying whether `can_create_repositories` is true. Do not propose a repository name.

**Complete when:**

- The gateway named the login, the token type, and at least one owner that can create repositories. Otherwise, a blocker is recorded.

### Display the final repair plan

If the opening message already answers `Create a private demo repository?` with `Create <owner>/<repo>` or `Create <repo>`, that is the target. Do not call `ask_user_choice` for it. Pass that owner and repo as the delegate `facts`. A name without an owner uses the login from the probe. `Don't create a demo repository` keeps the existing-pull-request path below.

Show the final repair plan titled `Remote Repair Plan`. Put the probe's findings (login, token type, owner) in the overview so the user can see it once. 

- `Create <owner>/<name> and run the demo there`
- `Use an existing pull request`

**Complete when:**

- When the chosen scope is known. 
- Keep asking with `ask_user_choice` if not all required parameters are known. 

### Delegate the repair

- Send one `ask_hosted_gateway` prompt: "This is a new request. Call `run_ci_repair_demo` once with owner <owner> and repo <repo>. Do not load a skill; this prompt is the whole task. Do not reuse task IDs or repositories from earlier in this conversation. Delete nothing on GitHub. Ask the user only about a blocked step. If the seed or schedule fails, return that failure and do not schedule another loop."
- Pass the target as `facts` (`demo`, `owner`, `repo`, `pr_number`). Keep the prompt ID.


**Complete when:** the gateway returned a task ID and outcome, or a blocker.

### Verify the remote outcome

### Verify the remote outcome

- Call `ask_hosted_gateway(prompt_id=<the delegated prompt ID>)` once. Send no new prompt.
- Check the record against the delegated report: same task ID, a fix commit, a passing run ID, and the loop removed.
- This re-read is the only verification. `github_cli` and `list_github_actions_workflow_runs` are outside this step. A short transcript still verifies when the record has the task ID, the fix commit, and the passing run ID.
- If it says the gateway is not running, leave it stopped. Mark this step blocked with "gateway stopped after reporting; the delegated report is unverified" and show that report.

**Complete when:**

- The re-read matches the report, or the step is blocked with its reason.

### Show the outcome in a report

- Report the target PR, task ID, repair outcome, available CI evidence links, and retained resources. State pending, blocked, or failed outcomes plainly and concisely. 

- Write each GitHub link as its full URL, such as `Pull request: https://github.com/<owner>/<repo>/pull/<n>`, not as Markdown link text: the terminal shows link text without its URL. Give the pull request, failing commit, failed run, fix commit, and passing run URLs that the delegated record has.

- Add a `Root cause analysis` section from the delegated record: what failed, the root cause, the fix (with its diff when the record shows one), and the verification. State only what the record says; if it has no root cause analysis, say the gateway did not report one.

- The task's gateway ownership establishes independence from the shell; claim a tested disconnect only if the shell was actually disconnected during execution.

- Show the report once. Do not repeat the gateway's streamed steps.

- Do not call `memory_recall` or `memory_remember` in this workflow.

**Complete when:**

- When the Markdown report has been shown. Keep the gateway running so an unfinished repair can continue.

### Offer the follow-up

After a successful repair report, one `ask_user_choice` with the title
`Hand off the next CICD fix?`, `allow_custom` false, and these options:

- Fix a failing PR from Slack by tagging @OpenSRE
- Guard failing PRs on one of your repos
- Not now

Complete when the `ask_user_choice` call for this menu has returned in
this turn. The user's answer arrives in the next turn.

- **Connect Slack so the agent can fix issues there:** call
  `skill_view(name="connecting-slack")` and follow that skill. A channel
  mention or DM hands off the next failing check. Do not post to Slack, and
  do not offer Telegram.
- **Schedule a repair loop on a repo you use, so the next failing PR gets fixed:**
  call `skill_view(name="scheduling-github-ci-repairs")` and follow that skill.
  Do not reuse the private demo. Select a repository the user already uses,
  so the loop stays and pushes a fix for the next failing pull request.
  The machine has to stay on.
- **Return to the shell:** acknowledge in one line and conclude.

### Blockers 

**GitHub connection blocker:**

- If missing credentials, direct the user to https://app.opensre.com/dashboard/github and follow the tool's continuation guidance after it is corrected.

**Complete when:**

- The blockers are resolved or the user wants to write their own specific answer. 
- For a pending task, offer to continue observing the same task ID or leave it running. 
- Keep these recovery choices separate from the successful-demo options above.

