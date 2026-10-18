from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from agent_core.skills import SkillRegistry, SkillToolMiddleware
from agent_core.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    ToolSelectionConfig,
    create_context_tool,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def _tool_handler(
    _context: ToolContext,
    _arguments: Mapping[str, Any],
) -> ToolResult:
    return ToolResult.success("ok")


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name, keyword in (
        ("demo__calculate", "calculate"),
        ("demo__count_text", "count"),
        ("demo__server_time", "time"),
        ("demo__temperature", "temperature"),
        ("demo__runtime", "runtime"),
    ):
        registry.register(
            create_context_tool(
                name=name,
                description=f"Tool {name}",
                args_schema={"type": "object", "properties": {}},
                coroutine=_tool_handler,
            ),
            routing=ToolRoutingPolicy(keywords=frozenset({keyword})),
        )
    return registry


def _skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.discover(PROJECT_ROOT / "skills")
    return registry


def test_skill_middleware_progressively_reveals_prompt_and_required_tools() -> None:
    skill_registry = _skill_registry()
    middleware = SkillToolMiddleware(
        tool_registry=_tool_registry(),
        user_role="user",
        skill_registry=skill_registry,
        tool_selection_config=ToolSelectionConfig(
            lazy_load_threshold=2,
            max_loaded_tools=1,
        ),
    )

    unrelated = middleware.resolve("weather tomorrow")

    assert isinstance(middleware, AgentMiddleware)
    assert unrelated.selected_skill is None
    assert unrelated.prompt_message is None
    assert skill_registry.loaded_names == frozenset()

    selected = middleware.resolve("text statistics count words and characters")

    assert selected.selected_skill is not None
    assert selected.selected_skill.name == "text_statistics_report"
    assert skill_registry.loaded_names == frozenset({"text_statistics_report"})
    assert selected.tool_selection.mode == "skill"
    assert selected.tool_selection.loaded_names == {
        "demo__calculate",
        "demo__count_text",
    }
    assert selected.prompt_message is not None
    assert "SKILL_INSTRUCTIONS" in str(selected.prompt_message.content)
    assert "text_statistics_report" in str(selected.prompt_message.content)


def test_skill_middleware_overrides_langchain_model_request_dynamically() -> None:
    middleware = SkillToolMiddleware(
        tool_registry=_tool_registry(),
        user_role="user",
        skill_registry=_skill_registry(),
        tool_selection_config=ToolSelectionConfig(
            lazy_load_threshold=2,
            max_loaded_tools=1,
        ),
    )
    request = ModelRequest(
        model=object(),
        messages=[HumanMessage(content="text statistics count words")],
        system_message=SystemMessage(content="BASE_SYSTEM_PROMPT"),
        tools=[],
    )

    updated = middleware.wrap_model_call(request, lambda value: value)

    assert updated.system_message is not None
    assert "BASE_SYSTEM_PROMPT" in str(updated.system_message.content)
    assert "SKILL_INSTRUCTIONS" in str(updated.system_message.content)
    assert {tool.name for tool in updated.tools} == {
        "demo__calculate",
        "demo__count_text",
    }
    assert request.tools == []
    assert request.system_message is not None
    assert request.system_message.content == "BASE_SYSTEM_PROMPT"
