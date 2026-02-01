FROM apache/airflow:slim-3.1.6

USER airflow

WORKDIR /opt/airflow

RUN pip install --no-cache-dir boto3

USER root

COPY ../../pipeline_code/airflow_dag /opt/airflow/dags/airflow_dag
COPY entrypoint.sh /opt/airflow/entrypoint.sh
RUN chmod +x /opt/airflow/entrypoint.sh && chown -R airflow: /opt/airflow/dags/airflow_dag /opt/airflow/entrypoint.sh

EXPOSE 8080

USER airflow

ENTRYPOINT ["/opt/airflow/entrypoint.sh"]
