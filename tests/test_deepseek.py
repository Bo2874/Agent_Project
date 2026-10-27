import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import SecretStr, ValidationError

from agent_core.context_budget import ContextTokenBudget
from agent_core.contracts.retrieval import RetrievalResult
from agent_core.graph import AgentLimits
from agent_core.llm.deepseek import (
    LangChainDeepSeekChatModel,
    LLMConfigurationError,
    LLMProviderError,
    to_langchain_messages,
)
from agent_core.llm.types import LLMChunk
from agent_core.rag.qa import DocumentOutputError, QueryPlan, parse_json, validate_answer_text
from backend import dependencies_llm
from backend.models import DocumentChunk
from backend.schemas.document import DocumentSearchResponse
from backend.services import document_qa
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
    assert '"vách đá"' in parse_json(output)["answer"]

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


def test_only_query_plan_requires_json_schema() -> None:
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(parse_json('{"query":"Missing mode"}'))
    text = 'Khóa "lạc quan" kiểm tra phiên bản trước khi ghi.\n\nKhông đủ dữ kiện để kết luận thêm.'
    assert validate_answer_text(text) == text


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (" \n", "empty_answer"),
        ("private\x00text", "invalid_answer_text"),
        ("x" * 24001, "answer_too_long"),
    ],
)
def test_answer_text_validation_has_safe_diagnostics(content, code) -> None:
    with pytest.raises(DocumentOutputError) as raised:
        validate_answer_text(content)
    assert raised.value.code == code
    assert "private" not in json.dumps(raised.value.details)
    assert raised.value.excerpt is None


def test_schema_diagnostics_exclude_values_and_untrusted_field_names() -> None:
    with pytest.raises(ValidationError) as raised:
        QueryPlan.model_validate({"query": "x", "mode": "qa", "private_name": "private_value"})
    error = DocumentOutputError.from_schema_error(raised.value)
    assert error.code == "invalid_schema"
    assert error.details["count"] == 1
    assert error.details["issues"] == [{"type": "extra_forbidden", "loc": ["unknown"]}]
    assert "private" not in json.dumps(error.details)


async def test_output_repair_uses_bounded_diagnostics_and_same_evidence_once() -> None:
    payload = {"question": "x", "files": [{"id": "same-file"}]}
    observations = []
    raw = '{"answer":"a "quoted" phrase"}'

    async def invoke(attempt, reason):
        prompt, request = _repair_request("Return JSON.", payload, reason, json_mode=True)
        observations.append((attempt, prompt, request))
        if attempt == 1:
            return parse_json(raw)
        return QueryPlan(query="Valid", mode="qa")

    answer = await _with_output_repair(invoke)
    assert answer.query == "Valid"
    assert len(observations) == 2
    request = observations[1][2]
    assert request["output_validation_error"] == "invalid_json"
    assert request["validation_details"]["column"] > 0
    assert len(request["invalid_output_excerpt"]) <= 160
    assert request["files"] is payload["files"]
    assert "untrusted diagnostic data" in observations[1][1]
    assert "output_validation_error" not in payload


@pytest.mark.parametrize(
    "code", ["invalid_json", "invalid_schema", "empty_answer", "answer_too_long"]
)
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
        "empty_answer": "nội dung",
        "answer_too_long": "độ dài",
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


async def test_answer_text_repair_does_not_request_json_or_source_selection() -> None:
    observations = []

    async def invoke(attempt, reason):
        prompt, data = _repair_request(
            "Answer from supplied data.", {"question": "x"}, reason, json_mode=False
        )
        observations.append((prompt, data))
        return validate_answer_text(" " if attempt == 1 else 'Plain "answer" without citations.')

    assert await _with_output_repair(invoke) == 'Plain "answer" without citations.'
    assert len(observations) == 2
    assert observations[1][1]["output_validation_error"] == "empty_answer"
    assert "ordinary answer text, not JSON" in observations[1][0]
    assert "sources" not in observations[1][1]


@pytest.mark.parametrize(
    "scenario", ["nine", "repair", "summary", "empty", "unanswerable", "model_labels"]
)
async def test_qa_backend_owns_all_supplied_references(monkeypatch, scenario) -> None:
    """Run the real QA graph; provider/search/DB are controlled at their boundaries."""
    hits = (
        [
            RetrievalResult(
                citation_id=f"source.c{index}",
                document_id=UUID(int=100),
                version_id=UUID(int=101),
                chunk_id=UUID(int=index),
                title="Fixture.pdf",
                text=(f"Passage {index}. " + "data " * 300),
                page_start=index,
                page_end=index + 1,
                score=1.0,
                score_kind="fusion",
                retrieval_config_version="fixture-v1",
            )
            for index in range(1, 13 if scenario == "summary" else 10)
        ]
        if scenario != "empty"
        else []
    )
    version = SimpleNamespace(id=UUID(int=101), document_id=UUID(int=100), pipeline_hash="fixture")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, statement):
            if any(item.get("entity") is DocumentChunk for item in statement.column_descriptions):
                return SimpleNamespace(
                    all=lambda: [
                        (
                            SimpleNamespace(
                                id=hit.chunk_id,
                                document_id=hit.document_id,
                                version_id=hit.version_id,
                                text=hit.text,
                                page_start=hit.page_start,
                                page_end=hit.page_end,
                                section=None,
                                start_offset=None,
                                end_offset=None,
                            ),
                            hit.title,
                        )
                        for hit in hits
                    ]
                )
            return SimpleNamespace(all=lambda: [(UUID(int=100), "Fixture.pdf")])

    class Model:
        provider, model = "controlled", "controlled-text-answer"

        def __init__(self):
            self.requests = []

        async def stream(self, messages, **kwargs):
            self.requests.append((messages, kwargs))
            if len(self.requests) == 1:
                assert kwargs["json_mode"] is True
                yield LLMChunk(
                    content=json.dumps(
                        {"query": "Question", "mode": "summary" if scenario == "summary" else "qa"}
                    )
                )
                return
            assert kwargs["json_mode"] is False
            data = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
            assert "allowed_sources" not in data
            if scenario == "repair" and len(self.requests) == 2:
                yield LLMChunk(content=" ")
            elif scenario == "unanswerable":
                yield LLMChunk(content="Tài liệu không đủ thông tin để trả lời.")
            elif scenario == "model_labels":
                yield LLMChunk(content="Unrequested label [S99] does not add a reference.")
            else:
                yield LLMChunk(content='Ordinary "answer" without inline source labels.')

    retrieve = AsyncMock(
        return_value=DocumentSearchResponse(
            query="Question",
            mode="hybrid",
            items=hits,
            retrieval_config_version="fixture-v1",
        )
    )
    monkeypatch.setattr(document_qa, "SessionFactory", Session)
    monkeypatch.setattr(document_qa, "_scope", AsyncMock(return_value=[version]))
    monkeypatch.setattr(document_qa, "_recent_history", AsyncMock(return_value=([], False)))
    monkeypatch.setattr(document_qa, "_save_trace", AsyncMock())
    monkeypatch.setattr(document_qa, "retrieve_document_evidence", retrieve)
    result, model = document_qa.DocumentQAResult(), Model()
    events = [
        event
        async for event in document_qa.stream_document_qa(
            model=model,
            owner=UUID(int=200),
            conversation_id=UUID(int=201),
            message_id=UUID(int=202),
            query="Question",
            run_id=UUID(int=203),
            recorder=AsyncMock(),
            quota_guard=SimpleNamespace(before_execution=AsyncMock(), record_tokens=AsyncMock()),
            limits=AgentLimits(),
            context_budget=ContextTokenBudget(),
            result=result,
        )
    ]
    output = "".join(event.content for event in events if isinstance(event, LLMChunk))
    assert output
    assert result.trace["reference_policy"] == "supplied_context"
    assert result.trace["reference_sources"] == [
        f"S{i}" for i in range(1, len(result.evidence) + 1)
    ]
    assert "cited_sources" not in result.trace
    assert all(
        call["response_format"] == ("json_object" if i == 0 else "text")
        for i, call in enumerate(result.trace["calls"])
    )
    assert not any("citations" in row["id"] for row in result.trace["progress"])
    if scenario == "summary":
        retrieve.assert_not_awaited()
        assert result.trace["coverage"]["processed_chunks"] == len(hits) == 12
        assert result.trace["coverage"]["summary_levels"] >= 1
        assert len(model.requests) >= 4
    else:
        retrieve.assert_awaited_once()
        assert len(model.requests) == (
            1 if scenario == "empty" else 3 if scenario == "repair" else 2
        )
    if scenario == "empty":
        assert result.evidence == []
    else:
        assert [hit.chunk_id for hit in result.evidence] == [hit.chunk_id for hit in hits]
        assert "answerable" not in result.trace
    # Persistence uses the backend evidence list, even for a model abstention or stray label.
    session = SimpleNamespace(add=lambda row: bindings.append(row))
    bindings = []
    await document_qa.bind_document_citations(
        session,
        message=SimpleNamespace(id=UUID(int=204), conversation_id=UUID(int=201)),
        owner=UUID(int=200),
        result=result,
    )
    assert [row.chunk_id for row in bindings] == [hit.chunk_id for hit in result.evidence]
    assert all(
        row.owner_user_id == UUID(int=200) and row.version_id == UUID(int=101) for row in bindings
    )


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
