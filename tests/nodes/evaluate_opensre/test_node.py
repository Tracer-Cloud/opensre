from __future__ import annotations

from unittest.mock import patch

from app.nodes.evaluate_opensre.node import node_opensre_llm_eval


def test_node_opensre_llm_eval_skips_without_rubric() -> None:
    out = node_opensre_llm_eval({"opensre_eval_rubric": "", "opensre_evaluate": True})
    assert out["opensre_llm_eval"]["skipped"] is True


def test_node_opensre_llm_eval_calls_judge() -> None:
    with patch(
        "app.integrations.opensre.llm_eval_judge.run_opensre_llm_judge",
        return_value={"overall_pass": True, "score_0_100": 95},
    ):
        out = node_opensre_llm_eval(
            {
                "opensre_eval_rubric": "must cite latency",
                "root_cause": "latency",
                "evidence": {},
            }
        )
    assert out["opensre_llm_eval"]["overall_pass"] is True
    assert out["opensre_llm_eval"]["score_0_100"] == 95
