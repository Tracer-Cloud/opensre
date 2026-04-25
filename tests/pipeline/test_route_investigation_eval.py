from __future__ import annotations

from unittest.mock import patch

from app.pipeline.routing import route_investigation_loop


def test_route_investigation_loop_goes_to_eval_when_flag_and_rubric() -> None:
    with patch("app.pipeline.routing.should_continue_investigation", return_value="publish"):
        out = route_investigation_loop(
            {
                "opensre_evaluate": True,
                "opensre_eval_rubric": "rule one",
                "available_action_names": ["x"],
            }
        )
    assert out == "opensre_eval"


def test_route_investigation_loop_skips_eval_without_rubric() -> None:
    with patch("app.pipeline.routing.should_continue_investigation", return_value="publish"):
        out = route_investigation_loop(
            {
                "opensre_evaluate": True,
                "opensre_eval_rubric": "",
                "available_action_names": ["x"],
            }
        )
    assert out == "publish"


def test_route_investigation_loop_investigate_takes_precedence() -> None:
    with patch("app.pipeline.routing.should_continue_investigation", return_value="investigate"):
        out = route_investigation_loop(
            {
                "opensre_evaluate": True,
                "opensre_eval_rubric": "rules",
                "available_action_names": ["x"],
            }
        )
    assert out == "investigate"
