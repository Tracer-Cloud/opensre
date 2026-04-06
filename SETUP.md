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

This exposes the `run_rca` tool for MCP clients (e.g., OpenClaw).

### OpenClaw MCP Configuration (Example)
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

### Demo Alert Payload

A realistic example alert payload is available at:
`tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json`
