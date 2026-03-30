#!/usr/bin/env bash
# Exhaust connections by leaking client sessions from a misconfigured pool.
python leak_connections.py --workers 500 --hold-open-seconds 1200
