"""Contracts for GitHub's action-agent routing guidance."""

from integrations.github.action_prompt import github_action_prompt_fragment


def test_star_velocity_routes_to_chat_tool() -> None:
    prompt = github_action_prompt_fragment()

    assert "get_github_star_history" in prompt
    assert "gh api stargazers" in prompt
