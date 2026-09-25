"""Repository pipelines explicitly identify their runtime analytics traffic."""

from pathlib import Path

import yaml

from config.constants.analytics import ANALYTICS_CICD_ENV


def test_python_pipelines_declare_the_cicd_marker() -> None:
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    checked: list[str] = []
    for path in workflows.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        runs = [
            str(step.get("run", ""))
            for job in workflow.get("jobs", {}).values()
            for step in job.get("steps", [])
        ]
        if not any("uv run" in run or "uv sync" in run for run in runs):
            continue
        assert workflow.get("env", {}).get(ANALYTICS_CICD_ENV) == "1", path.name
        checked.append(path.name)
    assert {"ci.yml", "release.yml", "installer-canary.yml"} <= set(checked)
