"""Mock Nextflow client for the demo."""

from typing import Optional
from fixtures.mock_data import (
    NEXTFLOW_RUNS,
    NEXTFLOW_STEPS,
    NEXTFLOW_LOGS,
    NEXTFLOW_RUN_ID,
)


class MockNextflowClient:
    """Mock Nextflow API client that returns predefined data for the demo scenario."""

    def __init__(self):
        self._runs = NEXTFLOW_RUNS
        self._steps = NEXTFLOW_STEPS
        self._logs = NEXTFLOW_LOGS

    def get_run(self, run_id: str) -> Optional[dict]:
        """
        Get details of a specific pipeline run.
        
        Returns:
            Run details including status, timestamps, and paths
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        
        return {
            "run_id": run["run_id"],
            "pipeline_id": run["pipeline_id"],
            "status": run["status"],
            "started_at": run["started_at"].isoformat(),
            "ended_at": run["ended_at"].isoformat() if run["ended_at"] else None,
            "input_path": run["input_path"],
            "output_path": run["output_path"],
        }

    def get_latest_run(self, pipeline_id: str) -> Optional[dict]:
        """Get the latest run for a pipeline."""
        # In the mock, we just return the known run
        for run in self._runs.values():
            if run["pipeline_id"] == pipeline_id:
                return self.get_run(run["run_id"])
        return None

    def get_steps(self, run_id: str) -> list[dict]:
        """
        Get all steps for a pipeline run.
        
        Returns:
            List of steps with name, status, timestamps
        """
        steps = self._steps.get(run_id, [])
        return [
            {
                "step_name": s["step_name"],
                "status": s["status"],
                "started_at": s["started_at"].isoformat(),
                "ended_at": s["ended_at"].isoformat() if s.get("ended_at") else None,
                "error": s.get("error"),
            }
            for s in steps
        ]

    def get_step_logs(self, run_id: str, step_name: str) -> Optional[str]:
        """
        Get logs for a specific step.
        
        Returns:
            Log content as string, or None if not found
        """
        return self._logs.get((run_id, step_name))


# Singleton instance for the demo
_nextflow_client: Optional[MockNextflowClient] = None


def get_nextflow_client() -> MockNextflowClient:
    """Get the mock Nextflow client singleton."""
    global _nextflow_client
    if _nextflow_client is None:
        _nextflow_client = MockNextflowClient()
    return _nextflow_client

