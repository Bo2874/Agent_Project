from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent_core.context_budget import (
    TOKEN_ESTIMATOR_NAME,
    ContextBlock,
    ContextBudgetError,
    ContextTokenBudget,
    ContextTokenUsage,
    _canonicalize_json,
    _prefix_fingerprint,
    _render_data_section,
    _render_status_section,
    estimate_tokens,
    fit_context_blocks,
)
from agent_core.llm.deepseek import to_langchain_messages, to_model_messages
from agent_core.llm.types import ModelMessage
from agent_core.skills import SkillSpec, render_skill_system_message
from agent_core.status import AgentStatusBlock


@dataclass(frozen=True, slots=True)
class LangChainContextSections:
    system: list[BaseMessage]
    skill: list[BaseMessage]
    memory: list[BaseMessage]
    history: list[BaseMessage]
    history_summary: list[BaseMessage]
    tools: list[dict[str, Any]]
    rag: list[BaseMessage]
    status: list[BaseMessage]


@dataclass(frozen=True, slots=True)
class LangChainPreparedContext:
    messages: list[BaseMessage]
    tools: list[dict[str, Any]]
    usage: ContextTokenUsage
    sections: LangChainContextSections


def _measure_messages(messages: Sequence[BaseMessage]) -> int:
    model_messages = to_model_messages(list(messages))
    return sum(4 + estimate_tokens(message) for message in model_messages)


def _history_turns(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    turns: list[list[BaseMessage]] = []
    for message in messages:
        if isinstance(message, HumanMessage) or not turns:
            turns.append([])
        turns[-1].append(message)
    return turns


def _as_langchain_messages(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    return to_langchain_messages(cast(list[ModelMessage], messages))


class ContextAssemblyMiddleware(AgentMiddleware):
    """Assemble budgeted LangChain messages before each model call."""

    def __init__(
        self,
        budget: ContextTokenBudget,
        *,
        tool_definitions: Sequence[Mapping[str, Any]] = (),
        memory: Sequence[ContextBlock] = (),
        rag: Sequence[ContextBlock] = (),
        history_summaries: Sequence[ContextBlock] = (),
        skill: SkillSpec | None = None,
        available_tool_count: int | None = None,
        tool_selection_mode: str = "all",
    ) -> None:
        self.budget = budget
        self._tool_definitions = tuple(tool_definitions)
        self._memory = tuple(memory)
        self._rag = tuple(rag)
        self._history_summaries = tuple(history_summaries)
        self._skill = skill
        self._available_tool_count = available_tool_count
        self._tool_selection_mode = tool_selection_mode

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        """LangChain hook for agents that use the standard middleware pipeline."""

        del runtime
        prepared = self.prepare(state["messages"])
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *prepared.messages,
            ]
        }

    def prepare(
        self,
        messages: Sequence[BaseMessage],
        *,
        status: AgentStatusBlock | None = None,
        tool_definitions: Sequence[Mapping[str, Any]] | None = None,
        memory: Sequence[ContextBlock] | None = None,
    ) -> LangChainPreparedContext:
        system_messages = [
            deepcopy(message) for message in messages if isinstance(message, SystemMessage)
        ]
        history_messages = [
            deepcopy(message) for message in messages if not isinstance(message, SystemMessage)
        ]
        active_memory = self._memory if memory is None else tuple(memory)
        fitted_memory = fit_context_blocks(
            "memory",
            active_memory,
            self.budget.memory_tokens,
        )
        memory_messages = _as_langchain_messages(_render_data_section("memory", fitted_memory))
        rag_messages = _as_langchain_messages(_render_data_section("rag", self._rag))
        summary_messages = _as_langchain_messages(
            _render_data_section("history_summary", self._history_summaries)
        )
        status_messages = _as_langchain_messages(_render_status_section(status))
        skill_message = render_skill_system_message(self._skill)
        skill_messages = [] if skill_message is None else [skill_message]
        active_tool_definitions = (
            self._tool_definitions if tool_definitions is None else tuple(tool_definitions)
        )
        copied_tools = sorted(
            (_canonicalize_json(tool) for tool in active_tool_definitions),
            key=lambda tool: str(tool.get("function", {}).get("name", "")),
        )

        system_tokens = _measure_messages(system_messages)
        memory_tokens = _measure_messages(memory_messages)
        tool_tokens = estimate_tokens(copied_tools) if copied_tools else 0
        rag_tokens = _measure_messages(rag_messages)
        summary_tokens = _measure_messages(summary_messages)
        status_tokens = _measure_messages(status_messages)
        skill_tokens = _measure_messages(skill_messages)
        fixed_sections = (
            ("System prompt", system_tokens, self.budget.system_tokens),
            ("Memory", memory_tokens, self.budget.memory_tokens),
            ("Tool definitions", tool_tokens, self.budget.tool_tokens),
            ("RAG", rag_tokens, self.budget.rag_tokens),
        )
        for label, used, limit in fixed_sections:
            if used > limit:
                raise ContextBudgetError(f"{label} dùng {used} token, vượt budget {limit}")
        if system_tokens + skill_tokens + status_tokens > self.budget.system_tokens:
            raise ContextBudgetError(
                "System prompt, Skill và status dùng "
                f"{system_tokens + skill_tokens + status_tokens} token, vượt budget "
                f"{self.budget.system_tokens}"
            )
        if summary_tokens > self.budget.history_tokens:
            raise ContextBudgetError(
                f"History summary dùng {summary_tokens} token, vượt budget "
                f"{self.budget.history_tokens}"
            )

        fixed_tokens = (
            system_tokens
            + skill_tokens
            + status_tokens
            + memory_tokens
            + tool_tokens
            + rag_tokens
            + summary_tokens
        )
        turns = _history_turns(history_messages)
        trimmed_messages = 0
        while turns:
            flattened_history = [message for turn in turns for message in turn]
            history_tokens = _measure_messages(flattened_history)
            total_tokens = fixed_tokens + history_tokens
            if (
                history_tokens + summary_tokens <= self.budget.history_tokens
                and total_tokens <= self.budget.max_input_tokens
            ):
                break
            if len(turns) == 1:
                raise ContextBudgetError(
                    "Yêu cầu hiện tại và các tool observation đã vượt context budget"
                )
            trimmed_messages += len(turns[0])
            turns.pop(0)
        else:
            flattened_history = []
            history_tokens = 0
            total_tokens = fixed_tokens

        sections = LangChainContextSections(
            system=system_messages,
            skill=skill_messages,
            memory=memory_messages,
            history=flattened_history,
            history_summary=summary_messages,
            tools=copied_tools,
            rag=rag_messages,
            status=status_messages,
        )
        completed_history = [message for turn in turns[:-1] for message in turn]
        current_turn = turns[-1] if turns else []
        prepared_messages = [
            *sections.system,
            *sections.skill,
            *sections.history_summary,
            *completed_history,
            *sections.memory,
            *sections.rag,
            *sections.status,
            *current_turn,
        ]
        usage = ContextTokenUsage(
            estimator=TOKEN_ESTIMATOR_NAME,
            system_tokens=system_tokens,
            memory_tokens=memory_tokens,
            history_tokens=history_tokens,
            tool_tokens=tool_tokens,
            rag_tokens=rag_tokens,
            total_tokens=total_tokens,
            max_input_tokens=self.budget.max_input_tokens,
            trimmed_messages=trimmed_messages,
            prefix_fingerprint=_prefix_fingerprint(
                to_model_messages(system_messages),
                copied_tools,
            ),
            available_tool_count=(
                len(copied_tools)
                if self._available_tool_count is None
                else self._available_tool_count
            ),
            loaded_tool_count=len(copied_tools),
            tool_selection_mode=self._tool_selection_mode,
            status_tokens=status_tokens,
            summary_tokens=summary_tokens,
            skill_tokens=skill_tokens,
            selected_skill=self._skill.name if self._skill is not None else None,
        )
        return LangChainPreparedContext(
            messages=prepared_messages,
            tools=copied_tools,
            usage=usage,
            sections=sections,
        )
