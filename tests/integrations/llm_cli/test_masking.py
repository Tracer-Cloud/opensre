from __future__ import annotations

import json
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, RootModel

from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
from core.llm.types import ModelType, ToolCall
from integrations.llm_cli.base import CLIInvocation, CLIProbe
from integrations.llm_cli.copilot import CopilotAdapter
from integrations.llm_cli.runner import CLIBackedLLMClient, _StreamedProcessResult

IP = "192.0.2.42"
EMAIL = "privacy-check@example.invalid"
PROMPT = f"Investigate pod privacy-test-7b5b4cbd55-abcde in namespace privacy-test. {IP} {EMAIL}"


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> CopilotAdapter:
    monkeypatch.setenv("OPENSRE_MASK_ENABLED", "true")
    monkeypatch.setenv("OPENSRE_MASK_KINDS", "")
    monkeypatch.delenv("OPENSRE_MASK_EXTRA_REGEX", raising=False)
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.apply.apply_guardrails_to_text", lambda text: text
    )
    result = CopilotAdapter()
    monkeypatch.setattr(result, "_resolve_binary", lambda: "copilot")
    monkeypatch.setattr(
        result,
        "detect",
        lambda: CLIProbe(True, "1.0.0", True, "copilot", "ok"),
    )
    return result


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("stdin", [True, False])
def test_prepared_prompt_masks_identifiers_only_when_enabled(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch, enabled: bool, stdin: bool
) -> None:
    monkeypatch.setenv("OPENSRE_MASK_ENABLED", str(enabled))
    if stdin:

        def build_stdin(**kwargs: object) -> CLIInvocation:
            return CLIInvocation(("mock-cli",), str(kwargs["prompt"]), ".", None, 30)

        monkeypatch.setattr(adapter, "build", build_stdin)

    client = CLIBackedLLMClient(adapter)
    invocation = client._prepare_invocation(
        [{"role": "system", "content": f"Contact {EMAIL}"}, {"role": "user", "content": PROMPT}]
    ).invocation
    outgoing = invocation.stdin if stdin else invocation.argv[2]
    assert outgoing is not None
    if enabled:
        for identifier in (IP, EMAIL, "privacy-test-7b5b4cbd55-abcde", "privacy-test"):
            assert identifier not in outgoing
        for placeholder in ("<IP_ADDRESS_0>", "<EMAIL_0>", "<POD_0>", "<NAMESPACE_0>"):
            assert placeholder in outgoing
    else:
        assert PROMPT in outgoing


def test_agent_restores_tool_arguments_and_masks_followup_context(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_ip = "198.51.100.42"
    run = MagicMock()
    run.side_effect = [
        MagicMock(
            returncode=0,
            stdout=json.dumps(
                {"tool_calls": [{"name": "lookup", "input": {"ip": "<IP_ADDRESS_0>"}}]}
            ),
            stderr="",
        ),
        MagicMock(
            returncode=0,
            stdout="Contact <EMAIL_0> about <IP_ADDRESS_0> and <IP_ADDRESS_1>",
            stderr="",
        ),
    ]
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    client = CLIBackedAgentClient(adapter)
    messages = [{"role": "user", "content": PROMPT}]
    first = client.invoke(messages)
    assert first.tool_calls[0].input == {"ip": IP}

    messages.append(client.build_assistant_message(first.content, first.tool_calls))
    messages.append(
        client.build_tool_result_message(first.tool_calls, [{"ip": new_ip, "email": EMAIL}])
    )
    second = client.invoke(messages)
    assert second.content == f"Contact {EMAIL} about {IP} and {new_ip}"
    for call in run.call_args_list:
        outgoing = call.args[0][2]
        assert IP not in outgoing
        assert EMAIL not in outgoing
        assert "<IP_ADDRESS_0>" in outgoing
        assert "<EMAIL_0>" in outgoing
    followup = run.call_args_list[1].args[0][2]
    assert new_ip not in followup
    assert "<IP_ADDRESS_1>" in followup


def test_shared_client_does_not_restore_another_invocations_placeholders(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="<EMAIL_0>", stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    client = CLIBackedLLMClient(adapter)
    assert client.invoke(EMAIL).content == EMAIL
    assert client.invoke("Unrelated session with no identifiers").content == "<EMAIL_0>"


def test_concurrent_sessions_restore_their_own_identifiers(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2, timeout=10)

    def run(*args: object, **kwargs: object) -> MagicMock:
        barrier.wait()
        return MagicMock(returncode=0, stdout="<EMAIL_0>", stderr="")

    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    client = CLIBackedLLMClient(adapter)
    emails = [EMAIL, "other-session@example.invalid"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(client.invoke, emails))
    assert [response.content for response in responses] == emails


def test_masked_stream_restores_placeholders_split_between_chunks(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "streams_plain_stdout", True)
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="Contact <EMAIL_0>", stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)

    def split_stream(
        *args: object, **kwargs: object
    ) -> Generator[str, None, _StreamedProcessResult]:
        yield "Contact <EMA"
        yield "IL_0>"
        return _StreamedProcessResult(0, "Contact <EMAIL_0>", "", True)

    monkeypatch.setattr(CLIBackedLLMClient, "_run_plain_stdout_stream", split_stream)
    assert "".join(CLIBackedLLMClient(adapter).invoke_stream(EMAIL)) == f"Contact {EMAIL}"


@pytest.mark.parametrize("encoded_arguments", [False, True])
def test_restored_custom_identifier_keeps_tool_json_valid(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch, encoded_arguments: bool
) -> None:
    identifier = 'private"value\\path'
    monkeypatch.setenv("OPENSRE_MASK_EXTRA_REGEX", json.dumps({"custom": r'private"value\\path'}))
    arguments = {"value": "<CUSTOM_0>", "query": '"<CUSTOM_0>"'}
    output = json.dumps(
        {
            "tool_calls": [
                {
                    "name": "lookup",
                    "input": (json.dumps(arguments) if encoded_arguments else arguments),
                }
            ]
        }
    )
    run = MagicMock(
        return_value=MagicMock(
            returncode=0,
            stdout=f"```json\n{output}\n```",
            stderr="",
        )
    )
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    response = CLIBackedAgentClient(adapter).invoke([{"role": "user", "content": identifier}])
    assert response.tool_calls[0].input == {"value": identifier, "query": f'"{identifier}"'}


def test_restoration_preserves_prose_and_json_formatting(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = 'Contact <EMAIL_0>. Example: {"a":1}'
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=output, stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    assert CLIBackedLLMClient(adapter).invoke(EMAIL).content == output.replace("<EMAIL_0>", EMAIL)


@pytest.mark.parametrize("encoded_result", [False, True])
def test_json_tool_results_mask_contextual_identifiers(
    adapter: CopilotAdapter, encoded_result: bool
) -> None:
    result = {
        "namespace": "private-ns",
        "cluster": "private-cluster",
        "service_name": "private-service",
    }
    tool_result = CLIBackedAgentClient.build_tool_result_message(
        [ToolCall(id="lookup-1", name="lookup", input={})],
        [json.dumps(result) if encoded_result else result],
    )
    messages = [
        {
            "role": "user",
            "content": "Investigate namespace private-ns cluster private-cluster service private-service",
        },
        tool_result,
    ]
    prepared = CLIBackedLLMClient(adapter)._prepare_invocation(messages)
    outgoing = prepared.invocation.argv[2]
    for identifier in result.values():
        assert identifier not in outgoing
    for kind in ("NAMESPACE", "CLUSTER", "SERVICE_NAME"):
        assert outgoing.count(f"<{kind}_0>") == 2
    assert prepared.masker.unmask(outgoing) == (
        "=== USER ===\n" + messages[0]["content"] + "\n\n=== USER ===\n" + tool_result["content"]
    )


@pytest.mark.parametrize("fenced", [False, True])
def test_structured_output_restores_top_level_json_string(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch, fenced: bool
) -> None:
    identifier = 'private"value\\path'
    monkeypatch.setenv("OPENSRE_MASK_EXTRA_REGEX", json.dumps({"custom": r'private"value\\path'}))
    output = json.dumps("<CUSTOM_0>")
    if fenced:
        output = f"```json\n{output}\n```"
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=output, stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    response = CLIBackedLLMClient(adapter).with_structured_output(RootModel[str]).invoke(identifier)
    assert response.root == identifier


def test_structured_output_restores_quoted_text_without_extra_escaping(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    class QueryResponse(BaseModel):
        query: str

    identifier = 'private"value\\path'
    monkeypatch.setenv("OPENSRE_MASK_EXTRA_REGEX", json.dumps({"custom": r'private"value\\path'}))
    output = json.dumps({"query": '"<CUSTOM_0>"'})
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=output, stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    response = CLIBackedLLMClient(adapter).with_structured_output(QueryResponse).invoke(identifier)
    assert response.query == f'"{identifier}"'


@pytest.mark.parametrize(
    "output",
    ['"Contact <EMAIL_0>\nfor details"', r'"C:\Users\<EMAIL_0>\logs"'],
)
def test_quoted_non_json_answers_restore_as_plain_text(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=output, stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    response = CLIBackedLLMClient(adapter).invoke(EMAIL)
    assert response.content == output.replace("<EMAIL_0>", EMAIL)


@pytest.mark.parametrize("model_type", [ModelType.REASONING, ModelType.TOOLCALL])
def test_same_quoted_reply_uses_the_requested_response_format(
    adapter: CopilotAdapter, monkeypatch: pytest.MonkeyPatch, model_type: ModelType
) -> None:
    identifier = 'private"value\\path'
    monkeypatch.setenv("OPENSRE_MASK_EXTRA_REGEX", json.dumps({"custom": r'private"value\\path'}))
    output = json.dumps("<CUSTOM_0>")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=output, stderr=""))
    monkeypatch.setattr("integrations.llm_cli.runner.subprocess.run", run)
    client = CLIBackedLLMClient(adapter, model_type=model_type)
    structured = client.with_structured_output(RootModel[str]).invoke(identifier)
    assert structured.root == identifier
    plain = client.invoke(identifier)
    assert plain.content == f'"{identifier}"'
