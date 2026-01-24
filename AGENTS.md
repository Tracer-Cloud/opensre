# Tracer Agent – Project Overview for AI Coding Assistant

## Workflow Expectations

* When “push” is mentioned, it means pushing the commit to GitHub and verifying that all linting checks and GitHub Actions pass for that commit.
* Before pushing any changes, always run make test locally.

## Sensitive Data

* Never commit API keys, tokens, or secrets of any kind.

## Testing Approach

* Write tests as integration tests only. Do not use mock services.
* Tests should live alongside the code they validate.
* If the source file is large, create a separate test file in the same directory using the _test.py suffix.

Example
src/agent/nodes/frame_problem/frame_problem.py
src/agent/nodes/frame_problem/frame_problem_test.py

## Linting

* Ruff is the only linter used in this project.
* Linting must pass before any push.

## Environment

* Do not use virtual environments.
* Use the system python3 directly.

## Best Practices

* Always run linters before committing.
* Always validate changes with make test.
* Follow Go-style discipline in structure and formatting where applicable.
* Review all changes for potential security implications.

## What Not to Do

* Do not introduce fallback logic that relies on mock or fake data.
* Do not bypass tests or CI checks.
