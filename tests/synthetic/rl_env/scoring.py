"""Pure scoring logic for evaluating agent diagnoses against an answer key."""

from __future__ import annotations

from typing import Any

from tests.synthetic.rds_postgres.scenario_loader import ScenarioAnswerKey


def score_diagnosis(diagnosis: dict[str, Any], answer: ScenarioAnswerKey) -> tuple[float, dict[str, Any]]:
    """Score a submitted diagnosis against the scenario answer key.

    Returns
    -------
    (reward, info) where reward is in [0.0, 1.0] and info contains
    structured pass/fail details for logging and debugging.

    Scoring rules
    -------------
    - Category mismatch or missing root cause: reward = 0.0
    - Category match: reward = fraction of required_keywords present in the
      submitted root_cause text (1.0 when all keywords matched).
    """
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    actual_category = str(diagnosis.get("root_cause_category") or "").strip()
    root_cause_present = bool(
        root_cause and root_cause.lower() != "unable to determine root cause"
    )

    if not root_cause_present or actual_category != answer.root_cause_category:
        return 0.0, {
            "passed": False,
            "expected_category": answer.root_cause_category,
            "actual_category": actual_category,
            "keyword_score": 0.0,
            "matched_keywords": [],
            "missing_keywords": list(answer.required_keywords),
        }

    normalized = " ".join(root_cause.lower().split())
    matched = [k for k in answer.required_keywords if k.lower() in normalized]
    missing = [k for k in answer.required_keywords if k not in matched]
    keyword_score = len(matched) / len(answer.required_keywords) if answer.required_keywords else 1.0

    return keyword_score, {
        "passed": not missing,
        "expected_category": answer.root_cause_category,
        "actual_category": actual_category,
        "keyword_score": keyword_score,
        "matched_keywords": matched,
        "missing_keywords": missing,
    }
