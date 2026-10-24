from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from agent_core.llm import LangChainDeepSeekChatModel
from agent_core.llm.types import ChatModel
from backend.core.config import get_settings


def create_chat_model(*, max_tokens: int | None = None) -> ChatModel:
    """Create one model instance for FastAPI dependencies and CLI entry points."""
    settings = get_settings()
    api_key = settings.deepseek_api_key
    return LangChainDeepSeekChatModel(
        api_key=api_key.get_secret_value() if api_key else "",
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking=settings.deepseek_thinking,
        max_tokens=max_tokens if max_tokens is not None else settings.deepseek_max_tokens,
    )


async def get_chat_model() -> AsyncIterator[ChatModel]:
    model = create_chat_model()
    try:
        yield model
    finally:
        await model.aclose()


ChatModelDependency = Annotated[ChatModel, Depends(get_chat_model)]
