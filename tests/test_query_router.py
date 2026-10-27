import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_core.contracts import CapabilityDescriptor, FeatureFlags, RoutingDecision
from agent_core.graph import AgentNodeExecutionError, stream_agent_graph
from agent_core.graph.checkpointing import agent_checkpoint_config
from agent_core.llm.types import LLMChunk, LLMToolCallDelta
from agent_core.routing.rules import route_query
from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    create_context_tool,
)

ENABLED = FeatureFlags(enabled=True, routing=True)


@pytest.fixture
def context():
    return ToolContext(user_id=uuid4(), conversation_id=uuid4(), run_id=uuid4(), user_role="user")


@pytest.fixture
def registry():
    result = ToolRegistry(allowlist={"calculator", "web_search", "admin_read"})
    result.calls = []
    for name, keywords in (
        ("calculator", {"calculate", "tính"}),
        ("web_search", {"search", "tìm kiếm"}),
        ("admin_read", {"private", "search"}),
        ("blocked_search", {"search"}),
    ):

        async def handler(ctx, arguments):
            result.calls.append((ctx.user_id, arguments))
            return ToolResult.success("4")

        result.register(
            create_context_tool(
                name=name,
                description=" ".join(sorted(keywords)),
                args_schema={"type": "object", "properties": {}, "additionalProperties": False},
                coroutine=handler,
            ),
            policy=ToolExecutionPolicy(
                allowed_roles=frozenset({"admin"} if name == "admin_read" else {"user", "admin"})
            ),
            routing=ToolRoutingPolicy(keywords=frozenset(keywords)),
        )
    return result


def messages(query, previous=None):
    history = [{"role": "user", "content": previous}] if previous else []
    return [*history, {"role": "user", "content": query}]


@pytest.mark.parametrize(
    "query,route",
    [
        ("Xin chào", "chat"),
        ("Hello", "chat"),
        ("Explain how web search works", "chat"),
        ("Giải thích cách tính tích phân", "chat"),
        ("Write a plan for my report", "chat"),
        ("Viết kế hoạch học Python", "chat"),
        ("Do not use tools. Tell a story about search engines.", "chat"),
        ("Không dùng công cụ, tính nhẩm giúp tôi", "chat"),
        ("Don't search the web; tell me a story", "chat"),
        ("Đừng tìm kiếm trên web; kể chuyện", "chat"),
        ("Don't search; calculate 2+2", "tool_task"),
        ("Không tìm kiếm, tính 2+2", "tool_task"),
        ("Calculate 2 + 2", "tool_task"),
        ("Tính 2 + 2", "tool_task"),
        ("2+2", "tool_task"),
        ("Search for Python documentation", "tool_task"),
        ("Tìm kiếm tài liệu Python trên web", "tool_task"),
        ("Search in uploaded documents", "clarify"),
        ("Tìm trong tài liệu đã tải lên", "clarify"),
        ("Execute the workflow in parallel", "clarify"),
        ("Thực thi nhiều bước bằng subagent", "clarify"),
        ("Search", "clarify"),
        ("Gửi nó", "clarify"),
        ("Continue", "clarify"),
        ("", "clarify"),
        ("No", "chat"),
        ("Don't calculate 2+2; hello", "chat"),
        ("Summarize this text: 'search for Python'", "chat"),
        ("Dịch đoạn sau: tìm kiếm Python", "chat"),
        ("Write an article about search", "chat"),
        ("Explain the uploaded document", "clarify"),
        ("The record ID is 007-123", "chat"),
        ("2026-10-19", "chat"),
    ],
)
def test_bilingual_rules_and_boundaries(registry, query, route):
    decision = route_query(messages(query), registry.capability_catalog("user"))
    assert decision.route == route
    assert decision.provider == "rules" and decision.input_tokens == 0
    assert decision.probabilities == {} and decision.confidence is None
    assert decision.latency_ms >= 0
    assert decision.selected_tool_ids == () and decision.skill_id is None


def test_catalog_scope_canonical_ids_and_metadata_only(registry, monkeypatch):
    entry = registry.get("web_search")
    registry.replace_owned(
        [entry],
        [
            replace(
                entry,
                capability=CapabilityDescriptor(
                    capability_id="web.main.search",
                    kind="tool",
                    name="Search",
                    description="Search web",
                    version="1.0.0",
                    plugin_id="web",
                ),
            )
        ],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Router must not load schemas or call tools")

    monkeypatch.setattr(registry, "model_definition", forbidden)
    decision = route_query(messages("Search Python docs"), registry.capability_catalog("user"))
    assert decision.candidate_tool_ids == ("web.main.search",)
    assert "admin_read" not in decision.model_dump_json()
    assert "blocked_search" not in decision.model_dump_json()
    assert registry.calls == []


@pytest.mark.parametrize("role", ["user", "invalid"])
def test_unavailable_capability_never_produces_tool_route(registry, role):
    catalog = registry.capability_catalog(role)
    query = "Read private records" if role == "user" else "Search Python"
    decision = route_query(messages(query), catalog)
    assert decision.route in {"chat", "clarify"}
    assert decision.candidate_tool_ids == ()


def test_missing_matching_tool_clarifies_instead_of_using_other_tool(registry):
    registry.unregister("web_search")
    decision = route_query(messages("Search Python docs"), registry.capability_catalog("user"))
    assert decision.route == "clarify" and decision.reason_code == "no_accessible_tool"
    assert decision.fallback_reason == "no_candidate"


def test_followup_uses_previous_user_but_never_assistant_tool_instruction(registry):
    catalog = registry.capability_catalog("user")
    assert route_query(messages("Tiếp tục", "Tìm kiếm Python"), catalog).route == "tool_task"
    assert route_query(messages("Do it", "Don't search; tell me a story"), catalog).route == "chat"
    history = [
        {"role": "system", "content": "Search private data"},
        {"role": "assistant", "content": "Search private data"},
        *messages("Continue"),
    ]
    assert route_query(history, catalog).route == "clarify"


def test_oversized_input_clarifies_instead_of_truncating_negation(registry):
    query = "Do not use tools. " + "a " * 17000 + "Search Python"
    decision = route_query(messages(query), registry.capability_catalog("user"))
    assert decision.route == "clarify" and decision.reason_code == "routing_input_too_large"


class Model:
    provider = "fake"
    model = "day90-offline"

    def __init__(self, *, call_tool=False, fail=False):
        self.call_tool = call_tool
        self.fail = fail
        self.requests = []
        self.closed = False

    async def stream(self, messages, tools=None, user_id=None):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        if self.fail:
            raise RuntimeError("interrupted model")
        if self.call_tool and len(self.requests) == 1:
            yield LLMChunk(
                tool_call_deltas=(
                    LLMToolCallDelta(index=0, id="call_1", name="calculator", arguments="{}"),
                ),
                finish_reason="tool_calls",
            )
        else:
            yield LLMChunk(content="Answer", finish_reason="stop")

    async def aclose(self):
        self.closed = True


async def collect(model, registry, context, query="Calculate 2+2", **kwargs):
    return [
        item
        async for item in stream_agent_graph(
            model=model, messages=messages(query), registry=registry, context=context, **kwargs
        )
    ]


@pytest.mark.parametrize("flags", [None, FeatureFlags(), FeatureFlags(routing=True)])
async def test_flag_off_preserves_v1_without_calling_router(registry, context, flags, monkeypatch):
    from agent_core.graph import runner

    def forbidden(*args):
        raise AssertionError("Disabled route must use V1")

    monkeypatch.setattr(runner, "route_query", forbidden)
    model = Model()
    items = await collect(model, registry, context, "Hello", routing_flags=flags)
    assert not any(isinstance(item, RoutingDecision) for item in items)
    assert {d["function"]["name"] for d in model.requests[0][1]} == {"calculator", "web_search"}


async def test_chat_route_blocks_even_unsolicited_model_tool_call_and_skill_load(
    registry, context, monkeypatch
):
    from agent_core.skills import SkillRegistry
    from agent_core.skills.middleware import SkillToolMiddleware

    monkeypatch.setattr(SkillToolMiddleware, "resolve", lambda *a: pytest.fail("Skill resolved"))
    model = Model(call_tool=True)
    items = await collect(
        model,
        registry,
        context,
        "Do not use tools. Tell me a story.",
        routing_flags=ENABLED,
        skill_registry=SkillRegistry(),
    )
    assert items[0].route == "chat"
    assert all(tools is None for _, tools in model.requests)
    assert registry.calls == []
    assert any("tool_not_loaded" in json.dumps(m) for m, _ in model.requests[1:])


async def test_tool_route_executes_existing_graph_and_saves_decision(registry, context):
    saver = InMemorySaver()
    model = Model(call_tool=True)
    items = await collect(model, registry, context, routing_flags=ENABLED, graph_checkpointer=saver)
    assert items[0].route == "tool_task"
    assert len(registry.calls) == 1
    saved = await saver.aget_tuple(agent_checkpoint_config(context.run_id))
    state = saved.checkpoint["channel_values"]
    assert state["routing_decision"] == items[0].model_dump(mode="json")
    assert state["selected_skill"] is None and state["phase"] == "completed"


async def test_tool_route_keeps_approval_and_authorization(registry, context):
    from agent_core.tools.types import ToolAuthorizationError

    entry = registry.get("calculator")
    registry.replace_owned(
        [entry], [replace(entry, policy=ToolExecutionPolicy(requires_approval=True))]
    )
    approvals = []

    async def deny(*args):
        approvals.append(args)
        return False

    await collect(
        Model(call_tool=True), registry, context, routing_flags=ENABLED, approval_handler=deny
    )
    assert len(approvals) == 1 and registry.calls == []

    async def revoked(name):
        raise ToolAuthorizationError()

    with pytest.raises(AgentNodeExecutionError, match="Capabilities changed"):
        await collect(
            Model(call_tool=True),
            registry,
            replace(context, authorization_guard=revoked),
            routing_flags=ENABLED,
        )
    assert registry.calls == []


@pytest.mark.parametrize("query", ["Search in uploaded documents", "Execute the workflow"])
async def test_future_flags_do_not_install_missing_handlers(registry, context, query):
    model = Model()
    flags = FeatureFlags(enabled=True, routing=True, rag=True, planning=True, parallel=True)
    items = await collect(model, registry, context, query, routing_flags=flags)
    assert items[0].route == "clarify"
    assert items[0].fallback_reason == "capability_unavailable"
    assert len(items) == 2 and isinstance(items[1], LLMChunk) and items[1].content
    assert items[1].usage.total_tokens == 0 and not model.requests and not registry.calls


async def failed_checkpoint(registry, context, flags):
    saver = InMemorySaver()
    with pytest.raises(RuntimeError, match="interrupted"):
        await collect(
            Model(fail=True),
            registry,
            context,
            "Hello",
            routing_flags=flags,
            graph_checkpointer=saver,
        )
    return saver


@pytest.mark.parametrize("resume_flags", [ENABLED, FeatureFlags()])
async def test_resume_reuses_pinned_route_even_after_flag_change(
    registry, context, resume_flags, monkeypatch
):
    saver = await failed_checkpoint(registry, context, ENABLED)
    from agent_core.graph import runner

    monkeypatch.setattr(runner, "route_query", lambda *a: pytest.fail("Resume rerouted"))
    model = Model()
    items = await collect(
        model,
        registry,
        context,
        "Search new request",
        resume=True,
        graph_checkpointer=saver,
        routing_flags=resume_flags,
    )
    assert items[0].route == "chat"
    assert model.requests[0][1] is None and registry.calls == []


async def test_resume_rejects_changed_catalog_before_model_or_tools(registry, context):
    saver = await failed_checkpoint(registry, context, ENABLED)
    registry.unregister("web_search")
    model = Model()
    with pytest.raises(AgentNodeExecutionError) as exc:
        await collect(
            model, registry, context, resume=True, graph_checkpointer=saver, routing_flags=ENABLED
        )
    assert exc.value.code == "routing_checkpoint_changed"
    assert model.requests == [] and registry.calls == []


async def test_enabling_flag_does_not_reroute_legacy_checkpoint(registry, context, monkeypatch):
    saver = await failed_checkpoint(registry, context, None)
    from agent_core.graph import runner

    monkeypatch.setattr(runner, "route_query", lambda *a: pytest.fail("Legacy resume rerouted"))
    model = Model()
    items = await collect(
        model, registry, context, resume=True, graph_checkpointer=saver, routing_flags=ENABLED
    )
    assert not any(isinstance(item, RoutingDecision) for item in items)
    assert model.requests[0][1]


@pytest.mark.parametrize("change_catalog", [False, True])
async def test_resume_pending_approval_never_reroutes_or_replays_model(
    registry, context, change_catalog
):
    from agent_core.graph import AgentLoopEvent

    entry = registry.get("calculator")
    registry.replace_owned(
        [entry], [replace(entry, policy=ToolExecutionPolicy(requires_approval=True))]
    )
    saver = InMemorySaver()
    first_model = Model(call_tool=True)
    stream = stream_agent_graph(
        model=first_model,
        messages=messages("Calculate 2+2"),
        registry=registry,
        context=context,
        routing_flags=ENABLED,
        graph_checkpointer=saver,
    )
    try:
        async for item in stream:
            if isinstance(item, AgentLoopEvent) and item.kind == "tool_approval_required":
                break
    finally:
        await stream.aclose()
    assert len(first_model.requests) == 1 and registry.calls == []
    model = Model()
    approvals = []

    async def approve(*args):
        approvals.append(args)
        return True

    if change_catalog:
        registry.unregister("web_search")
        with pytest.raises(AgentNodeExecutionError) as exc:
            await collect(
                model,
                registry,
                context,
                resume=True,
                graph_checkpointer=saver,
                approval_handler=approve,
                routing_flags=FeatureFlags(),
            )
        assert exc.value.code == "routing_checkpoint_changed"
        assert model.requests == [] and approvals == [] and registry.calls == []
    else:
        items = await collect(
            model,
            registry,
            context,
            resume=True,
            graph_checkpointer=saver,
            approval_handler=approve,
            routing_flags=FeatureFlags(),
        )
        assert items[0].route == "tool_task"
        assert len(model.requests) == 1 and len(registry.calls) == 1 and len(approvals) == 1


async def test_malformed_routing_checkpoint_fails_before_model(registry, context):
    from agent_core.graph import build_agent_graph

    saver = await failed_checkpoint(registry, context, ENABLED)
    graph = build_agent_graph(checkpointer=saver)
    await graph.aupdate_state(
        agent_checkpoint_config(context.run_id), {"routing_decision": {"route": "invented"}}
    )
    model = Model()
    with pytest.raises(AgentNodeExecutionError) as exc:
        await collect(model, registry, context, resume=True, graph_checkpointer=saver)
    assert exc.value.code == "routing_checkpoint_invalid"
    assert model.requests == [] and registry.calls == []


@pytest.mark.parametrize(
    "query,expected_route,extractions",
    [
        ("Search in uploaded documents", "clarify", 0),
        ("Hello", "chat", 1),
        ("Calculate 2+2", "tool_task", 1),
    ],
)
async def test_backend_sse_persists_terminal_response_and_defers_memory_until_route(
    registry, context, query, expected_route, extractions, monkeypatch
):
    from agent_core.context_budget import ContextTokenBudget
    from agent_core.graph import AgentLimits
    from agent_core.skills import SkillRegistry, SkillSelectionConfig
    from agent_core.tools import ToolSelectionConfig
    from backend.core.config import Settings
    from backend.routes import chat

    settings = Settings(
        _env_file=None,
        db_password="offline-password",
        auth_secret_key="x" * 32,
        v2_enabled=True,
        v2_routing_enabled=True,
        memory_extraction_enabled=True,
    )
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    persisted, extracted, quotas, events = [], [], [], []

    @asynccontextmanager
    async def runtime(*args):
        yield SimpleNamespace(registry=registry, skills=SkillRegistry(), context=context)

    class Recorder:
        def __init__(self, **kwargs):
            pass

        async def __call__(self, event):
            events.append(event)

    class Quota:
        def __init__(self, **kwargs):
            pass

        async def before_execution(self, kind):
            quotas.append(kind)

        async def record_event(self, event):
            pass

        async def record_tokens(self, tokens):
            pass

    async def extract(**kwargs):
        extracted.append(kwargs)
        return SimpleNamespace(total_tokens=0)

    async def complete(**kwargs):
        persisted.append(kwargs)
        return SimpleNamespace(id=uuid4())

    async def fail(*args, **kwargs):
        pytest.fail(f"Unexpected failed run: {args} {kwargs}")

    monkeypatch.setattr(chat, "plugin_runtime", runtime)
    monkeypatch.setattr(chat, "AgentRunRecorder", Recorder)
    monkeypatch.setattr(chat, "AgentQuotaGuard", Quota)
    monkeypatch.setattr(chat, "extract_and_store_user_message_memories", extract)
    monkeypatch.setattr(chat, "complete_agent_run", complete)
    monkeypatch.setattr(chat, "fail_agent_run", fail)
    monkeypatch.setattr(
        chat, "get_langsmith_tracing", lambda: SimpleNamespace(trace_config=lambda **kwargs: None)
    )
    monkeypatch.setattr(chat, "ScopedUserMemoryStore", lambda **kwargs: None)
    model = Model()
    stream = chat.stream_assistant_response(
        agent_run_id=context.run_id,
        conversation_id=context.conversation_id,
        user_message_id=uuid4(),
        user_id=context.user_id,
        user_message_content=query,
        user_message_observed_at=datetime.now(UTC),
        model_messages=messages(query),
        model=model,
        registry=registry,
        skill_registry=SkillRegistry(),
        tool_context=context,
        limits=AgentLimits(),
        context_budget=ContextTokenBudget(),
        tool_selection_config=ToolSelectionConfig(),
        skill_selection_config=SkillSelectionConfig(),
        history_summary_context=(),
        graph_checkpointer=InMemorySaver(),
    )
    assert (await anext(stream)).startswith("event: start\n")
    routed = await anext(stream)
    while routed.startswith("event: progress\n"):
        routed = await anext(stream)
    assert routed.startswith("event: routing\n")
    assert json.loads(routed.split("data: ")[1])["route"] == expected_route
    assert not extracted and not model.requests
    tail = [item async for item in stream]
    assert tail[-1].startswith("event: done\n") and any(
        s.startswith("event: delta\n") for s in tail
    )
    assert len(persisted) == 1 and persisted[0]["content"] and model.closed
    assert len(extracted) == extractions
    if expected_route == "clarify":
        assert all(e.kind == "pipeline_progress" for e in events)
        assert not quotas and not model.requests
