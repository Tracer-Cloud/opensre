# Vulture whitelist - false positives for dataclass/TypedDict fields
# These fields are part of the API contract and accessed dynamically

# TypedDict fields in ReportContext (accessed via .get())
s3_marker_exists  # used in format_slack_message and format_problem_md
tracer_run_status  # used in format_slack_message and format_problem_md
tracer_run_name  # used in format_slack_message and format_problem_md
tracer_pipeline_name  # used in format_slack_message and format_problem_md
tracer_run_cost  # used in format_slack_message and format_problem_md
tracer_max_ram_gb  # used in format_slack_message and format_problem_md
tracer_user_email  # used in format_slack_message and format_problem_md
tracer_team  # used in format_slack_message and format_problem_md
tracer_instance_type  # used in format_slack_message and format_problem_md
tracer_failed_tasks  # used in format_slack_message and format_problem_md
batch_failure_reason  # used in format_slack_message and format_problem_md
batch_failed_jobs  # used in format_problem_md

# Dataclass fields in TracerRunResult (part of API response)
total_tasks  # part of TracerTaskResult dataclass
failed_task_details  # part of TracerTaskResult dataclass

# Dataclass fields in TracerRun (from Tracer API)
max_cpu  # part of TracerRun and TracerTask dataclass

# Dataclass fields in TracerTask (from Tracer API)
tool_id  # part of TracerTask dataclass

# Dataclass fields in AWSBatchJob (from Tracer API)
job_id  # part of AWSBatchJob dataclass

# TypedDict fields in InvestigationState
problem_md  # part of InvestigationState, used in output

# TypedDict fields in Alert schema
message  # part of GrafanaAlert schema
incident_id  # part of Alert schema
affected_system  # part of Alert schema
detected_at  # part of Alert schema
source_url  # part of Alert schema

# Variable in llm.py used for debugging
raw  # stores raw LLM response for debugging

