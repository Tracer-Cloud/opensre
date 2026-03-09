# Contributing

Thanks for your interest in contributing to Tracer.

This document describes how to propose changes, report bugs, and submit pull requests in a way that keeps review fast and the project reliable.

## Quick links

- Docs: https://www.tracer.cloud/docs
- Support / contact: hello@tracer.cloud
- Book a demo: https://www.tracer.cloud/demo
- Trust Center: https://trust.tracer.cloud/

## Choose the right channel

- **Bugs & small fixes**: open a GitHub Issue (if one exists) and/or submit a PR.
- **New features / behavioral changes**: open a GitHub Issue first to discuss the approach.
- **Questions / “how do I”**: use the docs or email hello@tracer.cloud (GitHub Issues are for actionable engineering work).
- **Security issues**: do **not** open a public issue; follow `SECURITY.md`.

## Development workflow

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests (where applicable)
4. Run the project’s checks locally before opening a PR:
   ```bash
   make lint        # ruff linter
   make typecheck   # mypy
   make test-cov    # pytest with coverage
   ```
   All three must pass. CI runs the same checks and a PR cannot be merged if they fail.
5. Open a pull request

### Pull request guidelines

To keep PRs easy to review:

- Keep PRs **focused** (one logical change per PR)
- Describe **what** changed and **why**
- Include relevant context (links to issues, logs, screenshots)
- Avoid drive-by refactors mixed with functional changes

If your PR changes user-visible behavior or output, include:

- **Before** and **after** evidence (screenshots, logs, CLI output, etc.)

## Code quality expectations

- Prefer clarity over cleverness
- Add tests for bug fixes and non-trivial logic
- Keep public APIs stable; call out breaking changes explicitly
- Update documentation when behavior or configuration changes

## AI-assisted contributions

AI-assisted PRs are welcome.

If you used an AI tool to generate any portion of the change, please include:

- A note in the PR description that it is **AI-assisted**
- The level of testing performed (untested / lightly tested / fully tested)
- Anything a reviewer should double-check (assumptions, edge cases)

## Reporting bugs

When filing a bug, include:

- What you expected to happen
- What actually happened
- Steps to reproduce (minimal repro if possible)
- Environment details (OS, version, relevant config)
- Logs or error output (redact secrets)

## Licensing

By contributing, you agree that your contributions will be licensed under the project’s license (see `LICENSE`).
