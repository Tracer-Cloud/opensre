.PHONY: install test demo clean lint format

PYTHON = .venv/bin/python
PIP = .venv/bin/pip

# Create venv and install dependencies
install:
	python3 -m venv .venv
	$(PIP) install -r requirements.txt

# Run the demo
demo:
	$(PYTHON) -m src.main

# Run tests
test:
	$(PYTHON) -m pytest tests/ -v

# Run tests with coverage
test-cov:
	$(PYTHON) -m pytest tests/ -v --cov=src --cov-report=term-missing

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ 2>/dev/null || true

# Lint code
lint:
	ruff check src/ tests/

# Format code
format:
	ruff format src/ tests/

# Type check
typecheck:
	mypy src/

# Run all checks
check: lint typecheck test

# Show help
help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make demo       - Run the demo"
	@echo "  make test       - Run tests"
	@echo "  make test-cov   - Run tests with coverage"
	@echo "  make clean      - Clean up cache files"
	@echo "  make lint       - Lint code with ruff"
	@echo "  make format     - Format code with ruff"
	@echo "  make typecheck  - Type check with mypy"
	@echo "  make check      - Run all checks"

