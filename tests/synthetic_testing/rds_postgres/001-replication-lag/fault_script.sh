#!/usr/bin/env bash
# Simulate replication lag by driving a sustained write-heavy workload on the primary.
pgbench -c 128 -j 16 -T 900 -f bulk_updates.sql -d payments
