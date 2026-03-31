## Tracer Development Reference

## Build and Run commands

- Build `make install`
- Run `opensre`

## Lint & Format

- Lint all: `make lint`
- Fix linting: `ruff check app/ tests/ --fix`
- Type check: `make typecheck`

## Testing

- Test: `make test-cov`
- Test real alerts: `make test-rca`

## Code Style

- Use strict typing, follow DRY principle
- One clear purpose per file (separation of concerns)

### Before Push

1. Clean working tree
2. `make test-cov`
3. `make lint`
4. `make typecheck`
