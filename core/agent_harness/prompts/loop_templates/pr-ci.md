---
name: PR CI
description: Fixes failing CI checks on open pull requests.
cron: "28 * * * *"
mode: agent
---

Fix failing CI checks on open pull requests.

1. List open non-draft pull requests from branches in this repository whose latest commit has a failed check.
2. Skip pull requests with merge conflicts or a push in the last 10 minutes.
3. Pick one, read the failed check's logs, and find the root cause.
4. Fix it on the pull request's branch, run the relevant tests locally, and push a normal commit.
5. Never force-push or merge, and reply with the pull request link and what you changed.
