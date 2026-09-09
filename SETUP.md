# Development environment setup

## Prerequisites

- **Python 3.12+** — required by [`pyproject.toml`](pyproject.toml) (`requires-python = ">=3.12"`). CI workflows use **Python 3.13** (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)). [`.tool-versions`](.tool-versions) pins Python **3.13**, **uv**, **ruff**, and **mypy** (versions aligned with [`uv.lock`](uv.lock) where applicable) plus Node/pnpm for mise/asdf-style managers — optional; normal flows install **ruff** and **mypy** into `.venv` via **`make install`** / **`uv sync`**.
- Git
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — required for `make install` (locked deps from `uv.lock`)
- **Make** — standard on macOS/Linux; Windows options below

## Supported platforms and architectures

Release availability is separate from default CI coverage. Use the install path for your platform below, then follow the matching install guide for the first-run flow. The [Quickstart](docs/quickstart.mdx) has the same flow with screenshots.

| OS | Architecture | Install paths | Notes |
| --- | --- | --- | --- |
| macOS | arm64, x86_64 | [curl installer](https://install.opensre.com), [Homebrew](https://github.com/tracer-cloud/homebrew-tap), binaries: [arm64](https://github.com/Tracer-Cloud/opensre/releases/download/main-build/opensre_main_darwin-arm64.tar.gz), [x86_64](https://github.com/Tracer-Cloud/opensre/releases/download/main-build/opensre_main_darwin-x64.tar.gz) | See the [macOS install steps](https://www.opensre.com/docs/quickstart#install-opensre-macos). |
| Linux | x86_64, arm64 | [curl installer](https://install.opensre.com), [Homebrew](https://github.com/tracer-cloud/homebrew-tap), binaries: [x86_64](https://github.com/Tracer-Cloud/opensre/releases/download/main-build/opensre_main_linux-x64.tar.gz), [arm64](https://github.com/Tracer-Cloud/opensre/releases/download/main-build/opensre_main_linux-arm64.tar.gz) | See the [Linux install steps](https://www.opensre.com/docs/quickstart#install-opensre-linux). |
| Windows | x64 | [PowerShell installer](https://install.opensre.com), binary: [x64 ZIP](https://github.com/Tracer-Cloud/opensre/releases/download/main-build/opensre_main_windows-x64.zip) | See the [Windows install steps](https://www.opensre.com/docs/quickstart#install-opensre-windows). |
| Windows | arm64 | — | **Unsupported.** It is not in the default release matrix because `cryptography` does not publish Windows arm64 wheels; source installs are best effort only. |

The binary links above are the rolling `main` build — the same channel `install.opensre.com` uses by default. For a pinned version, download the matching `opensre_<version>_<target>` asset from the [releases page](https://github.com/Tracer-Cloud/opensre/releases/latest). Each archive ships a `.sha256` file next to it.

Main CI runs mostly on `ubuntu-latest`. Windows CI is optional and runs only when a PR has the `ci:windows` label, so it is useful signal rather than a guarantee that every platform is covered by default.

## Quick setup (all platforms)

1. Fork and clone:

```bash
git clone https://github.com/YOUR_USERNAME/opensre.git
cd opensre
```

2. Install uv if needed:

- **macOS/Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh` (or the [uv install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Windows (PowerShell):** `irm https://astral.sh/uv/install.ps1 | iex`  
  Or: `winget install --id astral-sh.uv -e`

3. Install dependencies:

```bash
make install
```

Without Make (equivalent to `make install`):

```bash
uv sync --frozen --extra dev
uv run python -m infrastructure.analytics.install
```

4. Verify:

```bash
make lint && make format-check && make typecheck && make test-cov
```

`format-check` is what CI enforces for formatting; include it before opening a PR.

---

## VS Code dev container

1. Install the **Dev Containers** extension in VS Code.
2. Start Docker Desktop, OrbStack, Colima, or another Docker-compatible runtime on the host.
3. Open the repository and run **Dev Containers: Reopen in Container**.

The image is built from [`.devcontainer/Dockerfile`](.devcontainer/Dockerfile) (**Python 3.13**). **`postCreateCommand`** creates `.venv-devcontainer` and runs **`pip install -e '.[dev]'`** (not `uv`). The interpreter VS Code uses is `.venv-devcontainer/bin/python`.

On the host, most contributors use **`make install`** + **`uv run`** instead; both approaches are valid.

---

## Windows-specific setup

Windows does not ship **make**. Pick one path below.

### Option A: Chocolatey (recommended)

1. Open PowerShell **as Administrator**.
2. Install Chocolatey (review the script first):

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
```

3. Install make:

```powershell
choco install make
```

4. Restart the terminal and verify: `make --version`.

### Option B: winget

```powershell
winget install GnuWin32.Make
```

Restart the terminal, then `make --version`.

### Option C: No Make

Run equivalents from the repo root (same shell where `uv` is on `PATH`). Prefer **`make test-cov`** when possible — the full pytest line is in the [`Makefile`](Makefile) under the `test-cov` target (`pytest -n auto`, coverage, and ignores).

```bash
uv sync --frozen --extra dev
uv run python -m infrastructure.analytics.install

uv run ruff check config core gateway integrations infrastructure surfaces tools tests/
uv run ruff format --check config core gateway integrations infrastructure surfaces tools tests/
uv run mypy config core gateway integrations infrastructure surfaces tools

uv run pytest -n auto -v \
  --cov=config --cov=core --cov=gateway --cov=integrations \
  --cov=infrastructure --cov=surfaces --cov=tools --cov-report=term-missing
```

---

## Troubleshooting

### Commands not using the project environment

- Prefer **`uv run <command>`** from the repo root.
- Refresh deps: **`uv sync --frozen --extra dev`**.

### Command not found: python

- Install Python **3.12+** and ensure it is on `PATH` (`python --version`).

### Command not found: uv

- Install uv (links above), then restart the terminal.

### `make install` / `uv sync` fails

- Run commands from the repository root; ensure **`uv.lock`** is present.
- Upgrade uv: **`uv self update`**.
- If the lockfile does not match **`pyproject.toml`**, run **`uv lock`** locally and commit the updated lockfile (or open a PR).

### make: command not found (Windows)

- Install make (above) or use Option C.

### Import errors when running code

- Use **`uv run`** from the repo root.
- Re-run **`uv sync --frozen --extra dev`**.

### Boot capability warnings (`curl`, `shell`, `network`, `python` gaps)

Boot logs non-fatal warnings when a `PATH` tool is missing or a sandbox probe fails. Sandbox lines look like `<capability> is unavailable in this environment (probe returned unavailable) — the agent will not be able to use it.` The two network rows are **expected** on a normal machine.

| Boot warning | Cause | Impact | What to do |
| :--- | :--- | :--- | :--- |
| **`curl is not on PATH`** | `curl` is not on `PATH`. | The agent is told not to shell out to `curl`. | **macOS:** `brew install curl`<br />**Linux:** `sudo apt-get install -y curl`<br />**Windows:** `winget install cURL.cURL` |
| **`no interactive shell (bash/sh) on PATH`** | Neither `bash` nor `sh` is on `PATH`. | The agent is told it cannot run shell commands. | **Linux:** `sudo apt-get install -y bash`<br />**macOS:** keep `/bin` on `PATH`.<br />**Windows:** Git Bash (`winget install Git.Git`) or WSL. |
| **`network egress is blocked for sandboxed code by default`** | Default sandbox policy blocks outbound sockets. | Sandboxed Python cannot open raw sockets. | Expected. Ignore it. Use configured integrations for outbound HTTP. Do **not** set `OPENSRE_ALLOW_NETWORK=1` — that only hides the warning. |
| **`network requests is unavailable in this environment`** | The sandbox network probe uses the same default block. | Same as the previous row. | Expected. Same as the previous row. |
| **`python execution is unavailable in this environment`** | The sandbox could not run a short Python snippet, often because the OpenSRE temp dir is not writable. That dir is `opensre` under Python's process temp (`tempfile.gettempdir()`), which may be `$TMPDIR`, `%TEMP%`, `%TMP%`, or `/tmp` — not a fixed path. | The agent is told it cannot run sandboxed Python. | Create the directory the process actually uses (it prints the path): `uv run python -c "from pathlib import Path; import tempfile; p = Path(tempfile.gettempdir()) / 'opensre'; p.mkdir(parents=True, exist_ok=True); p.chmod(0o700); print(p)"`<br />Then `make install` (or `uv sync --frozen --extra dev`). |
| **`shell commands is unavailable in this environment`** | No `bash`/`sh` on `PATH` (same check as the shell row). | The agent is told it cannot run shell commands. | Same install steps as **`no interactive shell (bash/sh) on PATH`**. |
| **`file reading is unavailable in this environment`** | The process cannot list the current working directory. | The agent is told it cannot read local files. | Run from a readable checkout (`ls .` should succeed). |

### `opensre` does not pick up local code edits

`make install` installs this repo in **editable** mode into `.venv`, but another **`opensre`** may appear earlier on **`PATH`** (installer binary, version manager, `~/.local/bin`, etc.).

1. Prefer **`uv run opensre …`** from the repository root.
2. Or prepend the venv: `export PATH="$(pwd)/.venv/bin:$PATH"` (macOS/Linux), then **`hash -r`** / new shell, and confirm **`which opensre`** points at **`<repo>/.venv/bin/opensre`**.

---

## Verify your setup

```bash
make lint && make format-check && make typecheck && make test-cov
```

If those pass, you are ready to develop. Contribution flow: **[CONTRIBUTING.md](CONTRIBUTING.md)**. Deeper contributor topics (benchmark, deployment, telemetry detail): **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.

---
