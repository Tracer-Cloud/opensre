FROM apache/airflow:slim-3.1.6

USER root

WORKDIR /opt/airflow

RUN pip install --no-cache-dir boto3

COPY ../../pipeline_code/airflow_dag /opt/airflow/dags/airflow_dag

COPY entrypoint.sh /opt/airflow/entrypoint.sh
RUN chmod +x /opt/airflow/entrypoint.sh

EXPOSE 8080

USER airflow

ENTRYPOINT ["/opt/airflow/entrypoint.sh"]
