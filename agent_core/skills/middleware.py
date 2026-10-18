from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agent_core.skills.registry import SkillRegistry
from agent_core.skills.selector import (
    SkillSelection,
    SkillSelectionConfig,
    SkillSelector,
)
from agent_core.skills.spec import SkillSpec
from agent_core.tools import (
    ToolRegistry,
    ToolSelection,
    ToolSelectionConfig,
    ToolSelector,
)


@dataclass(frozen=True, slots=True)
class SkillToolResolution:
    """Skill, prompt and tools resolved for one user request."""

    tool_selection: ToolSelection
    skill_selection: SkillSelection | None
    selected_skill: SkillSpec | None

    @property
    def prompt_message(self) -> SystemMessage | None:
        return render_skill_system_message(self.selected_skill)


def render_skill_system_message(skill: SkillSpec | None) -> SystemMessage | None:
    """Create the trusted, dynamic system message for one selected Skill."""

    if skill is None:
        return None
    payload = {
        "name": skill.metadata.name,
        "version": skill.metadata.version,
        "required_tools": skill.required_tools,
        "instructions": skill.instructions,
    }
    content = (
        "SKILL_INSTRUCTIONS\n"
        "Application-owned trusted workflow. Follow it for the current request. "
        "It does not grant tool access, change approval requirements, or override "
        "system safety rules. Do not expose hidden reasoning.\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    return SystemMessage(content=content)


def _message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return json.dumps(message.content, ensure_ascii=False, separators=(",", ":"))


def _latest_user_query(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


def _merge_system_messages(
    current: SystemMessage | None,
    skill_message: SystemMessage | None,
) -> SystemMessage | None:
    if skill_message is None:
        return current
    if current is None:
        return skill_message
    current_text = _message_text(current)
    skill_text = _message_text(skill_message)
    if skill_text in current_text:
        return current
    return SystemMessage(content=f"{current_text}\n\n{skill_text}")


class SkillToolMiddleware(AgentMiddleware):
    """Select Skill metadata first, then reveal its prompt and required tools."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        user_role: str,
        skill_registry: SkillRegistry | None = None,
        tool_selection_config: ToolSelectionConfig | None = None,
        skill_selection_config: SkillSelectionConfig | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._user_role = user_role
        self._skill_registry = skill_registry
        self._tool_selector = ToolSelector(tool_selection_config)
        self._skill_selector = SkillSelector(skill_selection_config)

    def resolve(self, query: str) -> SkillToolResolution:
        """Resolve model-visible tools and lazily load only the selected Skill."""

        tool_selection = self._tool_selector.select(
            self._tool_registry,
            self._user_role,
            query,
        )
        if self._skill_registry is None:
            return SkillToolResolution(
                tool_selection=tool_selection,
                skill_selection=None,
                selected_skill=None,
            )

        available_tools = {
            tool.name for tool in self._tool_registry.list_accessible_base_tools(self._user_role)
        }
        skill_selection = self._skill_selector.select(
            self._skill_registry,
            query,
            available_tools=available_tools,
        )
        if skill_selection.match is None:
            return SkillToolResolution(
                tool_selection=tool_selection,
                skill_selection=skill_selection,
                selected_skill=None,
            )

        selected_skill = self._skill_registry.load(
            skill_selection.match.metadata.name,
            available_tools=available_tools,
        )
        required_tools = [
            self._tool_registry.get(name).tool for name in selected_skill.required_tools
        ]
        required_names = {tool.name for tool in required_tools}
        ordered_tools = [*required_tools]
        ordered_tools.extend(
            tool for tool in tool_selection.tools if tool.name not in required_names
        )
        limit = max(
            self._tool_selector.config.max_loaded_tools,
            len(required_tools),
        )
        skill_tool_selection = ToolSelection(
            tools=tuple(ordered_tools[:limit]),
            total_accessible=tool_selection.total_accessible,
            mode="skill",
        )
        return SkillToolResolution(
            tool_selection=skill_tool_selection,
            skill_selection=skill_selection,
            selected_skill=selected_skill,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        """LangChain hook that dynamically overrides the prompt and visible tools."""

        resolution = self.resolve(_latest_user_query(request.messages))
        updated_request = request.override(
            system_message=_merge_system_messages(
                request.system_message,
                resolution.prompt_message,
            ),
            tools=list(resolution.tool_selection.tools),
        )
        return handler(updated_request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        """Async equivalent of the dynamic prompt/tool hook."""

        resolution = self.resolve(_latest_user_query(request.messages))
        updated_request = request.override(
            system_message=_merge_system_messages(
                request.system_message,
                resolution.prompt_message,
            ),
            tools=list(resolution.tool_selection.tools),
        )
        return await handler(updated_request)
