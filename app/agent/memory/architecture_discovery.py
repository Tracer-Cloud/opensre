"""Dynamic discovery of test case architecture documentation."""

from pathlib import Path

from app.agent.memory.io import get_memories_dir


def discover_architecture_docs(tests_dir: Path | None = None) -> dict[str, str]:
    """Discover all ARCHITECTURE.md files in test case directories.

    Args:
        tests_dir: Path to the tests directory. Defaults to <project_root>/tests.

    Returns:
        Dict mapping test case identifiers to architecture doc paths
    """
    if tests_dir is None:
        tests_dir = get_memories_dir().parent.parent / "tests"

    architecture_docs = {}
    for arch_file in tests_dir.glob("test_case_*/ARCHITECTURE.md"):
        test_case_name = arch_file.parent.name
        relative_path = f"tests/{test_case_name}/ARCHITECTURE.md"
        architecture_docs[test_case_name] = relative_path

    return architecture_docs


def find_architecture_doc_for_pipeline(
    pipeline_name: str, tests_dir: Path | None = None
) -> list[str]:
    """Find architecture documentation matching the pipeline name.

    Matches using orchestrator-specific keywords (prefect, airflow, flink, lambda)
    found in the pipeline name to identify the relevant test case architecture.

    Args:
        pipeline_name: Pipeline name from alert (e.g., "upstream_downstream_pipeline_airflow")
        tests_dir: Path to the tests directory. Defaults to <project_root>/tests.

    Returns:
        List of paths to relevant architecture docs (may be empty)
    """
    if not pipeline_name:
        return []

    architecture_docs = discover_architecture_docs(tests_dir)
    pipeline_lower = pipeline_name.lower()
    seed_paths = []

    # Match by orchestrator/technology keywords
    orchestrator_keywords = ["prefect", "airflow", "flink", "lambda"]

    for keyword in orchestrator_keywords:
        if keyword in pipeline_lower:
            for test_case_name, doc_path in architecture_docs.items():
                if keyword in test_case_name.lower():
                    seed_paths.append(doc_path)
                    break

    return seed_paths
