# Development Environment Setup

## Prerequisites

- Python 3.11 or later
- Git
- Make (standard on macOS/Linux; see Windows section below)

## Quick Setup (All Platforms)

1. Fork and clone the repo:
   ```bash
   git clone https://github.com/YOUR_USERNAME/open-sre-agent.git
   cd open-sre-agent
   ```

2. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

4. Verify setup by running checks:
   ```bash
   make lint && make typecheck && make test-cov
   ```

All three must pass before you're ready to develop.

---

## Windows-Specific Setup

Windows does not include `make` by default. Install it to use our development task runner.

### Option A: Chocolatey (Recommended)

1. Open PowerShell as Administrator
   - Search "PowerShell" in Start Menu
   - Right-click → "Run as administrator"

2. Install Chocolatey (review the script first):
   ```powershell
   Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
   ```

3. Install make:
   ```powershell
   choco install make
   ```

4. Restart your terminal and verify:
   ```bash
   make --version
   ```

### Option B: winget

If you prefer winget (Windows Package Manager):

```powershell
winget install GnuWin32.Make
```

Restart your terminal and verify:
```bash
make --version
```

### Option C: Manual Commands (No make required)

If you can't install make, you can run these approximate equivalents directly instead (they are close to, but not always identical to, the Makefile targets; see comments for differences):

```bash
# Linting (rough equivalent of `make lint`; this also applies auto-fixes via --fix)
python -m ruff check app/ tests/ --fix

# Type checking (equivalent of `make typecheck`)
mypy app/

# Tests with coverage (rough equivalent of `make test-cov`; the Makefile version may add --cov-report/--ignore flags)
pytest --cov=app tests/
```

---

## Troubleshooting

### Virtual environment not activating
- **macOS/Linux:** Make sure you ran `source .venv/bin/activate`
- **Windows:** Use `.venv\Scripts\activate` instead

### Command not found: python
- Make sure Python 3.11+ is installed and in your PATH
- Verify with: `python --version`

### pip install fails
- Update pip: `pip install --upgrade pip`
- Try installing in the venv again: `pip install -e ".[dev]"`

### make: command not found (Windows)
- See Windows-Specific Setup section above
- Or use Option C (manual commands)

### Import errors when running code
- Make sure you've activated the virtual environment
- Reinstall dependencies: `pip install -e ".[dev]"`

---

## Verify Your Setup

Run this to confirm everything is working:

```bash
make lint && make typecheck && make test-cov
```

If all three pass, you're ready to start developing! See `CONTRIBUTING.md` for the development workflow.


---

## Running OpenSRE MCP Server

You can start the MCP server with:
```bash
opensre-mcp
```

This exposes the `run_rca` tool for MCP clients.

---

## Connecting OpenClaw

OpenClaw is an AI coding assistant that can call OpenSRE's `run_rca` tool via MCP — letting you investigate production errors without leaving your editor.

### Step 1: Add OpenSRE to OpenClaw's MCP config

In OpenClaw, open **Settings → MCP Servers** and add:

```json
{
  "mcpServers": {
    "opensre": {
      "command": "opensre-mcp",
      "args": []
    }
  }
}
```

If `opensre-mcp` is not on your `PATH`, use the full path:
```json
{ "command": "/path/to/venv/bin/opensre-mcp" }
```

### Step 2: Configure at least one observability integration

OpenSRE needs credentials to query logs/metrics when it investigates. Set env vars before launching OpenClaw:

```bash
# Datadog
export DD_API_KEY=your_api_key
export DD_APP_KEY=your_app_key

# OR Sentry
export SENTRY_ORG_SLUG=my-org
export SENTRY_AUTH_TOKEN=sntrys_...
```

Or run the interactive setup once (writes to `~/.tracer/integrations.json`):

```bash
opensre integrations setup
```

### Step 3: Investigate from OpenClaw

In OpenClaw chat, call `run_rca` directly:

```
Run an investigation on: {"title": "High error rate on /api/checkout", "service": "checkout"}
```

OpenSRE queries your connected integrations and returns a structured root-cause analysis in the editor.

### Optional: Connect OpenSRE back to OpenClaw

If you want OpenSRE to call tools exposed by your OpenClaw MCP server during investigations:

```bash
# Recommended: use OpenClaw's stdio MCP bridge
export OPENCLAW_MCP_MODE=stdio
export OPENCLAW_MCP_COMMAND=openclaw
export OPENCLAW_MCP_ARGS="mcp serve"

# Advanced/custom remote MCP transport
# export OPENCLAW_MCP_MODE=streamable-http
# export OPENCLAW_MCP_URL=https://your-custom-openclaw-mcp-endpoint
# export OPENCLAW_MCP_AUTH_TOKEN=your_bearer_token
```

Verify:
```bash
opensre integrations verify --service openclaw
```

Full documentation: `docs/openclaw.mdx`

---

### Demo Alert Payload

A ready-to-paste OpenClaw `run_rca` payload is available at:
`tests/fixtures/openclaw_test_alert.json`

A more realistic full-fidelity alert fixture is also available at:
`tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json`
