# Tracer Agent – Project Overview for AI Coding Assistant

## Workflow Expectations

- When “push” is mentioned, it means pushing the commit to GitHub and verifying that all linting checks and GitHub Actions pass for that commit.
- Before pushing any changes, always run `make demo` locally.

## Sensitive Data

- Never commit API keys, tokens, or secrets of any kind.

## Testing Approach

- Write tests as integration tests only. Do not use mock services.
- Tests should live alongside the code they validate.
- If the source file is large, create a separate test file in the same directory using the `_test.py` suffix.

Example:

```
app/agent/nodes/frame_problem/frame_problem.py
app/agent/nodes/frame_problem/frame_problem_test.py
```

## Linting

- Ruff is the only linter used in this project.
- Linting must pass before any push.

## Environment

- Do not use virtual environments.
- Use the system `python3` directly.

## Best Practices

- Always run linters before committing.
- Always validate changes with `make test`.
- Follow Go-style discipline in structure and formatting where applicable.
- Review all changes for potential security implications.

## What Not to Do

- Do not introduce fallback logic that relies on mock or fake data.
- Do not bypass tests or CI checks.

## GitHub Push and CI Verification Protocol

“Push” means completing the full push cycle, not just running `git push`.

### Required Steps Before Declaring a Push Successful

1. Ensure working tree is clean.
2. Run `make test` locally.
3. Run linters locally (`ruff`).
4. Push the branch to GitHub.

### Required Steps After Pushing

1. Verify GitHub authentication is working.
2. If `gh` reports HTTP 401, run `gh auth login`.
3. Ensure `GITHUB_TOKEN` is correctly scoped or unset if using `gh` credentials.
4. Check GitHub Actions for the pushed branch:
   - `gh run list --branch <branch> --limit N`
5. Identify the most recent workflow run for the commit.
6. Confirm CI status:
   - All required workflows must complete successfully.
   - A failed or cancelled workflow means the push is not complete.

### Failure Handling

- If CI fails, investigate and fix before proceeding.
- Do not proceed assuming CI will “probably pass”.
- If authentication blocks CI inspection, resolve auth first before continuing work.

### Completion Definition

A push is only considered complete when:

- Code is pushed.
- CI has run.
- CI has passed.

Optional but recommended:

- Capture CI run ID in commit or task notes.
- Call out infra or CI failures explicitly if unrelated to code changes.

### Why This Helps

- Prevents silent CI failures.
- Prevents broken demo branches.
- Forces agents to treat CI as part of the development loop, not an afterthought.

### Hard Rule

Never say “pushed” unless CI has been checked and verified green.
