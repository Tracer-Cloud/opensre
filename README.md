# How To Use This Project

- `make install`
- `make demo` to test the project.

## Quick Start

### 1. Install dependencies

```bash
make install
```

This uses your system `python3` and does not create a virtual environment. On Homebrew-managed Python, this uses `--user --break-system-packages` to satisfy PEP 668.

For more commands refer [Makefile](./Makefile)

### Running RCA reports locally

1. Make sure the JWT_TOKEN is present in the .env file

2. Run `make test-rca` to run all the alerts

3. Run `make test-rca FILE=pipeline_error_in_logs` to run a specific file
