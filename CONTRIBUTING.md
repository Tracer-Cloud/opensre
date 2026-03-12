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
- **Questions / "how do I"**: use the docs or email hello@tracer.cloud (GitHub Issues are for actionable engineering work).
- **Security issues**: do **not** open a public issue; follow `SECURITY.md`.

## Setting up locally

### Prerequisites

- Python 3.11 or higher
- Git

### 1. Fork and clone

```bash
git clone https://github.com/<your-username>/open-sre-agent.git
cd open-sre-agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
pip install -e .
```

### 3. Set up your environment file

Create a `.env` file in the project root:

```bash
cp .env.example .env   # if .env.example exists
# or create it manually:
touch .env             # Linux/macOS
type nul > .env        # Windows CMD
```

Add your API key to `.env`:

```
ANTHROPIC_API_KEY=your_key_here
```

> **Note:** An `ANTHROPIC_API_KEY` is only required to run the agent end-to-end. For docs fixes, small code changes, and running unit tests, you do not need an API key.

### Windows setup

`make` is not available by default on Windows. Use the Python equivalents instead:

| macOS/Linux      | Windows equivalent                                                                                                   |
| ---------------- | -------------------------------------------------------------------------------------------------------------------- |
| `make lint`      | `python -m ruff check app/ tests/`                                                                                   |
| `make typecheck` | `python -m mypy app/`                                                                                                |
| `make test-cov`  | `python -m pytest -v --cov=app --cov-report=term-missing --ignore=tests/test_case_kubernetes_local_alert_simulation` |

If `ruff` or `mypy` are not found, locate them with:

```bash
# Git Bash / Linux / macOS
find ~/.local -name "ruff" 2>/dev/null

# Windows Git Bash
find ~/AppData -name "ruff.exe" 2>/dev/null
```

Then run using the full path, e.g.:

```bash
/c/Users/<your-username>/AppData/Roaming/Python/Python313/Scripts/ruff.exe check app/ tests/
```

## Development workflow

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests (where applicable)
4. Run the project's checks locally before opening a PR:

   **macOS/Linux:**

   ```bash
   make lint        # ruff linter
   make typecheck   # mypy
   make test-cov    # pytest with coverage
   ```

   **Windows:**

   ```bash
   python -m ruff check app/ tests/
   python -m mypy app/
   python -m pytest -v --cov=app --cov-report=term-missing --ignore=tests/test_case_kubernetes_local_alert_simulation
   ```

   All three must pass. CI runs the same checks and a PR cannot be merged if they fail.

5. Open a pull request

> **Note on CI:** The `CI / test-kubernetes` check fails on all PRs — this is a pre-existing infrastructure issue requiring live AWS credentials that only the core team has access to. It is set to `continue-on-error: true` and will not block your PR from merging.

### Running tests without API keys

Most unit tests do not require external services or API keys. To run only those tests:

```bash
# macOS/Linux
make test-cov

# Windows
python -m pytest -v --cov=app --cov-report=term-missing --ignore=tests/test_case_kubernetes_local_alert_simulation
```

Tests that require real API keys or external services (Grafana, Datadog, CloudWatch, AWS) are in the `tests/test_case_*` directories and will be skipped or fail gracefully if credentials are not set. You do not need to fix those failures locally.

### Pull request guidelines

To keep PRs easy to review:

- Keep PRs **focused** (one logical change per PR)
- Describe **what** changed and **why**
- Include relevant context (links to issues, logs, screenshots)
- Avoid drive-by refactors mixed with functional changes
- Reference the issue your PR addresses with `Closes #<issue number>` in the description

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

By contributing, you agree that your contributions will be licensed under the project's license (see `LICENSE`).
