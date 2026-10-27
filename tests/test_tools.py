import asyncio
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx
import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from agent_core.tools import (
    DuplicateToolError,
    ToolContext,
    ToolExecutionPolicy,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    create_context_tool,
)
from agent_core.tools.web_search import (
    SearchWebInput,
    WebSearchConfig,
    WebSearchHandler,
    register_web_search_tool,
)


def web_payload():
    return {
        "provider": "OpenAI",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "Answer ([Python](https://www.python.org/)).",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {"url": "https://www.python.org/", "title": "Python"},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cost": 0.0101,
            "prompt_tokens_details": {"cached_tokens": 40},
            "server_tool_use_details": {"web_search_requests": 1},
        },
    }


def web_transport(payload, *, status=200, native=True):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            assert "Authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "data": {
                        "endpoints": [
                            {
                                "tag": "openai",
                                "native_tools": {"openrouter:web_search": {}} if native else {},
                            }
                        ]
                    }
                },
            )
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(respond), requests


@pytest.mark.parametrize("query", [" ", "bad\x00query", "x" * 4001])
def test_web_search_rejects_invalid_input(query):
    with pytest.raises(ValidationError):
        SearchWebInput(query=query)


async def test_web_search_uses_native_endpoint_and_preserves_citations():
    transport, requests = web_transport(web_payload())
    config = WebSearchConfig(api_key=SecretStr("test-secret-not-public"))
    registry = ToolRegistry(allowlist={"search_web"})
    register_web_search_tool(registry, config, transport=transport)
    entry = registry.get("search_web")
    assert entry.tool.return_direct is True
    assert not entry.policy.requires_approval and entry.policy.max_retries == 0
    assert entry.capability.side_effect == "read_only"
    result = await entry.tool.ainvoke(
        {"query": "  Python release date?  "},
        config={
            "configurable": {
                "tool_context": ToolContext(
                    user_id=uuid4(), conversation_id=uuid4(), user_role="user"
                )
            }
        },
    )
    assert not result.is_error
    assert result.content == web_payload()["choices"][0]["message"]["content"]
    assert result.data["sources"] == [{"url": "https://www.python.org/", "title": "Python"}]
    assert result.usage.total_tokens == 120 and result.usage.cached_input_tokens == 40
    assert result.data["cost_usd"] == 0.0101 and result.data["search_requests"] == 1
    body = json.loads(requests[1].content)
    assert body["provider"] == {"only": ["openai"], "allow_fallbacks": False}
    assert body["tools"][0]["parameters"]["engine"] == "native"
    assert len(body["messages"]) == 2 and "history" not in body
    assert "test-secret-not-public" not in repr(config) + repr(result)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("not_native", "web_search_native_unavailable"),
        ("no_search", "web_search_not_performed"),
        ("other_provider", "web_search_not_performed"),
        ("no_sources", "web_search_missing_sources"),
        ("unsafe_url", "web_search_missing_sources"),
        ("truncated", "web_search_invalid_output"),
        ("bad_data", "web_search_invalid_response"),
    ],
)
async def test_web_search_fails_closed_without_fallback_or_retry(case, expected):
    payload = web_payload()
    if case == "no_search":
        payload["usage"]["server_tool_use_details"]["web_search_requests"] = 0
    if case == "other_provider":
        payload["provider"] = "Azure"
    if case == "no_sources":
        payload["choices"][0]["message"]["annotations"] = []
    if case == "unsafe_url":
        payload["choices"][0]["message"]["annotations"][0]["url_citation"]["url"] = (
            "javascript:alert(1)"
        )
    if case == "truncated":
        payload["choices"][0]["finish_reason"] = "length"
    if case == "bad_data":
        payload["choices"] = "not-a-list"
    transport, requests = web_transport(payload, native=case != "not_native")
    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("test-secret")), transport=transport
    )(
        ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
        {"query": "Question"},
    )
    assert result.is_error and result.error_code == expected
    assert len(requests) == (1 if case == "not_native" else 2)
    if case != "not_native":
        assert result.usage.total_tokens == 120


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
async def test_web_search_sanitizes_errors(status):
    transport, _ = web_transport({"error": {"message": "SECRET provider detail"}}, status=status)
    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("SECRET")), transport=transport
    )(
        ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
        {"query": "Question"},
    )
    assert result.is_error and "SECRET" not in repr(result)


async def test_web_search_deadline_does_not_retry_or_report_success():
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("fake"), timeout_seconds=0.01),
        transport=httpx.MockTransport(respond),
    )(
        ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
        {"query": "Question"},
    )
    assert result.error_code == "web_search_timeout" and result.is_error
    assert len(calls) == 1 and result.data["billing_status"] == "unknown"


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


async def echo_handler(context: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
    return ToolResult.success(
        f"{context.user_id}: {arguments['text']}",
        data={"text": arguments["text"]},
    )


def make_echo_tool() -> StructuredTool:
    return create_context_tool(
        name="echo_text",
        description="Trả lại đoạn văn bản được cung cấp.",
        args_schema=EchoInput,
        coroutine=echo_handler,
    )


async def test_registry_registers_and_returns_langchain_tool() -> None:
    registry = ToolRegistry()
    tool = make_echo_tool()
    registry.register(tool)
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")

    registered = registry.get("echo_text")
    result = await registered.tool.ainvoke(
        {"text": "xin chào"},
        config={"configurable": {"tool_context": context}},
    )

    assert len(registry) == 1
    assert "echo_text" in registry
    assert registry.list_tools() == (tool,)
    assert result == ToolResult.success(f"{context.user_id}: xin chào", data={"text": "xin chào"})


def test_registry_rejects_duplicate_tool_name() -> None:
    registry = ToolRegistry()
    registry.register(make_echo_tool())

    with pytest.raises(DuplicateToolError, match="echo_text"):
        registry.register(make_echo_tool())


def test_registry_reports_unknown_tool() -> None:
    with pytest.raises(ToolNotFoundError, match="missing_tool"):
        ToolRegistry().get("missing_tool")


def test_registry_filters_allowlist_and_role_before_model_sees_tools() -> None:
    registry = ToolRegistry(allowlist={"echo_text"})
    registry.register(make_echo_tool())
    registry.register(
        create_context_tool(
            name="admin_tool",
            description="Chỉ admin được dùng.",
            args_schema={"type": "object", "properties": {}},
            coroutine=echo_handler,
        ),
        policy=ToolExecutionPolicy(allowed_roles=frozenset({"admin"})),
    )

    assert [tool.name for tool in registry.list_accessible_base_tools("user")] == ["echo_text"]
    assert [tool.name for tool in registry.list_accessible_base_tools("admin")] == ["echo_text"]


@pytest.mark.parametrize("name", ["Echo", "1_echo", "echo-text", "echo text"])
def test_tool_factory_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="Tool name"):
        create_context_tool(
            name=name,
            description="Mô tả hợp lệ",
            args_schema=EchoInput,
            coroutine=echo_handler,
        )


def test_tool_factory_requires_object_input_schema() -> None:
    with pytest.raises(ValueError, match="type='object'"):
        create_context_tool(
            name="bad_schema",
            description="Mô tả hợp lệ",
            args_schema={"type": "string"},
            coroutine=echo_handler,
        )


def test_tool_factory_rejects_invalid_json_schema() -> None:
    with pytest.raises(ValueError, match="JSON Schema"):
        create_context_tool(
            name="bad_schema",
            description="Mô tả hợp lệ",
            args_schema={
                "type": "object",
                "properties": {"count": {"type": "not-a-json-schema-type"}},
            },
            coroutine=echo_handler,
        )


def test_registry_requires_pydantic_args_schema() -> None:
    async def raw(**_: Any) -> ToolResult:
        return ToolResult.success("ok")

    tool = StructuredTool(
        name="raw_tool",
        description="Raw schema",
        args_schema={"type": "object", "properties": {}},
        coroutine=raw,
    )

    with pytest.raises(TypeError, match="Pydantic"):
        ToolRegistry().register(tool)


def test_tool_result_distinguishes_success_and_failure() -> None:
    success = ToolResult.success("Đã xử lý", data={"count": 1})
    failure = ToolResult.failure("Không thể xử lý", error_code="tool_failed")

    assert success.is_error is False
    assert success.error_code is None
    assert failure.is_error is True
    assert failure.error_code == "tool_failed"
