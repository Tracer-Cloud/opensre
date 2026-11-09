# Persistence

- Keep durable storage inside its owning domain. Use a package-local `storage/`
  package when the domain has multiple persistence concerns.
- Centralize backend connection configuration, transactions, and cleanup in a
  backend module; domain database modules select that policy and apply migrations.
- Store and repository modules own operational queries. Migration modules own
  schema DDL, inspection, upgrade ordering, and required data transformations.
- Keep one migration entry point per datastore. Do not combine SQLite,
  PostgreSQL, JSON, or other backends merely because their APIs look alike.
- Migrations must be idempotent, transactional where supported, and safe under
  concurrent startup.
- Keep one canonical import path and remove old modules in the same change.
- Test supported legacy upgrades, idempotence, concurrency, rollback, and any
  crash-recovery or isolation invariants the stored data requires.
