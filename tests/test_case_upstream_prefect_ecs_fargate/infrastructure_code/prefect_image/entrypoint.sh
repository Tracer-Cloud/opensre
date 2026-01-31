#!/bin/bash
set -e

echo "Starting Prefect server..."
prefect server start --host 0.0.0.0 &
SERVER_PID=$!

echo "Waiting for server to initialize (PID: $SERVER_PID)..."
sleep 20

# Check if server process is still alive
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "ERROR: Server process died unexpectedly"
    exit 1
fi

echo "Server started. Creating work pool..."
prefect work-pool create default-pool --type process 2>/dev/null || true

echo "Starting Prefect worker..."
exec prefect worker start --pool default-pool
