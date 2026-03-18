## Tracer Development Reference

## Build and Run commands

- Root: `yarn dev` - Run all services in parallel
- Rails: `cd apps/rails && yarn dev` or `bundle exec rails s -p 3001 -b 0.0.0.0`
- Expo: `cd apps/expo && yarn dev`
- NextJS: `cd apps/nextjs && yarn dev`

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
