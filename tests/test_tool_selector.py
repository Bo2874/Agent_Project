from collections.abc import Mapping
from typing import Any

from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    ToolSelectionConfig,
    ToolSelector,
    create_context_tool,
)


async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
    return ToolResult.success("ok")


def register_tool(
    registry: ToolRegistry,
    name: str,
    *,
    keywords: frozenset[str] = frozenset(),
    always_load: bool = False,
    roles: frozenset[str] = frozenset({"user", "admin"}),
) -> None:
    registry.register(
        create_context_tool(
            name=name,
            description=f"Handle {name}",
            args_schema={"type": "object", "properties": {}},
            coroutine=handler,
        ),
        policy=ToolExecutionPolicy(allowed_roles=roles),
        routing=ToolRoutingPolicy(keywords=keywords, always_load=always_load),
    )


def test_small_catalog_loads_all_accessible_tools() -> None:
    registry = ToolRegistry()
    register_tool(registry, "time_tool")
    register_tool(registry, "math_tool")

    selection = ToolSelector(ToolSelectionConfig(lazy_load_threshold=2, max_loaded_tools=1)).select(
        registry, "user", "trò chuyện bình thường"
    )

    assert selection.mode == "all"
    assert selection.total_accessible == 2
    assert [tool.name for tool in selection.tools] == ["math_tool", "time_tool"]


def test_large_catalog_loads_only_relevant_tools_with_accent_folding() -> None:
    registry = ToolRegistry()
    register_tool(registry, "calculate", keywords=frozenset({"tính toán"}))
    register_tool(registry, "server_time", keywords=frozenset({"mấy giờ"}))
    register_tool(registry, "count_text", keywords=frozenset({"đếm từ"}))

    selector = ToolSelector(ToolSelectionConfig(lazy_load_threshold=2, max_loaded_tools=1))
    selection = selector.select(registry, "user", "Tinh toan 12 nhân 4")

    assert selection.mode == "relevant"
    assert selection.total_accessible == 3
    assert [tool.name for tool in selection.tools] == ["calculate"]


def test_selector_applies_allowlist_and_role_before_relevance() -> None:
    registry = ToolRegistry(allowlist={"public_tool", "admin_tool", "always_tool"})
    register_tool(registry, "public_tool", keywords=frozenset({"public"}))
    register_tool(
        registry,
        "admin_tool",
        keywords=frozenset({"admin"}),
        roles=frozenset({"admin"}),
    )
    register_tool(registry, "blocked_tool", keywords=frozenset({"blocked"}))
    register_tool(registry, "always_tool", always_load=True)

    selection = ToolSelector(ToolSelectionConfig(lazy_load_threshold=1, max_loaded_tools=2)).select(
        registry, "user", "admin blocked"
    )

    assert selection.total_accessible == 2
    assert [tool.name for tool in selection.tools] == ["always_tool"]


def test_large_catalog_can_load_no_tool_for_normal_chat() -> None:
    registry = ToolRegistry()
    register_tool(registry, "calculate", keywords=frozenset({"tính"}))
    register_tool(registry, "server_time", keywords=frozenset({"mấy giờ"}))

    selection = ToolSelector(ToolSelectionConfig(lazy_load_threshold=1, max_loaded_tools=1)).select(
        registry, "user", "xin chào, bạn khỏe không"
    )

    assert selection.mode == "relevant"
    assert selection.tools == ()
