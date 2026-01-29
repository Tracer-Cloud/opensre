.PHONY: install test demo clean lint format

PYTHON = python3
PIP = python3 -m pip
PIP_INSTALL_FLAGS = --user --break-system-packages
USER_BASE := $(shell $(PYTHON) -m site --user-base)
USER_BIN := $(USER_BASE)/bin
export PATH := $(USER_BIN):$(PATH)

# Create venv and install dependencies
install:
	$(PIP) install $(PIP_INSTALL_FLAGS) -r requirements.txt

# Run the Superfluid test case demo
demo:
	.venv/bin/python -m tests.test_case_superfluid.test_case_superfluid

# Run CloudWatch minimal demo (requires LangGraph Studio running)
cloudwatch-demo:
	.venv/bin/python -m tests.test_case_cloudwatch_demo.run_cloudwatch_demo

# Run the generic CLI (reads from stdin or --input)
run:
	$(PYTHON) -m app.main

dev: 
	langgraph dev
# Run tests
test:
	$(PYTHON) -m pytest -v

# Run tests with coverage
test-cov:
	$(PYTHON) -m pytest -v --cov=app --cov-report=term-missing

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ 2>/dev/null || true

# Lint code
lint:
	ruff check app/ tests/

# Format code
format:
	ruff format app/ tests/

# Type check
typecheck:
	mypy app/

# Run all checks
check: lint typecheck test

# Show help
help:
	@echo "Available commands:"
	@echo "  make install         - Install dependencies"
	@echo "  make demo            - Run the demo"
	@echo "  make cloudwatch-demo - Run CloudWatch minimal demo (requires langgraph dev)"
	@echo "  make test            - Run tests"
	@echo "  make test-cov        - Run tests with coverage"
	@echo "  make clean           - Clean up cache files"
	@echo "  make lint            - Lint code with ruff"
	@echo "  make format          - Format code with ruff"
	@echo "  make typecheck       - Type check with mypy"
	@echo "  make check           - Run all checks"

