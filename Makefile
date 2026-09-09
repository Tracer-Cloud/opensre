-include .env
export

.PHONY: install build onboard \
	verify-integrations verify-integrations-smoke check-docker \
	grafana-local-up grafana-local-down grafana-local-seed \
	dev docs-dev \
	build-gateway-image deploy-gateway destroy-gateway \
	install-gateway-on-new-server destroy-gateway-on-new-server \
	test test-full test-cov test-scope test-cli-smoke test-grafana \
	clean lint format-check format typecheck vulture \
	check-imports check-cycles check-layers check-imports-strict check-layers-strict check help


ifneq ($(wildcard .venv/bin/python),)
    PYTHON = .venv/bin/python
    PIP = .venv/bin/python -m pip
else ifeq ($(OS),Windows_NT)
    ifneq ($(wildcard .venv/Scripts/python.exe),)
        PYTHON = .venv/Scripts/python.exe
        PIP = .venv/Scripts/python.exe -m pip
    else
        PYTHON = python
        PIP = python -m pip
    endif
else ifneq ($(shell command -v python3 2>/dev/null),)
    PYTHON = python3
    PIP = python3 -m pip
else
    PYTHON = python
    PIP = python -m pip
endif

# PIP_INSTALL_FLAGS = --user --break-system-packages
USER_BASE := $(shell $(PYTHON) -m site --user-base)
USER_BIN := $(if $(filter Windows_NT,$(OS)),$(USER_BASE)/Scripts,$(USER_BASE)/bin)
export PATH := $(if $(wildcard .venv/bin),$(CURDIR)/.venv/bin:,$(if $(wildcard .venv/Scripts),$(CURDIR)/.venv/Scripts:))$(USER_BIN):$(PATH)

PYTHON_SOURCE_PATHS := bootstrap config core gateway integrations infrastructure surfaces tools

# Create venv and install dependencies (requires https://docs.astral.sh/uv/)
install:
	uv sync --frozen --extra dev
	uv run python -m infrastructure.analytics.install

build:
	$(PYTHON) -m build

# Run the local onboarding flow
onboard:
	opensre onboard

verify-integrations:
	uv run opensre integrations verify $(if $(SERVICE),$(SERVICE),) $(if $(SLACK_TEST),--send-slack-test,)

verify-integrations-smoke:
	$(PYTHON) -m pytest -q \
	  tests/integrations/test_verification_registry.py \
	  tests/integrations/test_registry.py

check-docker:
	@command -v docker >/dev/null 2>&1 || { echo "Docker is required for the live local Grafana stack. Install Docker Desktop or another Docker-compatible runtime, then rerun this target."; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "Docker is installed, but the Docker daemon is not running. Start Docker Desktop, OrbStack, or Colima, then rerun this target."; exit 1; }

grafana-local-up: check-docker
	docker compose -f surfaces/cli/wizard/local_grafana_stack/docker-compose.yml up -d

grafana-local-down: check-docker
	docker compose -f surfaces/cli/wizard/local_grafana_stack/docker-compose.yml down

grafana-local-seed:
	$(PYTHON) -m surfaces.cli.wizard.grafana_seed

dev:
	@echo "Run the health app with: uv run uvicorn gateway.web.webapp:app --reload --host 0.0.0.0 --port 8000"

docs-dev:
	cd docs && mint dev


# Gateway deploy (Telegram; AMI + systemd on EC2)
# Step 1 — bake once per code change (launches temp EC2, installs opensre, snapshots AMI):
build-gateway-image:
	$(PYTHON) -m infrastructure.deployment.ec2.telegram_gateway.lifecycle build-server-image

# Step 2 — launch gateway instance from pre-baked AMI (fast):
deploy-gateway:
	$(PYTHON) -m infrastructure.deployment.ec2.telegram_gateway.lifecycle deploy

destroy-gateway:
	$(PYTHON) -m infrastructure.deployment.ec2.telegram_gateway.lifecycle destroy

# Gateway install on a new server (no pre-baked AMI — installs inline via SSM)
install-gateway-on-new-server:
	$(PYTHON) -m infrastructure.deployment.ec2.telegram_gateway.lifecycle install-on-new-server

destroy-gateway-on-new-server:
	$(PYTHON) -m infrastructure.deployment.ec2.telegram_gateway.lifecycle destroy-installed-server

# Run fast tests
test:
	$(PYTHON) -m pytest -v surfaces/cli tests/utils

# Run full test suite (CI/CD)
test-full:
	$(PYTHON) -m pytest -v

# Run tests with coverage (parallel via pytest-xdist).
test-cov:
	$(PYTHON) -m pytest -n auto -v $(addprefix --cov=,$(PYTHON_SOURCE_PATHS)) --cov-report=term-missing

# Run only the tests relevant to files changed on this branch (local use only).
# Pass ARGS=--dry-run to preview the command without executing it.
test-scope:
	$(PYTHON) .github/ci/run_test_scope.py --base main $(ARGS)

# Run the CLI smoke suite against the installed opensre entrypoint.
test-cli-smoke:
	$(PYTHON) -m pytest -v tests/cli/test_smoke.py

# Run Grafana integration tests
test-grafana:
	@echo "Running Grafana integration tests..."
	$(PYTHON) -m pytest tests/e2e/grafana_validation/test_grafana_cloud_queries.py -v

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -maxdepth 1 \( -name '.coverage' -o -name '.coverage.*' \) -delete 2>/dev/null || true
	rm -rf htmlcov/ 2>/dev/null || true

# Lint code
lint:
	$(PYTHON) -m ruff check $(PYTHON_SOURCE_PATHS) tests/

# Check formatting (read-only; CI uses this)
format-check:
	$(PYTHON) -m ruff format --check $(PYTHON_SOURCE_PATHS) tests/

# Format code
format:
	$(PYTHON) -m ruff format $(PYTHON_SOURCE_PATHS) tests/

# Type check
typecheck:
	$(PYTHON) -m mypy $(PYTHON_SOURCE_PATHS)

# Dead-code scan (reads [tool.vulture] from pyproject.toml; advisory only, not in CI)
vulture:
	$(PYTHON) -m vulture

# Import graph: cycles + layering + forbidden direct edges (one command).
check-imports:
	$(PYTHON) .github/ci/check_imports.py

# Deprecated aliases — use ``check-imports`` instead.
check-cycles check-layers: check-imports

# Optional: full transitive layer contracts (when .importlinter.strict exists).
check-imports-strict:
	$(PYTHON) .github/ci/check_imports.py --strict

check-layers-strict: check-imports-strict

# Run all checks (lint + format read-only check + types + imports + full tests; mirrors CI quality gates)
check: lint format-check typecheck check-imports test-full

# Show help
help:
	@echo "Available commands:"
	@echo ""
	@echo "  GATEWAY DEPLOY (systemd, no Docker — gateway only)"
	@echo "  make build-gateway-image - Build a server image with the gateway installed (saves the image id locally)"
	@echo "  make deploy-gateway  - Start a gateway server from the image built above (fast)"
	@echo "  make install-gateway-on-new-server  - Start a plain server and install the gateway on it (no image)"
	@echo "  make destroy-gateway-on-new-server  - Tear down the server created by the command above"
	@echo "  make destroy-gateway - Terminate gateway instance and clean up (set OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1 to also deregister AMI)"
	@echo ""
	@echo "  LOCAL STACKS"
	@echo "  make grafana-local-up - Start the local Grafana + Loki stack"
	@echo "  make grafana-local-seed - Seed failure logs into the local Loki instance"
	@echo "  make verify-integrations - Check local store + .env integrations"
	@echo "  make verify-integrations-smoke - Fast registry/catalog contract tests (CI smoke gate)"
	@echo ""
	@echo "  LOCAL DEVELOPMENT"
	@echo "  make install         - Install dependencies"
	@echo "  make onboard         - Run the OpenSRE onboarding flow"
	@echo "  make docs-dev        - Start the local documentation preview (requires mint CLI)"
	@echo ""
	@echo "  CLI (tab-completable, run 'opensre -h' for full help)"
	@echo "  opensre onboard                    - Interactive setup wizard"
	@echo "  opensre integrations list          - Show configured integrations"
	@echo "  opensre integrations verify        - Verify connectivity"
	@echo ""
	@echo "  TESTING & QUALITY"
	@echo "  make test            - Run fast unit tests"
	@echo "  make test-full       - Run full test suite (CI/CD)"
	@echo "  make test-cov        - Run tests with coverage"
	@echo "  make test-cli-smoke  - Run end-to-end CLI smoke tests"
	@echo "  make test-grafana    - Run Grafana integration tests"
	@echo "  make clean           - Clean up cache files"
	@echo "  make lint            - Lint code with ruff"
	@echo "  make format-check    - Check formatting with ruff (read-only)"
	@echo "  make format          - Format code with ruff"
	@echo "  make typecheck       - Type check with mypy"
	@echo "  make check-imports   - Import cycles, layers, and direct-edge checks"
	@echo "  make check-layers-strict - Full transitive layer contracts (.importlinter.strict)"
	@echo "  make check           - Run all checks"
