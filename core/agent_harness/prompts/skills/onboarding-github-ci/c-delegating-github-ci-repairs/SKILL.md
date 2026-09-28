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
  last_changed_at: 2026-09-28
  usecases:
    - For users asking whether OpenSRE can run CI/CD repairs for them as a managed service.
  requires:
    - Nothing; this skill only reports that the option is not available yet.
  version: "2.1"
---

# Setup Delegation to Remote managed service 
Deploy the existing GitHub CI repair workflow to the OpenSRE managed service.

This skill helps you setup OpenSRE as a remote managed cloud service to enable the agent to do work without your machine, and ensure that OpenSRE automatically keeps your CI green.

## Pre-Requisites 
- This skill is running in the interactive shell. 
- The opensre back-end is responding correctly 


## Skill plan 
After reading this skill, use `update_plan` to create the live plan from the workflow headings below:

- [ ] Check account and hosted gateway state.
- [ ] If needed, sign in with `/account login`.
- [ ] Start or provision the hosted gateway.
- [ ] Verify the gateway is healthy.
- [ ] Send a message to the gateway and ensure you receive one in return.
- [ ] Verify the configured repository is monitored remotely.
- [ ] Check if Gateway has the required permissions and GitHub access to monitor to the target repository. If not help the user to set it up correctly. 
- [ ] Trigger a test CI failure on a demo or test repository.
- [ ] Confirm the remote agent detects and repairs it.
- [ ] Respond with the outcome report as Markdown.
- [ ] After the report is shown, offer the follow-up with ask_user_choice.

If validation fails, diagnose the deployment or monitoring configuration and retry verification. 

## Success criteria

The workflow succeeds only when:

1. The hosted gateway responds to prompts from the interactive shell and they are returned back.
2. Scheduled loop is configured with the following skill core/agent_harness/prompts/skills/repair-github-ci.
3. A CI failure triggered after deployment is detected remotely.
4. The remote repair loop fixes the failure without the local shell remaining active.

## Configure remote gateway Github access 
In order to use this skill correctly, you need to configure the remote GitHub integrations correctly. To do that you need to enter you GitHub integration here:

https://app.opensre.com/dashboard/github

# Verification 
What needs to be verified in the remote:
- Does remote storage work 
- Does GitHub token work 

## Step #final - Ask user choice 
After successful validation, use `ask_user_choice`:

- Configure Slack or Telegram
- Add more scheduled tasks
- Exit to interactive shell 

## Relevant Tools 
You can use all tools, including the following tools that are used to communicate from the interactive shell to the gateway, but should not be used inside the gateway:

- `check_hosted_gateway()` — check whether it exists and is running.
- `ask_hosted_gateway(prompt, facts)` — send work to the managed gateway.
- `ask_hosted_gateway(prompt_id=...)` — continue a request awaiting input or retrieve its result.
- `start_hosted_gateway()` - start control the gateway lifecycle.
- `stop_hosted_gateway()`- stop control the gateway lifecycle.