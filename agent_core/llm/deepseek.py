from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langchain_deepseek import ChatDeepSeek

from agent_core.llm.types import (
    LLMChunk,
    LLMToolCallDelta,
    ModelMessage,
    ModelToolDefinition,
    TokenUsage,
)


class LLMConfigurationError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


def _message_content(message: ModelMessage) -> str | list[str | dict[str, Any]]:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, (str, list)):
        return content
    raise LLMConfigurationError("Nội dung message phải là chuỗi hoặc danh sách content block")


def to_langchain_messages(messages: list[ModelMessage]) -> list[BaseMessage]:
    """Convert the application's persisted message format at the provider boundary."""
    converted: list[BaseMessage] = []
    for message in messages:
        role = message.get("role")
        content = _message_content(message)
        if role == "system":
            converted.append(SystemMessage(content=content))
        elif role == "user":
            converted.append(HumanMessage(content=content))
        elif role == "assistant":
            additional_kwargs: dict[str, Any] = {}
            if tool_calls := message.get("tool_calls"):
                additional_kwargs["tool_calls"] = tool_calls
            converted.append(AIMessage(content=content, additional_kwargs=additional_kwargs))
        elif role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise LLMConfigurationError("Tool message thiếu tool_call_id")
            converted.append(ToolMessage(content=content, tool_call_id=tool_call_id))
        else:
            raise LLMConfigurationError(f"Role message không được hỗ trợ: {role!r}")
    return converted


def to_model_messages(messages: list[BaseMessage]) -> list[ModelMessage]:
    """Convert LangGraph/LangChain messages back to the persisted model contract."""
    converted: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            converted.append({"role": "system", "content": message.content})
        elif isinstance(message, HumanMessage):
            converted.append({"role": "user", "content": message.content})
        elif isinstance(message, AIMessage):
            item: ModelMessage = {
                "role": "assistant",
                "content": message.content or None,
            }
            raw_tool_calls = message.additional_kwargs.get("tool_calls")
            if raw_tool_calls:
                item["tool_calls"] = raw_tool_calls
            elif message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(
                                call.get("args", {}), ensure_ascii=False, default=str
                            ),
                        },
                    }
                    for call in message.tool_calls
                ]
            converted.append(item)
        elif isinstance(message, ToolMessage):
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
        else:
            raise LLMConfigurationError(
                f"Loại LangChain message không được hỗ trợ: {type(message).__name__}"
            )
    return converted


def _token_usage(chunk: AIMessageChunk) -> TokenUsage | None:
    usage = chunk.usage_metadata
    if not usage:
        return None

    prompt_tokens = int(usage.get("input_tokens") or 0)
    details = usage.get("input_token_details") or {}
    cached_tokens = int(details.get("cache_read") or 0)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        cached_input_tokens=cached_tokens,
        cache_miss_input_tokens=max(prompt_tokens - cached_tokens, 0),
    )


def _tool_call_deltas(chunk: AIMessageChunk) -> tuple[LLMToolCallDelta, ...]:
    deltas: list[LLMToolCallDelta] = []
    for position, tool_call in enumerate(chunk.tool_call_chunks):
        raw_index = tool_call.get("index")
        deltas.append(
            LLMToolCallDelta(
                index=raw_index if isinstance(raw_index, int) else position,
                id=tool_call.get("id") or None,
                name=tool_call.get("name") or None,
                arguments=tool_call.get("args") or "",
            )
        )
    return tuple(deltas)


class LangChainDeepSeekChatModel:
    """LangChain-backed DeepSeek model with the application's stable stream contract."""

    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        thinking: bool,
        max_tokens: int,
        chat_model: ChatDeepSeek | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.thinking = thinking
        self.max_tokens = max_tokens
        self._chat_model = chat_model
        if self._chat_model is None and self.api_key:
            self._chat_model = ChatDeepSeek(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                max_tokens=self.max_tokens,
                timeout=(10, 180),
                max_retries=0,
                stream_usage=True,
            )

    async def __aenter__(self) -> LangChainDeepSeekChatModel:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._chat_model is None:
            return
        async_client = getattr(self._chat_model, "root_async_client", None)
        if async_client is not None:
            result = async_client.close()
            if inspect.isawaitable(result):
                await result
        sync_client = getattr(self._chat_model, "root_client", None)
        if sync_client is not None:
            sync_client.close()

    async def stream(
        self,
        messages: list[ModelMessage],
        tools: list[ModelToolDefinition] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        if not self.api_key and self._chat_model is None:
            raise LLMConfigurationError("Chưa cấu hình DEEPSEEK_API_KEY trong file .env")
        if self._chat_model is None:  # pragma: no cover - guarded above
            raise LLMConfigurationError("LangChain chat model chưa được khởi tạo")

        runnable: Runnable[Any, AIMessage] | ChatDeepSeek = self._chat_model
        if tools:
            runnable = self._chat_model.bind_tools(tools, tool_choice="auto")

        extra_body: dict[str, Any] = {
            "thinking": {"type": "enabled" if self.thinking else "disabled"}
        }
        if user_id:
            extra_body["user_id"] = user_id

        try:
            async for raw_chunk in runnable.astream(
                to_langchain_messages(messages),
                stream_usage=True,
                extra_body=extra_body,
            ):
                if not isinstance(raw_chunk, AIMessageChunk):
                    raise LLMProviderError("LangChain trả về stream chunk không hợp lệ")
                metadata: Mapping[str, Any] = raw_chunk.response_metadata
                yield LLMChunk(
                    content=str(raw_chunk.text),
                    usage=_token_usage(raw_chunk),
                    finish_reason=metadata.get("finish_reason"),
                    tool_call_deltas=_tool_call_deltas(raw_chunk),
                )
        except (LLMConfigurationError, LLMProviderError):
            raise
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            prefix = (
                f"DeepSeek trả về HTTP {status_code}" if status_code else "Không thể gọi DeepSeek"
            )
            raise LLMProviderError(f"{prefix}: {str(exc)[:300]}") from exc
