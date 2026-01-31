#!/bin/bash
set -e

echo "Initializing Airflow database..."
airflow db init

echo "Ensuring admin user exists..."
airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com \
  --password admin >/dev/null 2>&1 || true

echo "Starting Airflow scheduler and API server..."
airflow dag-processor &
airflow scheduler &
sleep 5
exec airflow api-server
