---
name: delegating-github-ci-repairs
description: >-
  In the interactive shell, prepare the hosted gateway and delegate a bounded
  GitHub PR repair or remote demo. Use for remote CI repair onboarding.
getting_started: Run one repair in OpenSRE Cloud
demo_order: 3
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-10-02
  usecases:
    - For interactive-shell users running a GitHub CI repair on their hosted gateway.
  requires:
    - A reachable hosted gateway with a GitHub integration and an authenticated coding agent.
    - An interactive shell and a signed-in OpenSRE account in the organization for hosted gateway access.
    - GitHub write access to the selected PR; demo mode also needs private-repository creation.
  version: "2.8"
---

# Delegate a remote CI repair

This runs one bounded repair that finishes on the gateway without the shell. It does not set up continuous repository monitoring.

## Roles

**Orchestrator (local shell):**

- Checks the gateway is ready, picks the target, delegates, verifies and reports.

**Executor (gateway):**

- Runs the repair with the skill `scheduling-github-ci-repairs` and reports back to the shell.

## Plan

Use `update_plan` to create the live plan from the workflow headings below. Mark a step `in_progress` or `completed` in the same response as that step's tool call. A response that only calls `update_plan` is not progress.

**Inside the interactive shell:**

- [ ] Prepare the hosted gateway with check_hosted_gateway.
- [ ] Probe GitHub access on the gateway with one ask_hosted_gateway prompt.

**Inside the interactive shell:**

- [ ] Confirm the target and approval in one ask_user_choice question.
- [ ] Delegate execution with ask_hosted_gateway and retain its prompt ID.


**Remote Gateway agent:**

- [ ] Read the following skill: `scheduling-github-ci-repairs` to understand how to seed a demo PR and inside a demo repository and how to fix it. 
- [ ] Create the demo repository, failing branch, and PR with seed_ci_repair_demo (demo only). The returned failed_run_id is the failure confirmation.
- [ ] Schedule the bounded repair with schedule_ci_repair_loop and record its task id.
- [ ] Wait for the scheduled tick with get_ci_repair_loop and read its report.
- [ ] Verify the repair with one `pr view` call.
- [ ] Save evidence, remove the demo loop, and verify with one `finish_ci_repair_demo call`. Nothing on GitHub is deleted; the demo repository is kept.
- [ ] Respond with the outcome report as Markdown.

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

Report this gateway's GitHub access for a CI repair demo. Run only these calls and create nothing:

- [1] `github_cli ["api", "user", "--include"]` for the login. An `X-OAuth-Scopes` header means a classic PAT: list its scopes, which need `repo` and `workflow` (the demo pushes a workflow file). No header means a fine-grained or app token.

- [2] `github_cli ["api", "user/memberships/orgs", "--jq", "[.[] | {org: .organization.login, role, state}]"]`

- [3] For each organization: `github_cli ["api", "graphql", "-f", "query=query($o: String!) { organization(login: $o) { viewerCanCreateRepositories } }", "-F", "o=<org>"]`, Answer with the login, the token type and scopes, and one line per owner (the login plus each organization) saying whether it can create repositories. Then propose one new private demo repository as `<owner>/opensre-ci-repair-demo-<4 lowercase letters or digits>`.

**Complete when:**

- The gateway named the login, the token type, and at least one owner that can create repositories. Otherwise, a blocker is recorded.

### Display the final repair plan

Show the final repair plan titled `Remote Repair Plan`. Put the probe's findings (login, token type, owner) in the overview so the user can see it once. 

- `Create <owner>/<name> and run the demo there`
- `Use an existing pull request`

**Complete when:**

- When the chosen scope is known. 
- Keep asking with `ask_user_choice` if not all required parameters are known. 

### Delegate the repair

- Send one `ask_hosted_gateway` prompt: "This is a new request. Start a new plan from `scheduling-github-ci-repairs` for <target>; do not reuse plan steps, task IDs, or repositories from earlier in this conversation. Delete nothing on GitHub. Seed only with seed_ci_repair_demo. If that repository is not an OpenSRE CI repair demo, the tool seeds `opensre-ci-repair-demo-<4 characters>` itself and leaves the refused repository unchanged. Continue this same plan with the owner and repo the tool returns. Do not ask the user. github_cli, an organization repository listing, a code search, and list_github_actions_workflow_runs are outside the seed."
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

- The task's gateway ownership establishes independence from the shell; claim a tested disconnect only if the shell was actually disconnected during execution.

- Show the report once. Do not repeat the gateway's streamed steps.

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

