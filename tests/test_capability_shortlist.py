import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from agent_core.context_budget import ContextBudgetError
from agent_core.contracts import CapabilityDescriptor, FeatureFlags
from agent_core.graph import (
    AgentContextLimitError,
    AgentLoopEvent,
    AgentNodeExecutionError,
    stream_agent_graph,
)
from agent_core.graph.checkpointing import agent_checkpoint_config
from agent_core.llm.types import LLMChunk, LLMToolCallDelta
from agent_core.routing.shortlist import (
    CapabilityChoice,
    SelectionPolicyError,
    ShortlistConfig,
    ShortlistRouter,
)
from agent_core.routing.types import DecisionProviderError, DecisionResult
from agent_core.skills import SkillRegistry
from agent_core.skills.spec import SkillMetadata, SkillSpec
from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    ToolSelectionConfig,
    create_context_tool,
)
from agent_core.tools.types import ToolAuthorizationError

ENABLED = FeatureFlags(enabled=True, routing=True)


class ScriptedDecisionProvider:
    """Policy fixture at the project Protocol; does not mock a Jev SDK/client."""

    def __init__(self, *answers, effect=None):
        self.answers, self.effect, self.requests = list(answers), effect, []

    async def decide(self, request):
        self.requests.append(request)
        if self.effect:
            self.effect()
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        option = next(o for o in request.options if o.id == answer or answer in o.description)
        return DecisionResult(
            selected_id=option.id,
            probabilities={o.id: float(o.id == option.id) for o in request.options},
        )


@pytest.fixture
def context():
    return ToolContext(user_id=uuid4(), conversation_id=uuid4(), run_id=uuid4(), user_role="user")


@pytest.fixture
def registry():
    registry = ToolRegistry(allowlist={"calculator", "read_data", "admin_read"})
    registry.calls = []
    for name in ("calculator", "read_data", "admin_read", "blocked"):

        async def handle(ctx, args, binding=name):
            registry.calls.append(binding)
            return ToolResult.success(
                "private result never sent to DecisionProvider", data={"secret": 7}
            )

        registry.register(
            create_context_tool(
                name=name,
                description=f"Calculate or read data using {name}",
                args_schema={"type": "object", "properties": {}, "additionalProperties": False},
                coroutine=handle,
            ),
            policy=ToolExecutionPolicy(
                allowed_roles=frozenset({"admin"} if name == "admin_read" else {"user", "admin"})
            ),
            routing=ToolRoutingPolicy(keywords=frozenset({"calculate", "tính", name})),
        )
    return registry


def add_skill(skills, name="calculation_report", dependencies=("calculator",), *, approval=False):
    metadata = SkillMetadata(
        name=name, version="1.0.0", description="Calculate report", required_tools=dependencies
    )
    calls = {
        "load": 0,
        "metadata": 0,
        "validate": 0,
        "revoked": False,
        "instructions": "Trusted Skill instructions",
    }

    def load():
        calls["load"] += 1
        return SkillSpec(metadata, calls["instructions"], requires_approval=approval)

    async def validate():
        calls["validate"] += 1
        if calls["revoked"]:
            raise ToolAuthorizationError()

    async def validate_metadata():
        calls["metadata"] += 1
        if calls["revoked"]:
            raise ToolAuthorizationError()

    skills.register_loader(
        metadata,
        load,
        validator=validate,
        metadata_validator=validate_metadata,
        capability=CapabilityDescriptor(
            capability_id=f"fixture.skill.{name}",
            kind="skill",
            plugin_id="fixture",
            name=name,
            description="Calculate report",
            version="1.0.0",
        ),
    )
    return calls


async def test_metadata_choice_loads_only_selected_tool_schema(registry, context, monkeypatch):
    provider = ScriptedDecisionProvider("Tool: calculator")
    router = ShortlistRouter(provider, registry, context)
    original, loaded = registry.model_definition, []

    def schema(name):
        loaded.append(name)
        return original(name)

    monkeypatch.setattr(registry, "model_definition", schema)
    choice = await router.choose("Calculate 2+2", step_number=1)
    assert loaded == [] and registry.calls == []
    resolution = await router.resolve(choice)
    assert loaded == ["calculator"]
    assert resolution.tool_selection.loaded_names == {"calculator"}
    text = provider.requests[0].model_dump_json()
    assert "admin_read" not in text and "blocked" not in text
    assert "parameters" not in text and "instructions.md" not in text


async def test_skill_instructions_lazy_and_required_tools_atomic(registry, context):
    skills = SkillRegistry()
    chosen = add_skill(skills)
    other = add_skill(skills, "another_report", ("read_data",))
    unavailable = add_skill(skills, "missing_report", ("blocked",))
    router = ShortlistRouter(
        ScriptedDecisionProvider("Skill: calculation_report"), registry, context, skills
    )
    choice = await router.choose("Calculate report", step_number=1)
    assert chosen["load"] == other["load"] == unavailable["load"] == 0
    assert other["validate"] == 0 and other["metadata"] > 0
    resolution = await router.resolve(choice)
    assert resolution.selected_skill.name == "calculation_report" and chosen["load"] == 1
    assert resolution.tool_selection.loaded_names == {"calculator"}
    assert other["load"] == 0
    budgeted = ShortlistRouter(
        ScriptedDecisionProvider(),
        registry,
        context,
        skills,
        tool_config=ToolSelectionConfig(schema_token_budget=0),
    )
    with pytest.raises(ContextBudgetError):
        # Keep the new policy fingerprint; this checks schema fit, not old policy resume.
        await budgeted.resolve(
            choice.model_copy(update={"catalog_version": budgeted.catalog_version})
        )


async def test_policy_budget_change_invalidates_saved_choice(registry, context):
    original = ShortlistRouter(ScriptedDecisionProvider("Tool: calculator"), registry, context)
    choice = await original.choose("Calculate", step_number=1)
    changed = ShortlistRouter(
        ScriptedDecisionProvider(), registry, context, config=ShortlistConfig(page_size=1)
    )
    with pytest.raises(SelectionPolicyError) as exc:
        await changed.resolve(choice)
    assert exc.value.code == "selection_checkpoint_changed"


@pytest.mark.parametrize(
    "answers,reason,pages",
    [
        (("none",), "none", 1),
        (("search_more", "none"), "none", 2),
        (("search_more", "search_more"), "search_exhausted", 2),
    ],
)
async def test_none_and_search_more_are_bounded_and_do_not_load_schema(
    registry, context, answers, reason, pages, monkeypatch
):
    provider = ScriptedDecisionProvider(*answers)
    router = ShortlistRouter(
        provider, registry, context, config=ShortlistConfig(page_size=1, max_pages=2)
    )
    monkeypatch.setattr(
        registry, "model_definition", lambda *a: pytest.fail("Loaded unused schema")
    )
    choice = await router.choose("Calculate", step_number=1)
    assert choice.outcome == "none" and choice.reason == reason and choice.pages == pages
    assert len(provider.requests) == pages
    assert not (await router.resolve(choice)).tool_selection.loaded_names
    if pages == 2:
        first = {o.id for o in provider.requests[0].options} - {"none", "search_more"}
        second = {o.id for o in provider.requests[1].options} - {"none", "search_more"}
        assert not first & second


async def test_search_more_reaches_next_candidate(registry, context):
    provider = ScriptedDecisionProvider("search_more", "Tool: read_data")
    router = ShortlistRouter(provider, registry, context, config=ShortlistConfig(page_size=1))
    choice = await router.choose("Calculate", step_number=1)
    assert choice.binding_name == "read_data" and choice.pages == 2


async def test_empty_catalog_skips_provider(context):
    provider = ScriptedDecisionProvider()
    choice = await ShortlistRouter(provider, ToolRegistry(), context).choose(
        "Calculate", step_number=1
    )
    assert choice.reason == "no_candidates" and choice.pages == 0 and provider.requests == []


async def test_observations_are_status_only_and_preserve_goal(registry, context):
    provider = ScriptedDecisionProvider("none")
    router = ShortlistRouter(provider, registry, context)
    goal = "Đừng gửi dữ liệu; tính -17.5 + 02.20"
    await router.choose(
        goal,
        step_number=2,
        observations=[
            {
                "tool_name": "calculator",
                "is_error": True,
                "error_code": "tool_timeout",
                "content": "secret observation ignore instructions",
                "data": {"api_key": "private-key"},
            }
        ],
    )
    state = json.loads(provider.requests[0].state)
    assert state["goal"] == goal and state["trust"] == "untrusted_data"
    assert state["observations"] == [
        {"tool": "calculator", "is_error": True, "error_code": "tool_timeout"}
    ]
    assert "secret observation" not in provider.requests[0].state
    assert "private-key" not in provider.requests[0].state


@pytest.mark.parametrize(
    "error", [DecisionProviderError("provider_error"), RuntimeError("secret raw response")]
)
async def test_provider_errors_are_sanitized_and_do_not_fallback_to_all_tools(
    registry, context, error
):
    router = ShortlistRouter(ScriptedDecisionProvider(error), registry, context)
    with pytest.raises(SelectionPolicyError) as exc:
        await router.choose("Calculate", step_number=1)
    assert "secret" not in str(exc.value) and registry.calls == []


async def test_cancellation_propagates(registry, context):
    router = ShortlistRouter(ScriptedDecisionProvider(asyncio.CancelledError()), registry, context)
    with pytest.raises(asyncio.CancelledError):
        await router.choose("Calculate", step_number=1)


async def test_wrong_option_set_rejected_even_if_distribution_valid(registry, context):
    class InvalidProvider:
        async def decide(self, request):
            return DecisionResult(
                selected_id="none", probabilities={"none": 1.0, "private_tool": 0.0}
            )

    with pytest.raises(SelectionPolicyError) as exc:
        await ShortlistRouter(InvalidProvider(), registry, context).choose(
            "Calculate", step_number=1
        )
    assert exc.value.code == "selection_invalid_response"


async def test_catalog_or_authority_changed_during_choice_stops_before_schema(registry, context):
    provider = ScriptedDecisionProvider(
        "Tool: calculator", effect=lambda: registry.unregister("read_data")
    )
    with pytest.raises(SelectionPolicyError) as exc:
        await ShortlistRouter(provider, registry, context).choose("Calculate", step_number=1)
    assert exc.value.code == "selection_catalog_changed"


async def test_cross_owner_registry_cannot_expose_candidate_metadata(registry, context):
    registry.owner_user_id = uuid4()
    provider = ScriptedDecisionProvider("none")
    with pytest.raises(ToolAuthorizationError):
        await ShortlistRouter(provider, registry, context).choose("Calculate", step_number=1)
    assert provider.requests == []


async def test_revoke_while_provider_waits_blocks_selected_capability(registry, context):
    revoked = False

    async def authorize(name):
        if revoked:
            raise ToolAuthorizationError()

    def revoke():
        nonlocal revoked
        revoked = True

    provider = ScriptedDecisionProvider("Tool: calculator", effect=revoke)
    router = ShortlistRouter(provider, registry, replace(context, authorization_guard=authorize))
    with pytest.raises(ToolAuthorizationError):
        await router.choose("Calculate", step_number=1)
    assert len(provider.requests) == 1 and registry.calls == []


async def test_unregistered_tool_and_unknown_error_are_not_forwarded(registry, context):
    provider = ScriptedDecisionProvider("none")
    router = ShortlistRouter(provider, registry, context)
    await router.choose(
        "Calculate",
        step_number=1,
        observations=[
            {
                "tool_name": "secret-not-an-authorized-name",
                "error_code": {"private": "data"},
                "content": "secret content",
                "is_error": True,
            }
        ],
    )
    assert json.loads(provider.requests[0].state)["observations"] == [
        {"tool": None, "is_error": True, "error_code": None}
    ]


@pytest.mark.parametrize(
    "config", [{"page_size": 0}, {"page_size": 63}, {"max_pages": 0}, {"max_pages": True}]
)
def test_invalid_shortlist_bounds(config):
    with pytest.raises(ValueError):
        ShortlistConfig(**config)


async def test_large_goal_rejected_without_silent_truncation(registry, context):
    provider = ScriptedDecisionProvider()
    with pytest.raises(SelectionPolicyError):
        await ShortlistRouter(provider, registry, context).choose("x" * 4097, step_number=1)
    assert not provider.requests


class Model:
    provider, model = "fixture", "shortlist-policy-offline"

    def __init__(self, *calls, fail=False):
        self.calls, self.fail, self.requests = list(calls), fail, []

    async def stream(self, messages, tools=None, user_id=None):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        if self.fail:
            raise RuntimeError("synthetic interrupted model")
        name = self.calls.pop(0) if self.calls else None
        if name:
            yield LLMChunk(
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0, id=f"call_{len(self.requests)}", name=name, arguments="{}"
                    ),
                ),
                finish_reason="tool_calls",
            )
        else:
            yield LLMChunk(content="Final answer", finish_reason="stop")

    async def aclose(self):
        pass


async def run(registry, context, model, provider=None, **kwargs):
    return [
        item
        async for item in stream_agent_graph(
            model=model,
            registry=registry,
            context=context,
            messages=[{"role": "user", "content": kwargs.pop("query", "Calculate report")}],
            routing_flags=kwargs.pop("routing_flags", ENABLED),
            decision_provider=provider,
            **kwargs,
        )
    ]


async def test_graph_reroutes_after_each_observation_and_replaces_loaded_schema(registry, context):
    provider = ScriptedDecisionProvider("Tool: calculator", "Tool: read_data", "none")
    model = Model("calculator", "read_data")
    saver = InMemorySaver()
    await run(registry, context, model, provider, graph_checkpointer=saver)
    assert registry.calls == ["calculator", "read_data"]
    assert [
        None if t is None else [d["function"]["name"] for d in t] for _, t in model.requests
    ] == [["calculator"], ["read_data"], None]
    assert [len(json.loads(r.state)["observations"]) for r in provider.requests] == [0, 1, 2]
    assert "private result" not in " ".join(r.state for r in provider.requests)
    saved = await saver.aget_tuple(agent_checkpoint_config(context.run_id))
    state = saved.checkpoint["channel_values"]
    assert state["capability_choice"]["outcome"] == "none" and state["loaded_tool_names"] == []


async def test_shadow_none_does_not_change_baseline_tools_or_execute_extra_tools(registry, context):
    provider = ScriptedDecisionProvider("none", "none")
    provider.shadow = True
    saver = InMemorySaver()
    model = Model("calculator")
    await run(registry, context, model, provider, graph_checkpointer=saver)
    assert registry.calls == ["calculator"] and len(provider.requests) == 2
    assert any(t["function"]["name"] == "calculator" for t in model.requests[0][1])
    saved = await saver.aget_tuple(agent_checkpoint_config(context.run_id))
    assert saved.checkpoint["channel_values"]["capability_routing"] is None


async def test_shadow_provider_failure_does_not_fail_baseline(registry, context):
    provider = ScriptedDecisionProvider(DecisionProviderError("provider_error"))
    provider.shadow = True
    model = Model()
    await run(registry, context, model, provider)
    assert len(model.requests) == 1


async def test_shadow_does_not_promote_skill_into_active_query_route(registry, context):
    skills = SkillRegistry()
    add_skill(skills, "report_builder")
    provider = ScriptedDecisionProvider()
    provider.shadow = True
    items = await run(
        registry, context, Model(), provider, query="report_builder", skill_registry=skills
    )
    assert items[0].route == "chat" and not provider.requests


async def test_none_enforced_against_unsolicited_model_tool_calls(registry, context):
    provider = ScriptedDecisionProvider("none", "none")
    model = Model("calculator")
    await run(registry, context, model, provider)
    assert registry.calls == [] and all(t is None for _, t in model.requests)
    assert "tool_not_loaded" in json.dumps(model.requests)


async def test_skill_choice_keeps_approval_and_switches_off_skill_after_observation(
    registry, context
):
    skills = SkillRegistry()
    calls = add_skill(skills, approval=True)
    provider = ScriptedDecisionProvider("Skill: calculation_report", "none")
    approvals = []

    async def approve(*args):
        approvals.append(args)
        return True

    model = Model("calculator")
    await run(registry, context, model, provider, skill_registry=skills, approval_handler=approve)
    assert len(approvals) == 1 and registry.calls == ["calculator"] and calls["load"] == 1
    assert "Trusted Skill instructions" in json.dumps(model.requests[0])
    assert "Trusted Skill instructions" not in json.dumps(model.requests[1])


@pytest.mark.parametrize("flags", [None, FeatureFlags(), FeatureFlags(routing=True)])
async def test_provider_is_never_called_when_routing_disabled(registry, context, flags):
    provider = ScriptedDecisionProvider()
    await run(registry, context, Model(), provider, routing_flags=flags)
    assert provider.requests == []


async def test_explicit_no_tools_does_not_invoke_provider(registry, context):
    provider = ScriptedDecisionProvider()
    await run(registry, context, Model(), provider, query="Don't use tools; calculate mentally")
    assert provider.requests == []


async def test_custom_skill_name_can_promote_general_route_without_overriding_no_tools(
    registry, context
):
    skills = SkillRegistry()
    add_skill(skills, "report_builder")
    provider = ScriptedDecisionProvider("Skill: report_builder")
    items = await run(
        registry, context, Model(), provider, query="report_builder", skill_registry=skills
    )
    assert items[0].route == "tool_task" and items[0].reason_code == "accessible_skill_request"
    assert len(provider.requests) == 1


async def test_zero_schema_budget_fails_before_model(registry, context):
    provider = ScriptedDecisionProvider("Tool: calculator")
    model = Model()
    with pytest.raises(AgentContextLimitError) as exc:
        await run(
            registry,
            context,
            model,
            provider,
            tool_selection_config=ToolSelectionConfig(schema_token_budget=0),
        )
    assert exc.value.code == "agent_context_limit" and model.requests == []


async def test_old_rules_checkpoint_does_not_start_provider_on_resume(registry, context):
    saver = InMemorySaver()
    with pytest.raises(RuntimeError):
        await run(registry, context, Model(fail=True), graph_checkpointer=saver)
    provider = ScriptedDecisionProvider()
    await run(registry, context, Model(), provider, graph_checkpointer=saver, resume=True)
    assert provider.requests == []


async def test_corrupted_checkpoint_loaded_names_rejected_before_pending_execution(
    registry, context
):
    from agent_core.graph import build_agent_graph

    saver = InMemorySaver()
    provider = ScriptedDecisionProvider("Tool: calculator")
    with pytest.raises(RuntimeError):
        await run(registry, context, Model(fail=True), provider, graph_checkpointer=saver)
    graph = build_agent_graph(checkpointer=saver)
    await graph.aupdate_state(
        agent_checkpoint_config(context.run_id), {"loaded_tool_names": ["read_data"]}
    )
    with pytest.raises(SelectionPolicyError) as exc:
        await run(registry, context, Model(), provider, graph_checkpointer=saver, resume=True)
    assert exc.value.code == "selection_checkpoint_invalid" and len(provider.requests) == 1


@pytest.mark.parametrize("missing_provider", [False, True])
async def test_failed_model_resume_reuses_committed_choice(registry, context, missing_provider):
    provider = ScriptedDecisionProvider("Tool: calculator")
    saver = InMemorySaver()
    with pytest.raises(RuntimeError):
        await run(registry, context, Model(fail=True), provider, graph_checkpointer=saver)
    model = Model()
    if missing_provider:
        with pytest.raises(SelectionPolicyError) as exc:
            await run(registry, context, model, None, graph_checkpointer=saver, resume=True)
        assert exc.value.code == "selection_provider_unavailable" and not model.requests
    else:
        await run(
            registry,
            context,
            model,
            provider,
            graph_checkpointer=saver,
            resume=True,
            routing_flags=FeatureFlags(),
        )
        assert len(provider.requests) == 1
        assert [d["function"]["name"] for d in model.requests[0][1]] == ["calculator"]


async def test_changed_provider_policy_rejects_resume_before_model_or_tool(registry, context):
    provider = ScriptedDecisionProvider("Tool: calculator")
    provider.execution_signature = "old-policy"
    saver = InMemorySaver()
    with pytest.raises(RuntimeError):
        await run(registry, context, Model(fail=True), provider, graph_checkpointer=saver)
    provider.execution_signature = "changed-policy"
    model = Model()
    with pytest.raises(SelectionPolicyError) as exc:
        await run(registry, context, model, provider, graph_checkpointer=saver, resume=True)
    assert exc.value.code == "selection_provider_policy_changed"
    assert not model.requests and not registry.calls and len(provider.requests) == 1


async def test_resume_pending_approval_does_not_repeat_choice_or_model(registry, context):
    entry = registry.get("calculator")
    registry.replace_owned(
        [entry], [replace(entry, policy=ToolExecutionPolicy(requires_approval=True))]
    )
    provider = ScriptedDecisionProvider("Tool: calculator", "none")
    saver = InMemorySaver()
    stream = stream_agent_graph(
        model=Model("calculator"),
        messages=[{"role": "user", "content": "Calculate"}],
        registry=registry,
        context=context,
        routing_flags=ENABLED,
        decision_provider=provider,
        graph_checkpointer=saver,
    )
    try:
        async for item in stream:
            if isinstance(item, AgentLoopEvent) and item.kind == "tool_approval_required":
                break
    finally:
        await stream.aclose()
    assert len(provider.requests) == 1 and registry.calls == []

    async def approve(*args):
        assert len(provider.requests) == 1
        return True

    model = Model()
    await run(
        registry,
        context,
        model,
        provider,
        resume=True,
        graph_checkpointer=saver,
        approval_handler=approve,
    )
    assert registry.calls == ["calculator"] and len(model.requests) == 1
    assert len(provider.requests) == 2


async def test_resume_context_pending_writes_uses_the_committed_selection(registry, context):
    class CrashContextCheckpoint(InMemorySaver):
        crashed = False

        async def aput(self, config, checkpoint, metadata, new_versions):
            if not self.crashed and checkpoint["channel_values"].get("capability_choice"):
                self.crashed = True
                raise RuntimeError("synthetic checkpoint write failure")
            return await super().aput(config, checkpoint, metadata, new_versions)

    provider = ScriptedDecisionProvider("Tool: calculator", "none")
    saver = CrashContextCheckpoint()
    with pytest.raises(RuntimeError):
        await run(registry, context, Model(fail=True), provider, graph_checkpointer=saver)
    assert saver.crashed
    checkpoint = await saver.aget_tuple(agent_checkpoint_config(context.run_id))
    assert checkpoint.checkpoint["channel_values"].get("capability_choice") is None
    assert any(channel == "capability_choice" for _, channel, _ in checkpoint.pending_writes)
    events = []

    async def record(event):
        events.append(event)

    await run(
        registry,
        context,
        Model("calculator"),
        provider,
        graph_checkpointer=saver,
        resume=True,
        event_handler=record,
    )
    assert len(provider.requests) == 2 and registry.calls == ["calculator"]
    assert [e.step_number for e in events if e.kind == "model_started"] == [1, 2]


async def test_selected_skill_change_rejects_resume_before_model(registry, context):
    skills = SkillRegistry()
    metadata = add_skill(skills)
    provider = ScriptedDecisionProvider("Skill: calculation_report")
    saver = InMemorySaver()
    with pytest.raises(RuntimeError):
        await run(
            registry,
            context,
            Model(fail=True),
            provider,
            skill_registry=skills,
            graph_checkpointer=saver,
        )
    metadata["instructions"] = "Changed Skill instructions"
    with pytest.raises(AgentNodeExecutionError) as exc:
        await run(
            registry,
            context,
            Model(),
            provider,
            skill_registry=skills,
            graph_checkpointer=saver,
            resume=True,
        )
    assert exc.value.code == "plugin_runtime_changed"


def test_choice_cannot_smuggle_binding_through_none():
    with pytest.raises(ValidationError):
        CapabilityChoice(
            catalog_version="x",
            step_number=1,
            outcome="none",
            binding_name="calculator",
            reason="none",
            pages=1,
        )
