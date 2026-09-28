---
name: delegating-github-ci-repairs
description: >-
  Deploys GitHub CI repair monitoring to the OpenSRE managed service so repairs
  continue without the user's machine. Use when the user wants remote,
  scheduled, or always-on CI/CD repair.
getting_started: Run CI/CD repairs remotely
demo_order: 3
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-14
  usecases:
    - For users asking whether OpenSRE can run CI/CD repairs for them as a managed service.
  requires:
    - Nothing; this skill only reports that the option is not available yet.
  version: "1.0"
---

# Setup Delegation to Remote managed service 
Deploy the existing GitHub CI repair workflow to the OpenSRE managed service.

This skill helps you setup OpenSRE as a remote managed cloud service to enable the agent to do work without your machine, and ensure that OpenSRE automatically keeps your CI green.


## Objective 
- Is to connect to a managed fargate container that spins up a ci-cd-repair loop: core/agent_harness/prompts/skills/repair-github-ci
- This is seperate from skill core/agent_harness/prompts/skills/onboarding-github-ci/d-connecting-slack

## Tools to use 
- `check_hosted_gateway()` — check whether it exists and is running.
- `ask_hosted_gateway(prompt, facts)` — send work to the managed gateway.
- `ask_hosted_gateway(prompt_id=...)` — continue a request awaiting input or retrieve its result.
- `start_hosted_gateway()` - start control the gateway lifecycle.
- `stop_hosted_gateway()`- stop control the gateway lifecycle.

## Pre-Requisites 
- This skill is running in the interactive shell. 
- The opensre back-end is responding correctly 


## Skill plan 
After reading this skill, use `update_plan` to create the live plan from the workflow headings below:

- [ ] Check account and hosted gateway state.
- [ ] If needed, sign in with `/account login`.
- [ ] Start or provision the hosted gateway.
- [ ] Verify the gateway is healthy.
- [ ] Verify the configured repository is monitored remotely.
- [ ] Check if Gateway has the required permissions and GitHub access to monitor to the target repository. If not help the user to set it up correctly. 
- [ ] Trigger a test CI failure on a demo or test repository.
- [ ] Confirm the remote agent detects and repairs it.
- [ ] Respond with the outcome report as Markdown.
- [ ] After the report is shown, offer the follow-up with ask_user_choice.

If validation fails, diagnose the deployment or monitoring configuration and retry verification. 

## Success criteria

The workflow succeeds only when:

1. The hosted gateway is healthy.
2. A CI failure triggered after deployment is detected remotely.
3. The remote repair loop fixes the failure without the local shell remaining active.

## Configure remote gateway Github access 
We need to give users their GitHub access token 

What needs to be verified in the remote:
- Does remote storage work 
- Does GitHub token work 

## Step #final - Ask user choice 
After successful validation, use `ask_user_choice`:

- Configure Slack or Telegram
- Add more scheduled tasks
- Exit to interactive shell 

## Analytics

Record:

- `hosted_gateway_started`
- `hosted_gateway_healthy`
- `remote_ci_monitoring_started`
- `test_ci_failure_triggered`
- `remote_ci_failure_detected`
- `remote_ci_repair_succeeded`
