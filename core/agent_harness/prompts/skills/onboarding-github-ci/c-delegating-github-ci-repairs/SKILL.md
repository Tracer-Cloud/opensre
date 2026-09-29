---
name: delegating-github-ci-repairs
description: >-
  In the interactive shell, prepare the hosted gateway and delegate a bounded
  GitHub PR repair or remote demo. Use for remote CI repair onboarding. Gateway
  execution and repair-status requests belong to operating-github-ci-repairs.
getting_started: Run CI/CD repairs remotely
demo_order: 3
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-29
  usecases:
    - For interactive-shell users running a GitHub CI repair on their hosted gateway.
  requires:
    - An interactive shell and an organization administrator account for hosted gateway access.
    - A reachable hosted gateway with a GitHub integration and an authenticated coding agent.
    - GitHub write access to the selected PR; demo mode also needs private-repository creation.
  version: "2.4"
---

# Delegate a remote CI repair

This runs one bounded repair that finishes on the gateway without the shell. It does not set up continuous repository monitoring.

## Roles
**Orchestrator (local shell):**
- Checks the gateway is ready, picks the target, delegates, verifies and reports.

**Executor (gateway):**
- Runs the repair with the skill `scheduling-github-ci-repairs` and reports back to the shell.

## Plan
Use `update_plan` to create the live plan from the workflow headings below:

**Inside the interactive shell:**
- [ ] Prepare the hosted gateway with check_hosted_gateway.
- [ ] Ask remote gateway agent to understand available GitHub account owner and if the required permissions are available 

**Remote Gateway agent:**
- [ ] Do a check to understand what GitHub integration credentials are configured 
- [ ] Confirm permissions and wether there are permissions to create a seed repository

**Inside the interactive shell:**
- [ ] Based on information from Gateway agent ask user for permission to execute the plan based on the permissions and propose repository name and branch etc. 
- [ ] Delegate execution with ask_hosted_gateway and retain its prompt ID.

**Remote Gateway agent:**
- [ ] Execute the skill `scheduling-github-ci-repairs` 

**Inside the interactive shell:**
- [ ] Verify the remote repair outcome through ask_hosted_gateway.
- [ ] Show the remote outcome and evidence as Markdown.
- [ ] Offer the next step or blocker resolution with ask_user_choice.

## Success criteria

The workflow succeeds only when:

1. The hosted gateway is healthy with active GitHub permissions.
2. A seed demo repository is configured with a failing PR 
3. The CI failure is detected remotely.
4. The remote repair loop fixes the failure without intervention from the local shell.

## Workflow

### Prepare the hosted gateway
These steps are for the interactive shell only: 
- Run `check_hosted_gateway()` and use`start_hosted_gateway()` as appropriate if none is running, then check readiness again. 
- Ensure that the gateway can receive a simple prompt, are you running correctly? And that the response is recorded back inside the interactive shell. 

**Completed when:**
- The gateway is ready. 

### Ask for missing repair-target information
The goal for this step is to retrieve the necescarry information to execute a demo. 

Communicate with the remote Gateway to ask the following questions. 

- Use the user's existing PR selection or demo choice or ask once with
`ask_user_choice`, title `Remote Repair Target`, offering:
- `Use a disposable demo repository`
- `Use an existing pull request`
- with custom answers enabled.

For an existing target:
- obtain its PR URL
- Owner
- Repository name
- PR number and link

The bounded tool repairs a PR; a repository or branch alone is incomplete.

**Complete when**
- When the chosen scope is known. 
- Keep asking until all required parameters are known. 

### Blockers 
**GitHub connection blocker**
- If missing credentials, direct the user to https://app.opensre.com/dashboard/github and follow the tool's continuation guidance after it is corrected.

Complete when the remote task has a terminal outcome, or a concrete blocker
prevents further verification. 

Claim a successful demo only with the failing
run, repair commit, and passing run from that task. An accepted request, token
permission check, or successful scheduler delivery alone proves no repair.

**Complete when**
- The blockers are resolved or the user wants to write their own specific answer. 
- For a pending task, offer to continue observing the same task ID or leave it running. 
- Keep these recovery choices separate from the successful-demo options above.

### Show the outcome

Report the target PR, task ID, repair outcome, available CI evidence links, and
retained resources. State pending, blocked, or failed outcomes plainly. The
task's gateway ownership establishes independence from the shell; claim a tested
disconnect only if the shell was actually disconnected during execution.

Complete when the Markdown report has been shown. Keep the gateway running so
an unfinished repair can continue.

### Offer the follow-up

After a successful repair report, use `ask_user_choice`:

- Configure Slack or Telegram
- Add more scheduled tasks
- Exit to interactive shell

**Complete when:**
- The appropriate menu is offered. 
- Keep this step pending until the report has been shown