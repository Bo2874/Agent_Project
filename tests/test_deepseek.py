import json
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import SecretStr, ValidationError

from agent_core.llm.deepseek import (
    LangChainDeepSeekChatModel,
    LLMConfigurationError,
    LLMProviderError,
    to_langchain_messages,
)
from agent_core.rag.qa import DocumentOutputError, GroundedAnswer, QueryPlan, parse_json
from backend import dependencies_llm
from backend.services.document_qa import _repair_request, _with_output_repair
from backend.services.documents import DocumentServiceError


class FakeLangChainModel:
    def __init__(
        self,
        chunks: list[AIMessageChunk] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks or []
        self.error = error
        self.bound_tools: list[dict[str, Any]] | None = None
        self.tool_choice: str | None = None
        self.messages: list[Any] = []
        self.stream_kwargs: dict[str, Any] = {}

    def bind_tools(self, tools: list[dict[str, Any]], *, tool_choice: str) -> "FakeLangChainModel":
        self.bound_tools = tools
        self.tool_choice = tool_choice
        return self

    async def astream(self, messages: list[Any], **kwargs: Any) -> Any:
        self.messages = messages
        self.stream_kwargs = kwargs
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk


def make_client(model: FakeLangChainModel, api_key: str = "test-key") -> LangChainDeepSeekChatModel:
    return LangChainDeepSeekChatModel(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        thinking=False,
        max_tokens=512,
        chat_model=model,  # type: ignore[arg-type]
    )


def test_cli_and_fastapi_share_the_same_model_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSettings:
        deepseek_api_key = SecretStr("test-key")
        deepseek_base_url = "https://example.invalid"
        deepseek_model = "deepseek-test"
        deepseek_thinking = False
        deepseek_max_tokens = 512

    monkeypatch.setattr(dependencies_llm, "get_settings", FakeSettings)

    model = dependencies_llm.create_chat_model()

    assert isinstance(model, LangChainDeepSeekChatModel)
    assert model.provider == "deepseek"
    assert model.model == "deepseek-test"


def test_converts_application_messages_to_langchain_messages() -> None:
    raw_tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "echo_text", "arguments": '{"text":"xin chào"}'},
        }
    ]

    messages = to_langchain_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": None, "tool_calls": raw_tool_calls},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
    )

    assert [type(message) for message in messages] == [
        SystemMessage,
        HumanMessage,
        AIMessage,
        ToolMessage,
    ]
    assert messages[2].additional_kwargs["tool_calls"] == raw_tool_calls
    assert isinstance(messages[3], ToolMessage)
    assert messages[3].tool_call_id == "call_1"


async def test_langchain_stream_maps_content_usage_and_request_options() -> None:
    model = FakeLangChainModel(
        [
            AIMessageChunk(content="Xin"),
            AIMessageChunk(
                content=" chào",
                response_metadata={"finish_reason": "stop"},
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                    "input_token_details": {"cache_read": 4},
                },
            ),
        ]
    )
    client = make_client(model)

    chunks = [
        chunk
        async for chunk in client.stream([{"role": "user", "content": "Hi"}], user_id="user-123")
    ]

    assert "".join(chunk.content for chunk in chunks) == "Xin chào"
    assert isinstance(model.messages[0], HumanMessage)
    assert model.stream_kwargs["stream_usage"] is True
    assert model.stream_kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "user_id": "user-123",
    }
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 12
    assert chunks[-1].usage.cached_input_tokens == 4
    assert chunks[-1].usage.cache_miss_input_tokens == 6
    assert chunks[-1].usage.prompt_cache_hit_rate == 0.4


async def test_langchain_stream_binds_tools_and_maps_fragmented_tool_call() -> None:
    model = FakeLangChainModel(
        [
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"index": 0, "id": "call_1", "name": "echo_text", "args": '{"te'}
                ],
            ),
            AIMessageChunk(
                content="",
                response_metadata={"finish_reason": "tool_calls"},
                tool_call_chunks=[
                    {"index": 0, "id": None, "name": None, "args": 'xt":"xin chào"}'}
                ],
            ),
        ]
    )
    client = make_client(model)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo_text",
                "description": "Echo text",
                "parameters": {"type": "object"},
            },
        }
    ]

    chunks = [
        chunk
        async for chunk in client.stream(
            [{"role": "user", "content": "Echo xin chào"}], tools=tools
        )
    ]

    assert model.bound_tools == tools
    assert model.tool_choice == "auto"
    deltas = [delta for chunk in chunks for delta in chunk.tool_call_deltas]
    assert deltas[0].id == "call_1"
    assert deltas[0].name == "echo_text"
    assert "".join(delta.arguments for delta in deltas) == '{"text":"xin chào"}'
    assert chunks[-1].finish_reason == "tool_calls"


async def test_langchain_model_requires_api_key_before_provider_call() -> None:
    client = LangChainDeepSeekChatModel(
        api_key="",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        thinking=False,
        max_tokens=512,
    )

    with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
        _ = [chunk async for chunk in client.stream([{"role": "user", "content": "Hi"}])]


async def test_langchain_provider_error_uses_application_error_contract() -> None:
    client = make_client(FakeLangChainModel(error=RuntimeError("provider unavailable")))

    with pytest.raises(LLMProviderError, match="Không thể gọi DeepSeek"):
        _ = [chunk async for chunk in client.stream([{"role": "user", "content": "Hi"}])]


async def test_json_mode_is_per_call_and_preserves_escaped_stream_content() -> None:
    raw = json.dumps(
        {"answer": 'Cắt ở "vách đá" ngữ nghĩa [S1].', "sources": ["S1"], "answerable": True},
        ensure_ascii=False,
    )
    split = raw.index('\\"') + 1
    model = FakeLangChainModel(
        [AIMessageChunk(content=raw[:split]), AIMessageChunk(content=raw[split:])]
    )
    client = make_client(model)
    chunks = [
        chunk
        async for chunk in client.stream(
            [{"role": "user", "content": "Return JSON"}], json_mode=True
        )
    ]
    assert model.stream_kwargs["response_format"] == {"type": "json_object"}
    assert model.stream_kwargs["stream_usage"] is True
    assert model.bound_tools is None
    output = "".join(chunk.content for chunk in chunks)
    assert output == raw
    answer = GroundedAnswer.model_validate(parse_json(output)).validate_sources({"S1"})
    assert '"vách đá"' in answer.answer

    _ = [chunk async for chunk in client.stream([{"role": "user", "content": "Hi"}])]
    assert "response_format" not in model.stream_kwargs


def test_invalid_json_reports_position_without_persisting_content_in_details() -> None:
    raw = '{"answer":"Cắt ở "vách đá" [S1].","sources":["S1"],"answerable":true}'
    with pytest.raises(DocumentOutputError) as raised:
        parse_json(raw)
    error = raised.value
    assert error.code == "invalid_json"
    assert error.details["message"] == "Expecting ',' delimiter"
    assert error.details["line"] == 1
    assert error.details["column"] == error.details["position"] + 1
    assert '"vách đá"' in error.excerpt
    assert len(error.excerpt) <= 160
    assert "vách đá" not in json.dumps(error.details, ensure_ascii=False)
    assert str(error) == "invalid_json"


@pytest.mark.parametrize("raw", ["", '```json\n{"answer":"x"}\n```', '{"answer":"a "quote""}'])
def test_json_parser_remains_strict(raw: str) -> None:
    with pytest.raises(DocumentOutputError, match="invalid_json"):
        parse_json(raw)


def test_json_mode_still_requires_schema_and_valid_citation_bindings() -> None:
    with pytest.raises(ValidationError):
        GroundedAnswer.model_validate(parse_json('{"answer":"No source"}'))
    with pytest.raises(DocumentOutputError, match="citation_sources"):
        GroundedAnswer(answer="Answer [S99]", sources=["S99"], answerable=True).validate_sources(
            {"S1"}
        )
    with pytest.raises(DocumentOutputError, match="citation_link"):
        GroundedAnswer(
            answer="Answer [S1](https://example.com)", sources=["S1"], answerable=True
        ).validate_sources({"S1"})


def test_schema_diagnostics_exclude_values_and_untrusted_field_names() -> None:
    with pytest.raises(ValidationError) as raised:
        QueryPlan.model_validate({"query": "x", "mode": "qa", "private_name": "private_value"})
    error = DocumentOutputError.from_schema_error(raised.value)
    assert error.code == "invalid_schema"
    assert error.details["count"] == 1
    assert error.details["issues"] == [{"type": "extra_forbidden", "loc": ["unknown"]}]
    assert "private" not in json.dumps(error.details)


async def test_output_repair_uses_bounded_diagnostics_and_same_evidence_once() -> None:
    payload = {"question": "x", "allowed_sources": ["S1"]}
    observations = []
    raw = '{"answer":"a "quoted" phrase"}'

    async def invoke(attempt, reason):
        prompt, request = _repair_request("Return JSON.", payload, reason, has_sources=True)
        observations.append((attempt, prompt, request))
        if attempt == 1:
            return parse_json(raw)
        return GroundedAnswer(answer="Valid [S1]", sources=["S1"], answerable=True)

    answer = await _with_output_repair(invoke)
    assert answer.sources == ["S1"]
    assert len(observations) == 2
    request = observations[1][2]
    assert request["output_validation_error"] == "invalid_json"
    assert request["validation_details"]["column"] > 0
    assert len(request["invalid_output_excerpt"]) <= 160
    assert request["allowed_sources"] is payload["allowed_sources"]
    assert "untrusted diagnostic data" in observations[1][1]
    assert "output_validation_error" not in payload


@pytest.mark.parametrize("code", ["invalid_json", "invalid_schema", "citation_sources"])
async def test_output_repair_stops_after_one_retry_with_specific_message(code: str) -> None:
    calls = []

    async def fail(attempt, reason):
        calls.append(attempt)
        raise DocumentOutputError(code)

    with pytest.raises(DocumentServiceError) as raised:
        await _with_output_repair(fail)
    assert calls == [1, 2]
    assert raised.value.code == "document_qa_invalid_output"
    expected = {
        "invalid_json": "JSON",
        "invalid_schema": "cấu trúc",
        "citation_sources": "Trích dẫn",
    }
    assert expected[code] in str(raised.value)


async def test_output_repair_does_not_retry_provider_errors() -> None:
    calls = []

    async def fail(attempt, reason):
        calls.append(attempt)
        raise LLMProviderError("controlled provider error")

    with pytest.raises(LLMProviderError):
        await _with_output_repair(fail)
    assert calls == [1]


def test_query_plan_keeps_one_question_with_all_requested_aspects() -> None:
    question = "Compare ANNOY and HNSW: memory, incremental updates and use cases."
    plan = QueryPlan.model_validate({"query": question, "mode": "qa"})
    assert plan.model_dump() == {"query": question, "mode": "qa"}
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(
            {"query": question, "mode": "qa", "queries": ["memory", "updates"]}
        )


@pytest.mark.parametrize("query", ["   ", "bad\x00query"])
def test_single_query_plan_rejects_invalid_question(query: str) -> None:
    with pytest.raises(ValidationError):
        QueryPlan(query=query, mode="qa")
