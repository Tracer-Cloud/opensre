#!/usr/bin/env bash
# Saturate CPU with an unindexed analytics query running in parallel.
python run_bad_query.py --concurrency 32 --query-file expensive_report.sql
