from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import SecretStr

from agent_core.llm.deepseek import (
    LangChainDeepSeekChatModel,
    LLMConfigurationError,
    LLMProviderError,
    to_langchain_messages,
)
from backend import dependencies_llm


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
