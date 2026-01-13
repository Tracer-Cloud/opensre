"""
Nextflow tools for the investigation agent.

Code #4: Context connectors
Functions that fetch context from Nextflow, demonstrating the agent can see across systems.
"""

from langchain_core.tools import tool

from src.mocks.nextflow import get_nextflow_client


@tool
def get_pipeline_run(pipeline_id: str) -> dict:
    """
    Get the latest run for a Nextflow pipeline.
    
    Use this to find out:
    - If a pipeline ran recently
    - What the overall status of the run was
    - What inputs/outputs were configured
    
    Args:
        pipeline_id: The pipeline identifier (e.g., 'events-etl')
    
    Returns:
        Run details including status, timestamps, input/output paths
    """
    client = get_nextflow_client()
    run = client.get_latest_run(pipeline_id)
    
    if run is None:
        return {
            "pipeline_id": pipeline_id,
            "found": False,
            "message": f"No runs found for pipeline '{pipeline_id}'"
        }
    
    return {
        "pipeline_id": pipeline_id,
        "found": True,
        "run": run,
        "message": f"Found run {run['run_id']} with status {run['status']}"
    }


@tool
def get_step_status(run_id: str) -> dict:
    """
    Get the status of all steps in a Nextflow pipeline run.
    
    Use this to find:
    - Which steps completed successfully
    - Which steps failed
    - Error messages for failed steps
    
    Args:
        run_id: The run identifier (e.g., 'run-20260113-001')
    
    Returns:
        List of steps with their status and any errors
    """
    client = get_nextflow_client()
    steps = client.get_steps(run_id)
    
    if not steps:
        return {
            "run_id": run_id,
            "found": False,
            "steps": [],
            "message": f"No steps found for run '{run_id}'"
        }
    
    # Summarize step statuses
    completed = [s for s in steps if s["status"] == "COMPLETED"]
    failed = [s for s in steps if s["status"] == "FAILED"]
    
    return {
        "run_id": run_id,
        "found": True,
        "steps": steps,
        "summary": {
            "total": len(steps),
            "completed": len(completed),
            "failed": len(failed),
        },
        "failed_steps": [{"name": s["step_name"], "error": s.get("error")} for s in failed],
        "message": f"Found {len(steps)} steps: {len(completed)} completed, {len(failed)} failed"
    }


@tool
def get_step_logs(run_id: str, step_name: str) -> dict:
    """
    Get detailed logs for a specific step in a Nextflow run.
    
    Use this to:
    - Understand why a step failed
    - Find error messages and stack traces
    - Identify root causes
    
    Args:
        run_id: The run identifier
        step_name: The name of the step (e.g., 'finalize', 'transform')
    
    Returns:
        Log content for the step
    """
    client = get_nextflow_client()
    logs = client.get_step_logs(run_id, step_name)
    
    if logs is None:
        return {
            "run_id": run_id,
            "step_name": step_name,
            "found": False,
            "logs": None,
            "message": f"No logs found for step '{step_name}' in run '{run_id}'"
        }
    
    return {
        "run_id": run_id,
        "step_name": step_name,
        "found": True,
        "logs": logs,
        "message": f"Found logs for step '{step_name}' ({len(logs)} characters)"
    }

