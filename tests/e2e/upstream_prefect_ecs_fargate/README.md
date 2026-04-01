# Briefing What Should Be Detected

Orchestrator (Mock DAG Lambda)
A downstream transformation execution fails while processing input data.

Orchestrator Logs (CloudWatch)
The agent retrieves execution logs and stack traces for the failed run.

Input Data Store (S3 – landing)
From the logs, the agent identifies the S3 object used as input and inspects its schema.

Schema Validation
The agent detects a schema mismatch in the S3 input data (missing customer_id).

Data Lineage (S3 metadata)
The agent traces the S3 object origin using metadata and correlation IDs.

Upstream Compute (Ingestion Lambda)
The agent retrieves the Lambda code and recent invocation context responsible for writing the S3 object.

External Dependency (Mock External Vendor API) ---> This is the goal
The agent identifies the external API dependency and inspects the request/response payloads.

