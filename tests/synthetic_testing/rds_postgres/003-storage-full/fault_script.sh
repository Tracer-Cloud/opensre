#!/usr/bin/env bash
# Drive storage exhaustion by backfilling a large audit archive table.
psql "$DATABASE_URL" -f archive_backfill.sql
