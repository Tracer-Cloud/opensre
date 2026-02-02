FROM apache/airflow:slim-3.1.6

USER airflow

WORKDIR /opt/airflow

COPY ../../pipeline_code/airflow_dag/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

USER root

COPY ../../shared/telemetry/tracer_telemetry /opt/airflow/dags/tracer_telemetry
COPY ../../pipeline_code/airflow_dag /opt/airflow/dags/airflow_dag
COPY entrypoint.sh /opt/airflow/entrypoint.sh
RUN chmod +x /opt/airflow/entrypoint.sh && chown -R airflow: /opt/airflow/dags/airflow_dag /opt/airflow/dags/tracer_telemetry /opt/airflow/entrypoint.sh

EXPOSE 8080

USER airflow

ENTRYPOINT ["/opt/airflow/entrypoint.sh"]
