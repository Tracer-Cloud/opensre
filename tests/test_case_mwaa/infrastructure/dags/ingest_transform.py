"""
Ingest and Transform DAG for MWAA test case.

This DAG simulates a data pipeline that:
1. Reads data from S3 (ingested by upstream Lambda)
2. Validates the schema
3. Transforms the data
4. Writes results back to S3

For testing failures, the schema validation step will fail
if required fields are missing (simulating upstream schema change).
"""

import json
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# DAG default arguments
default_args = {
    "owner": "tracer-test",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 0,
    "retry_delay": timedelta(minutes=1),
}

# Required schema fields - if any are missing, validation fails
REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]


def read_from_s3(**context):
    """Read data from S3 bucket."""
    import boto3

    s3_key = context["dag_run"].conf.get("s3_key", "ingested/latest/data.json")
    data_bucket = os.environ.get("DATA_BUCKET", "tracer-test-data")

    s3 = boto3.client("s3")

    try:
        response = s3.get_object(Bucket=data_bucket, Key=s3_key)
        data = json.loads(response["Body"].read().decode("utf-8"))
        print(f"Successfully read data from s3://{data_bucket}/{s3_key}")
        print(f"Record count: {len(data) if isinstance(data, list) else 1}")
        return data
    except Exception as e:
        print(f"Error reading from S3: {e}")
        raise


def validate_schema(**context):
    """Validate that all required fields are present in the data."""
    ti = context["ti"]
    data = ti.xcom_pull(task_ids="read_from_s3")

    if not data:
        raise ValueError("No data received from upstream task")

    records = data if isinstance(data, list) else [data]

    missing_fields = set()
    for i, record in enumerate(records):
        for field in REQUIRED_FIELDS:
            if field not in record:
                missing_fields.add(field)
                print(f"Record {i}: Missing required field '{field}'")

    if missing_fields:
        error_msg = (
            f"Schema validation failed: Missing required fields {list(missing_fields)}. "
            f"Expected fields: {REQUIRED_FIELDS}. "
            f"This may indicate an upstream schema change in the data source."
        )
        print(error_msg)
        raise ValueError(error_msg)

    print(f"Schema validation passed for {len(records)} records")
    return records


def transform_data(**context):
    """Transform the validated data."""
    ti = context["ti"]
    records = ti.xcom_pull(task_ids="validate_schema")

    if not records:
        raise ValueError("No validated data to transform")

    transformed = []
    for record in records:
        transformed_record = {
            "customer_id": record["customer_id"],
            "order_id": record["order_id"],
            "amount_cents": int(float(record["amount"]) * 100),
            "processed_at": datetime.utcnow().isoformat(),
            "original_timestamp": record["timestamp"],
        }
        transformed.append(transformed_record)

    print(f"Transformed {len(transformed)} records")
    return transformed


def write_to_s3(**context):
    """Write transformed data back to S3."""
    import boto3

    ti = context["ti"]
    transformed = ti.xcom_pull(task_ids="transform_data")

    if not transformed:
        raise ValueError("No transformed data to write")

    data_bucket = os.environ.get("DATA_BUCKET", "tracer-test-data")
    execution_date = context["execution_date"].isoformat()
    output_key = f"processed/{execution_date}/data.json"

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=data_bucket,
        Key=output_key,
        Body=json.dumps(transformed, indent=2),
        ContentType="application/json",
    )

    print(f"Successfully wrote {len(transformed)} records to s3://{data_bucket}/{output_key}")
    return {"output_key": output_key, "record_count": len(transformed)}


# Define the DAG
with DAG(
    dag_id="ingest_transform",
    default_args=default_args,
    description="Ingest data from S3, validate schema, transform, and write back",
    schedule_interval=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["tracer-test", "upstream-downstream"],
) as dag:

    read_task = PythonOperator(
        task_id="read_from_s3",
        python_callable=read_from_s3,
        provide_context=True,
    )

    validate_task = PythonOperator(
        task_id="validate_schema",
        python_callable=validate_schema,
        provide_context=True,
    )

    transform_task = PythonOperator(
        task_id="transform_data",
        python_callable=transform_data,
        provide_context=True,
    )

    write_task = PythonOperator(
        task_id="write_to_s3",
        python_callable=write_to_s3,
        provide_context=True,
    )

    read_task >> validate_task >> transform_task >> write_task
