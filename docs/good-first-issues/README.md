# Good First Issues

New to OpenSRE? This page is your starting point.

## What is a "good first issue"?

A good first issue is a task that is:

- **Self-contained** — you don't need to understand the full codebase to solve it
- **Well-scoped** — the expected output is clearly defined
- **Low risk** — a mistake won't break critical paths

They're designed so you can make a real contribution while getting familiar with the project.

## Find open issues

Browse issues tagged with the `good first issue` label:

[View good first issues on GitHub](https://github.com/Tracer-Cloud/opensre/issues?q=is%3Aopen+label%3A%22good+first+issue%22)

## How to pick and work on one

1. **Browse the list** — read the issue description and comments before claiming
2. **Comment to claim it** — post a comment like `"I'd like to work on this"` so maintainers can assign it to you
3. **Read the setup guide** — get your environment running first: [SETUP.md](../../SETUP.md)
4. **Fork and branch** — `git checkout -b issue/123-short-description`
5. **Make your changes** — keep the scope tight; one issue, one PR
6. **Run local checks** before opening a PR:
```bash
   make lint && make format-check && make typecheck && make test-cov
```
7. **Open a pull request** — link the issue with `Fixes #123` in your PR description

Full contribution flow is in [CONTRIBUTING.md](../../CONTRIBUTING.md).
After you open a PR, follow the [PR review flow](../guides/pr-review-flow.mdx)
(Greptile · human review · CI).

## Ask for help

Stuck? Don't guess — ask early.

- **Discord:** [#contribute](https://discord.gg/opensre)
- **GitHub:** comment directly on the issue

## A few tips

- Read through `CONTRIBUTING.md` before you start — it answers most questions upfront
- One concern per PR; don't bundle unrelated fixes
- If the issue feels unclear, ask for clarification before writing code
- AI-assisted code is fine, but you must understand and be able to explain every line (see [AI-Assisted PRs](../../CONTRIBUTING.md#ai-assisted-prs))
