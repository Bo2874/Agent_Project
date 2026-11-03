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
    normalize_web_sources,
    register_web_search_tool,
)


def web_payload():
    return {
        "id": "gen-fixture",
        "model": "openai/gpt-5.4-mini",
        "provider": "OpenAI",
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "query": "Python release"},
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Answer ([Python](https://www.python.org/)).",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://www.python.org/",
                                "title": "Python",
                            }
                        ],
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cost": 0.0101,
            "input_tokens_details": {"cached_tokens": 40},
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
    assert result.content == web_payload()["output"][1]["content"][0]["text"]
    assert result.data["sources"] == [{"url": "https://www.python.org/", "title": "Python"}]
    assert result.usage.total_tokens == 120 and result.usage.cached_input_tokens == 40
    assert result.data["cost_usd"] == 0.0101 and result.data["search_requests"] == 1
    body = json.loads(requests[1].content)
    assert body["provider"] == {"only": ["openai"], "allow_fallbacks": False}
    assert body["tools"] == [{"type": "web_search"}]
    assert requests[1].url.path.endswith("/responses") and "include" not in body
    assert len(body["input"]) == 2 and "history" not in body
    assert body["max_tool_calls"] == 2
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
        payload["output"][1]["content"][0]["annotations"] = []
    if case == "unsafe_url":
        payload["output"][1]["content"][0]["annotations"][0]["url"] = "javascript:alert(1)"
    if case == "truncated":
        payload["status"] = "incomplete"
    if case == "bad_data":
        payload["output"] = "not-a-list"
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
    else:
        assert result.usage.total_tokens == 0


async def test_web_search_missing_usage_keeps_billing_unknown_after_paid_post():
    payload = web_payload()
    payload.pop("usage")
    transport, requests = web_transport(payload)
    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("fake")), transport=transport
    )(
        ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
        {"query": "Question"},
    )
    assert result.error_code == "web_search_missing_usage" and result.usage is None
    assert len(requests) == 2 and result.data["billing_status"] == "unknown"


@pytest.mark.parametrize(
    "annotations",
    [
        None,
        [],
        [
            {"type": "url_citation", "url": "javascript:alert(1)"},
        ],
    ],
)
async def test_web_missing_sources_records_diagnostics_without_leaking_answer_to_model(annotations):
    from agent_core.graph import ToolCallRequest
    from agent_core.graph.runtime import _tool_result_message

    payload = web_payload()
    payload["id"] = "gen-test-diagnostic"
    message = payload["output"][1]["content"][0]
    message["text"] = "UNVERIFIED_WEATHER SECRET"
    message["reasoning_details"] = [{"data": "NEVER_RETAIN_REASONING"}]
    if annotations is None:
        message.pop("annotations")
    else:
        message["annotations"] = annotations
    transport, requests = web_transport(payload)
    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("SECRET")), transport=transport
    )(
        ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
        {"query": "Weather tonight"},
    )
    assert result.error_code == "web_search_missing_sources" and len(requests) == 2
    assert result.data["search_performed"] is True
    assert result.data["source_status"] == "missing" and result.data["retryable"] is False
    diagnostic = result.data["response_diagnostics"]
    assert diagnostic["request_id"] == "gen-test-diagnostic"
    assert diagnostic["annotations_count"] == len(annotations or [])
    assert diagnostic["answer_excerpt"] == "UNVERIFIED_WEATHER [REDACTED]"
    assert "SECRET" not in repr(result) and "NEVER_RETAIN_REASONING" not in repr(result)
    request = ToolCallRequest(id="c1", name="search_web", arguments={}, raw_arguments="{}")
    observation = _tool_result_message(request, result)["content"]
    assert "UNVERIFIED_WEATHER" not in observation and "response_diagnostics" not in observation
    assert "Hãy thử lại" not in result.content


def test_web_source_parser_reports_rejections_and_preserves_canonical_citations():
    from agent_core.tools.web_search import _sources

    annotations = [
        {
            "type": "url_citation",
            "url_citation": {
                "url": "https://www.python.org/",
                "title": "Python",
            },
        }
    ]
    invalid = [
        None,
        {"type": "other"},
        {"type": "url_citation"},
        {
            "type": "url_citation",
            "url_citation": {"url": "https://user:password@example.com"},
        },
    ]
    rejected = {}
    assert _sources([*invalid, *annotations, *annotations], rejected) == [
        {"url": "https://www.python.org/", "title": "Python"},
    ]
    assert rejected == {
        "not_url_citation": 2,
        "citation_not_object": 1,
        "unsafe_or_incomplete_url": 1,
        "duplicate_url": 1,
    }


@pytest.mark.parametrize("cited", [False, True])
async def test_web_search_preserves_retrieval_sources_without_requiring_inline_citations(cited):
    payload = web_payload()
    payload.pop("provider")  # Real Responses API omits this field.
    block = payload["output"][1]["content"][0]
    block["text"] = "A sourced answer without links in the prose."
    if not cited:
        block["annotations"] = []
    payload["output"][0]["action"]["sources"] = [
        {"type": "url", "url": "https://www.python.org/", "title": "Python"},
        {"type": "url", "url": "https://docs.python.org/", "title": "Docs"},
        {"type": "url", "url": "javascript:alert(1)", "title": "Unsafe"},
    ]
    transport, requests = web_transport(payload)
    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("fake")), transport=transport
    )(ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"), {"query": "Python"})
    assert not result.is_error and result.usage.total_tokens == 120
    assert result.data["source_status"] == ("cited" if cited else "retrieved")
    assert result.data["sources"] == [
        {"url": "https://www.python.org/", "title": "Python"},
        {"url": "https://docs.python.org/", "title": "Docs"},
    ]
    assert len(result.data["citations"]) == int(cited) and len(requests) == 2


@pytest.mark.parametrize("case", ["missing_call", "wrong_model", "failed_call", "prose_url"])
async def test_responses_requires_completed_search_and_structured_sources(case):
    payload = web_payload()
    if case == "missing_call":
        payload["output"].pop(0)
    elif case == "wrong_model":
        payload["model"] = "other/model"
    elif case == "failed_call":
        payload["output"][0]["status"] = "failed"
    elif case == "prose_url":
        payload["output"][1]["content"][0]["annotations"] = []
    transport, _ = web_transport(payload)
    result = await WebSearchHandler(
        WebSearchConfig(api_key=SecretStr("fake")), transport=transport
    )(ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"), {"query": "Python"})
    expected = "web_search_missing_sources" if case == "prose_url" else "web_search_not_performed"
    assert result.error_code == expected
    assert result.usage.total_tokens == 120


def test_web_source_normalization_never_accepts_unattributed_feed_or_unsafe_links():
    assert normalize_web_sources(
        [
            {"type": "oai-weather"},
            {"url": "javascript:alert(1)"},
            {"url": "https://user:secret@example.com"},
            {"url": "https://example.com", "title": "Source"},
            {"url": "https://example.com", "title": "Duplicate"},
        ]
    ) == [{"url": "https://example.com", "title": "Source"}]
    # Duplicate records from several calls must not consume the unique-source limit.
    assert (
        len(
            normalize_web_sources(
                [{"url": "https://example.com"}] * 100 + [{"url": "https://example.com/other"}]
            )
        )
        == 2
    )


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
    assert len(calls) == 1 and result.data["billing_status"] == "not_started"
    assert result.usage.total_tokens == 0


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
