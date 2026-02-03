#!/bin/bash
set -e

export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=True
export PYTHONPATH="/opt/airflow/dags:${PYTHONPATH:-}"

echo "Initializing Airflow database..."
airflow db migrate

echo "Ensuring admin user exists..."
airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com \
  --password admin >/dev/null 2>&1 || true

echo "Reserializing DAGs..."
airflow dags reserialize

echo "Listing DAGs..."
airflow dags list

echo "Starting Airflow scheduler and API server..."
airflow dag-processor &
airflow scheduler &
sleep 5
exec airflow api-server
