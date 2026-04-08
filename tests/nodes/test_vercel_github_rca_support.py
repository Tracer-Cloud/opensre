from __future__ import annotations

from types import SimpleNamespace

from app.nodes.investigate.processing.post_process import build_evidence_summary, merge_evidence
from app.nodes.root_cause_diagnosis.prompt_builder import build_diagnosis_prompt


def test_merge_evidence_maps_vercel_and_github_results() -> None:
    execution_results = {
        "vercel_deployment_logs": SimpleNamespace(
            success=True,
            data={
                "deployment_id": "dpl_123",
                "deployment": {
                    "id": "dpl_123",
                    "state": "ERROR",
                    "error": "Build failed",
                    "meta": {
                        "github_repo": "org/repo",
                        "github_commit_sha": "abc123",
                        "github_commit_ref": "main",
                    },
                },
                "events": [{"id": "evt_1", "text": "Build failed"}],
                "error_events": [{"id": "evt_1", "text": "Build failed"}],
                "runtime_logs": [{"id": "log_1", "message": "Error: cannot resolve import"}],
                "total_events": 1,
                "total_runtime_logs": 1,
            },
        ),
        "search_github_code": SimpleNamespace(
            success=True,
            data={
                "matches": [{"path": "src/app.ts", "matches": ["throw new Error('boom')"]}],
                "query": "Build failed",
                "text": "found 1",
            },
        ),
        "list_github_commits": SimpleNamespace(
            success=True,
            data={
                "commits": [{"sha": "abc123", "message": "fix: update import path"}],
                "text": "1 commit",
            },
        ),
    }

    evidence = merge_evidence({}, execution_results)

    assert evidence["vercel_deployment"]["id"] == "dpl_123"
    assert evidence["vercel_error_events"][0]["text"] == "Build failed"
    assert evidence["github_code_matches"][0]["path"] == "src/app.ts"
    assert evidence["github_commits"][0]["sha"] == "abc123"

    summary = build_evidence_summary(execution_results)
    assert "vercel:1 events" in summary
    assert "github:1 code matches" in summary
    assert "github:1 commits" in summary


def test_build_diagnosis_prompt_includes_vercel_and_github_sections() -> None:
    prompt = build_diagnosis_prompt(
        {
            "problem_md": "# Vercel deployment issue",
            "hypotheses": ["A code change broke the build"],
            "raw_alert": {
                "vercel_log_url": "https://vercel.com/org/proj/logs?selectedLogId=abc",
                "annotations": {"error": "Build failed"},
            },
        },
        {
            "vercel_deployment": {
                "id": "dpl_123",
                "state": "ERROR",
                "error": "Build failed",
                "meta": {
                    "github_repo": "org/repo",
                    "github_commit_sha": "abc123",
                    "github_commit_ref": "main",
                },
            },
            "vercel_error_events": [{"text": "Build failed: cannot resolve import"}],
            "vercel_runtime_logs": [{"message": "Error: cannot resolve import"}],
            "github_code_matches": [
                {"path": "src/app.ts", "matches": ["throw new Error('boom')"]}
            ],
            "github_commits": [{"sha": "abc123", "message": "fix: update import path"}],
        },
    )

    assert "Vercel Deployment Evidence" in prompt
    assert "GitHub Code Evidence" in prompt
    assert "vercel, github" in prompt
