---
name: Main CI
description: Fixes failing CI and scheduled workflows on the default branch.
cron: "58 * * * *"
mode: agent
---

Fix failing CI on the default branch.

1. List failed workflow runs on the default branch, including scheduled runs.
2. Skip any workflow whose latest run passed.
3. Pick one failure, read its job logs, and find the root cause.
4. Skip it if an open pull request already fixes it.
5. Fix it on a new branch, run the relevant tests locally, and open a pull request.
6. Never merge, and reply with the pull request link and the root cause.
