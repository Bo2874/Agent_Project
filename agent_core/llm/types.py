from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cache_miss_input_tokens: int = 0

    @property
    def prompt_cache_hit_rate(self) -> float | None:
        measured_tokens = self.cached_input_tokens + self.cache_miss_input_tokens
        if measured_tokens == 0:
            return None
        return self.cached_input_tokens / measured_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_miss_input_tokens=(self.cache_miss_input_tokens + other.cache_miss_input_tokens),
        )


@dataclass(frozen=True, slots=True)
class LLMToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(slots=True)
class LLMChunk:
    content: str = ""
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    tool_call_deltas: tuple[LLMToolCallDelta, ...] = ()


ModelMessage = dict[str, Any]
ModelToolDefinition = dict[str, Any]


class ChatModel(Protocol):
    provider: str
    model: str

    def stream(
        self,
        messages: list[ModelMessage],
        tools: list[ModelToolDefinition] | None = None,
        user_id: str | None = None,
        *,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMChunk]: ...

    async def aclose(self) -> None: ...
