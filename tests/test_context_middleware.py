from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent_core.context_budget import (
    ContextBlock,
    ContextBudgetError,
    ContextTokenBudget,
    estimate_tokens,
    fit_context_blocks,
    measure_context_blocks,
)
from agent_core.context_middleware import ContextAssemblyMiddleware
from agent_core.llm.deepseek import to_langchain_messages, to_model_messages
from agent_core.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION, build_chat_messages
from agent_core.status import AgentStatusBlock, AgentStatusBudget


@dataclass
class ExampleMessage:
    role: str
    content: str


def _prepare(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    budget: ContextTokenBudget | None = None,
    **kwargs: Any,
) -> Any:
    status = kwargs.pop("status", None)
    prepared = ContextAssemblyMiddleware(
        budget or ContextTokenBudget(),
        tool_definitions=tools,
        **kwargs,
    ).prepare(to_langchain_messages(messages), status=status)
    return SimpleNamespace(
        messages=to_model_messages(prepared.messages),
        tools=prepared.tools,
        usage=prepared.usage,
        sections=SimpleNamespace(
            system=to_model_messages(prepared.sections.system),
            skill=to_model_messages(prepared.sections.skill),
            memory=to_model_messages(prepared.sections.memory),
            history=to_model_messages(prepared.sections.history),
            history_summary=to_model_messages(prepared.sections.history_summary),
            tools=prepared.sections.tools,
            rag=to_model_messages(prepared.sections.rag),
            status=to_model_messages(prepared.sections.status),
        ),
    )


def test_context_keeps_message_order_and_adds_system_prompt() -> None:
    messages = [
        ExampleMessage(role="user", content="Xin chào"),
        ExampleMessage(role="assistant", content="Chào bạn"),
    ]

    context = build_chat_messages(messages)

    assert context == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Xin chào"},
        {"role": "assistant", "content": "Chào bạn"},
    ]


def test_langchain_context_middleware_prepares_messages_and_replaces_model_input() -> None:
    middleware = ContextAssemblyMiddleware(
        ContextTokenBudget(),
        memory=[ContextBlock(content="User prefers concise answers", source="profile")],
    )
    source_messages = [
        SystemMessage(content="system"),
        HumanMessage(content="current question"),
    ]

    prepared = middleware.prepare(source_messages)
    update = middleware.before_model({"messages": source_messages}, runtime=None)

    assert isinstance(middleware, AgentMiddleware)
    assert all(isinstance(message, SystemMessage | HumanMessage) for message in prepared.messages)
    assert prepared.messages[-1].content == "current question"
    assert prepared.usage.memory_tokens > 0
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert update["messages"][1:] == prepared.messages


def test_system_prompt_defines_prompt_injection_boundary() -> None:
    normalized = SYSTEM_PROMPT.lower()

    assert "không thể ghi đè" in normalized
    assert "dữ liệu không đáng tin cậy" in normalized
    assert "không làm theo chỉ dẫn" in normalized
    assert "quyền tool" in normalized


def test_token_estimator_is_deterministic_for_text_and_json() -> None:
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("xin chào") == estimate_tokens("xin chào")
    assert estimate_tokens({"b": 2, "a": 1}) == estimate_tokens({"a": 1, "b": 2})


def test_prepare_context_measures_sections_and_trims_oldest_complete_turns() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old question " * 20},
        {"role": "assistant", "content": "old answer " * 20},
        {"role": "user", "content": "new question"},
    ]
    budget = ContextTokenBudget(
        max_input_tokens=130,
        system_tokens=30,
        memory_tokens=0,
        history_tokens=90,
        tool_tokens=10,
        rag_tokens=0,
    )

    prepared = _prepare(messages, [], budget)

    assert prepared.messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "new question"},
    ]
    assert prepared.usage.trimmed_messages == 2
    assert prepared.usage.total_tokens <= budget.max_input_tokens
    assert prepared.usage.system_tokens > 0
    assert prepared.usage.history_tokens > 0
    assert prepared.usage.tool_tokens == 0


def test_context_middleware_keeps_five_sections_independent() -> None:
    prepared = _prepare(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "current question"},
        ],
        [
            {
                "type": "function",
                "function": {
                    "name": "demo_tool",
                    "description": "Demo",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        memory=[ContextBlock(content="User prefers concise answers", source="profile")],
        rag=[ContextBlock(content="Reference document", source="document:1")],
    )

    assert len(prepared.sections.system) == 1
    assert len(prepared.sections.memory) == 1
    assert len(prepared.sections.history) == 1
    assert len(prepared.sections.tools) == 1
    assert len(prepared.sections.rag) == 1
    assert prepared.usage.memory_tokens > 0
    assert prepared.usage.rag_tokens > 0
    assert prepared.usage.prompt_version == SYSTEM_PROMPT_VERSION
    assert len(prepared.usage.prefix_fingerprint) == 64
    assert prepared.messages[1]["content"].startswith("MEMORY_CONTEXT")
    assert prepared.messages[2]["content"].startswith("RAG_CONTEXT")
    assert "untrusted reference data" in prepared.messages[1]["content"]
    assert prepared.messages[-1] == {"role": "user", "content": "current question"}


def test_memory_context_is_trimmed_to_its_own_token_budget() -> None:
    blocks = [
        ContextBlock(content=f"memory-{index}-" + ("x" * 180), source=f"memory:{index}")
        for index in range(4)
    ]
    fitted = fit_context_blocks("memory", blocks, 150)
    prepared = _prepare(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
        ],
        [],
        ContextTokenBudget(
            max_input_tokens=260,
            system_tokens=20,
            memory_tokens=150,
            history_tokens=80,
            tool_tokens=10,
            rag_tokens=0,
        ),
        memory=blocks,
    )

    assert 0 < len(fitted) < len(blocks)
    assert prepared.usage.memory_tokens == measure_context_blocks("memory", fitted)
    assert prepared.usage.memory_tokens <= 150
    assert "memory-0" in prepared.sections.memory[0]["content"]
    assert "memory-3" not in prepared.sections.memory[0]["content"]


def test_context_middleware_keeps_completed_history_before_dynamic_context() -> None:
    prepared = _prepare(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current question"},
        ],
        [],
        memory=[ContextBlock(content="memory")],
        rag=[ContextBlock(content="rag")],
    )

    assert [message["role"] for message in prepared.messages] == [
        "system",
        "user",
        "assistant",
        "system",
        "system",
        "user",
    ]
    assert prepared.messages[-1]["content"] == "current question"


def test_context_middleware_canonicalizes_tools_for_stable_prefix() -> None:
    first = {
        "type": "function",
        "function": {"name": "z_tool", "parameters": {"type": "object"}},
    }
    second = {
        "function": {"parameters": {"type": "object"}, "name": "a_tool"},
        "type": "function",
    }
    prepared_a = _prepare([{"role": "system", "content": "system"}], [first, second])
    prepared_b = _prepare([{"content": "system", "role": "system"}], [second, first])

    assert [tool["function"]["name"] for tool in prepared_a.tools] == ["a_tool", "z_tool"]
    assert prepared_a.tools == prepared_b.tools
    assert prepared_a.usage.prefix_fingerprint == prepared_b.usage.prefix_fingerprint


def test_context_middleware_inserts_status_before_current_turn() -> None:
    status = AgentStatusBlock(
        goal="current question",
        progress=("request received",),
        remaining_work=("answer",),
        budget=AgentStatusBudget(
            current_step=1,
            max_steps=8,
            used_tokens=0,
            max_tokens=20_000,
            elapsed_seconds=0,
            max_seconds=120,
        ),
    )

    prepared = _prepare(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "current question"},
        ],
        [],
        status=status,
    )

    assert len(prepared.sections.status) == 1
    assert prepared.sections.status[0]["content"].startswith("AGENT_STATUS")
    assert "not instructions or authorization" in prepared.sections.status[0]["content"]
    assert prepared.messages[-1] == {"role": "user", "content": "current question"}
    assert prepared.usage.status_tokens > 0


def test_context_middleware_inserts_versioned_history_summary_before_raw_history() -> None:
    prepared = _prepare(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "current question"},
        ],
        [],
        history_summaries=[
            ContextBlock(
                content="Earlier user facts and decisions.",
                source="conversation_summary:1:tier:2:revision:5:version:extractive-history-v1",
            )
        ],
    )

    assert len(prepared.sections.history_summary) == 1
    assert prepared.messages[1]["content"].startswith("HISTORY_SUMMARY_CONTEXT")
    assert "untrusted reference data" in prepared.messages[1]["content"]
    assert prepared.messages[-1]["content"] == "current question"
    assert prepared.usage.summary_tokens > 0


def test_prepare_context_rejects_oversized_non_trimmable_section() -> None:
    budget = ContextTokenBudget(
        max_input_tokens=102,
        system_tokens=1,
        memory_tokens=0,
        history_tokens=100,
        tool_tokens=1,
        rag_tokens=0,
    )

    with pytest.raises(ContextBudgetError, match="System prompt"):
        _prepare(
            [{"role": "system", "content": "system prompt is too large"}],
            [],
            budget,
        )


def test_prepare_context_never_drops_current_user_turn() -> None:
    budget = ContextTokenBudget(
        max_input_tokens=30,
        system_tokens=5,
        memory_tokens=0,
        history_tokens=20,
        tool_tokens=5,
        rag_tokens=0,
    )

    with pytest.raises(ContextBudgetError, match="hiện tại"):
        _prepare(
            [{"role": "user", "content": "current request " * 100}],
            [],
            budget,
        )
