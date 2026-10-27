import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from agent_core.context_budget import ContextTokenBudget
from agent_core.contracts.retrieval import RetrievalResult
from agent_core.graph import AgentLoopEvent, stream_agent_graph
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry
from agent_core.tools.documents import (
    DocumentQueryInput,
    create_document_registry,
    register_document_tools,
)
from agent_core.tools.types import ToolAuthorizationError
from backend.schemas.document import DocumentSearchResponse
from backend.services import document_tools
from backend.services.document_qa import DocumentQAResult


def hit(number=1, **updates):
    return RetrievalResult(
        citation_id=f"source.{number}",
        document_id=UUID(int=10),
        version_id=UUID(int=20),
        chunk_id=UUID(int=number),
        title="Private.pdf",
        text=f"Evidence {number}",
        page_start=number,
        page_end=number,
        score=1.0,
        score_kind="fusion",
        retrieval_config_version="test1",
    ).model_copy(update=updates)


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


@pytest.fixture
def bridge(monkeypatch):
    context = ToolContext(
        user_id=uuid4(), conversation_id=uuid4(), user_role="user", run_id=uuid4()
    )
    result = DocumentQAResult(document_ids=[UUID(int=10)], versions={UUID(int=20): "hash"})
    service = document_tools.DocumentToolSession(
        owner=context.user_id,
        conversation_id=context.conversation_id,
        run_id=context.run_id,
        message_id=uuid4(),
        result=result,
        search_mode="fast",
        context_budget=ContextTokenBudget(),
        quota_guard=SimpleNamespace(
            before_execution=AsyncMock(),
            record_tokens=AsyncMock(),
            user_id=context.user_id,
            token_limit=100_000,
        ),
        recorder=AsyncMock(),
    )
    monkeypatch.setattr(service, "validate", AsyncMock())
    monkeypatch.setattr(service, "persist", AsyncMock())
    monkeypatch.setattr(document_tools, "SessionFactory", Session)
    return service, context


class Model:
    provider, model = "test", "test-model"

    def __init__(self):
        self.messages = []

    async def stream(self, messages, **kwargs):
        self.messages.append(messages)
        if len(self.messages) == 1:
            yield LLMChunk(
                finish_reason="tool_calls",
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0,
                        id="lookup",
                        name="search_documents",
                        arguments='{"query":"Question"}',
                    ),
                ),
            )
        else:
            yield LLMChunk(content="Read actual evidence", finish_reason="stop")

    async def aclose(self):
        pass


async def test_retrieval_tool_streams_real_graph_progress_and_exposes_only_host_sources(
    bridge, monkeypatch
):
    service, context = bridge
    retrieve = AsyncMock(
        return_value=DocumentSearchResponse(
            query="Question",
            mode="hybrid",
            items=[hit()],
            retrieval_config_version="test1",
        )
    )
    monkeypatch.setattr(document_tools, "retrieve_document_evidence", retrieve)
    registry = ToolRegistry()
    errors = []

    async def search(context, arguments):
        try:
            return await service.search(context, arguments)
        except Exception as exc:
            errors.append(exc)
            raise

    register_document_tools(
        registry, search=search, summarize=service.summarize, timeout_seconds=60
    )
    model = Model()
    items = [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Question"}],
            registry=registry,
            context=context,
            react_enabled=True,
        )
    ]
    completed = next(
        i for i in items if isinstance(i, AgentLoopEvent) and i.kind == "tool_completed"
    )
    assert not errors
    assert not completed.tool_result.is_error
    assert "Evidence 1" in json.dumps(model.messages[1])
    assert any(isinstance(i, AgentLoopEvent) and i.kind == "pipeline_progress" for i in items)
    assert retrieve.await_args.kwargs["query_is_planned"] is True
    assert retrieve.await_args.kwargs["candidates_only"] is True
    assert len(service.result.evidence) == 1
    assert completed.tool_result.data["document_references"][0]["citation_id"] == "S1"
    assert "text" not in completed.tool_result.data["document_references"][0]


async def test_worker_document_sessions_keep_passages_private_and_labels_canonical(
    bridge, monkeypatch
):
    service, context = bridge
    monkeypatch.setattr(document_tools.DocumentToolSession, "validate", AsyncMock())
    monkeypatch.setattr(
        document_tools,
        "retrieve_document_evidence",
        AsyncMock(
            side_effect=[
                DocumentSearchResponse(
                    query="First", mode="hybrid", items=[hit(1)], retrieval_config_version="test1"
                ),
                DocumentSearchResponse(
                    query="Second", mode="hybrid", items=[hit(2)], retrieval_config_version="test1"
                ),
            ]
        ),
    )
    base = ToolRegistry()
    async with service.worker_registry(SimpleNamespace(task_id="first"), base, 60) as first:
        async with service.worker_registry(SimpleNamespace(task_id="second"), base, 60) as second:
            one = await ToolExecutor().execute(
                first, context, "search_documents", {"query": "First"}
            )
            two = await ToolExecutor().execute(
                second, context, "search_documents", {"query": "Second"}
            )
            assert not one.is_error and not two.is_error
            assert "Evidence 1" in one.context_blocks[0].content
            assert "Evidence 2" not in one.context_blocks[0].content
            assert "Evidence 2" in two.context_blocks[0].content
            assert [h["citation_id"] for h in one.data["document_references"]] == ["S1"]
            assert [h["citation_id"] for h in two.data["document_references"]] == ["S2"]
    assert service.summary_chunks == {UUID(int=1), UUID(int=2)}
    assert service.used and len(service.actions) == 2
    assert not base.list_tools()


def test_document_tool_schema_cannot_override_owner_scope_or_mode():
    for args in (
        {"query": "x", "owner": str(uuid4())},
        {"query": "x", "document_ids": [str(uuid4())]},
        {"query": "x", "search_mode": "fast"},
        {"query": " "},
    ):
        with pytest.raises(ValidationError):
            DocumentQueryInput.model_validate(args)


async def test_planned_task_lineage_retains_only_evidence_already_received_in_scope(bridge):
    service, _ = bridge
    first, second = service.accept([hit(1), hit(2)])
    service.current_chunks = {second.chunk_id}
    snapshot = {"results": {"book": {"evidence_refs": [first.reference().model_dump(mode="json")]}}}
    await service.retain_task_sources(snapshot)
    await service.finalize()
    assert {h.chunk_id for h in service.result.evidence} == {first.chunk_id, second.chunk_id}
    bad = first.reference().model_copy(update={"version_id": uuid4()})
    with pytest.raises(document_tools.DocumentServiceError):
        await service.retain_task_sources(
            {"results": {"fake": {"evidence_refs": [bad.model_dump(mode="json")]}}}
        )


@pytest.mark.parametrize("budget_error", [False, True])
async def test_empty_evidence_is_distinct_from_evidence_budget_failure(
    bridge, monkeypatch, budget_error
):
    from dataclasses import replace

    service, context = bridge
    monkeypatch.setattr(
        document_tools,
        "retrieve_document_evidence",
        AsyncMock(
            return_value=DocumentSearchResponse(
                query="Question",
                mode="hybrid",
                items=[hit()] if budget_error else [],
                retrieval_config_version="test1",
            ),
        ),
    )
    if budget_error:
        service.context_budget = replace(service.context_budget, rag_tokens=0)
    result = await service.search(context, {"query": "Question"})
    assert result.is_error is budget_error
    assert result.error_code == ("document_evidence_budget" if budget_error else None)
    assert not result.context_blocks and not service.result.evidence


def test_document_registry_does_not_leak_handlers_between_users_or_runs(bridge):
    service, _ = bridge
    base = ToolRegistry()
    first = create_document_registry(
        base, search=service.search, summarize=service.summarize, timeout_seconds=60
    )
    second = create_document_registry(
        base, search=AsyncMock(), summarize=AsyncMock(), timeout_seconds=60
    )
    assert len(base) == 0 and len(first) == len(second) == 2
    assert first.get("search_documents") is not second.get("search_documents")


def test_reference_merge_deduplicates_global_chunk_ids_and_rejects_cross_scope(bridge):
    service, _ = bridge
    service.accept([hit(1), hit(2)])
    merged = service.accept([hit(2), hit(3)])
    assert [h.citation_id for h in merged] == ["S2", "S3"]
    assert len(service.result.evidence) == 3
    with pytest.raises(document_tools.DocumentServiceError):
        service.accept([hit(4, document_id=uuid4())])


async def test_refined_search_replaces_bounded_context_and_final_references(bridge, monkeypatch):
    service, context = bridge
    retrieve = AsyncMock(
        side_effect=[
            DocumentSearchResponse(
                query="First",
                mode="hybrid",
                items=[hit(1)],
                retrieval_config_version="test1",
            ),
            DocumentSearchResponse(
                query="Refined",
                mode="hybrid",
                items=[hit(2)],
                retrieval_config_version="test1",
            ),
        ]
    )
    monkeypatch.setattr(document_tools, "retrieve_document_evidence", retrieve)
    registry = create_document_registry(
        ToolRegistry(), search=service.search, summarize=service.summarize, timeout_seconds=60
    )

    class RefineModel(Model):
        async def stream(self, messages, **kwargs):
            self.messages.append(messages)
            n = len(self.messages)
            if n < 3:
                yield LLMChunk(
                    finish_reason="tool_calls",
                    tool_call_deltas=(
                        LLMToolCallDelta(
                            index=0,
                            id=f"search{n}",
                            name="search_documents",
                            arguments=json.dumps({"query": "First" if n == 1 else "Refined"}),
                        ),
                    ),
                )
            else:
                yield LLMChunk(content="Refined answer", finish_reason="stop")

    model = RefineModel()
    _ = [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Question"}],
            registry=registry,
            context=context,
            react_enabled=True,
        )
    ]
    final = json.dumps(model.messages[-1])
    assert "Evidence 2" in final and "Evidence 1" not in final
    assert len(service.result.evidence) == 2
    await service.finalize()
    assert [h.chunk_id for h in service.result.evidence] == [UUID(int=2)]


async def test_document_handler_rejects_a_different_trusted_user_before_reading(bridge):
    service, context = bridge
    from dataclasses import replace

    with pytest.raises(ToolAuthorizationError):
        await service.search(replace(context, user_id=uuid4()), {"query": "Question"})
    service.validate.assert_not_awaited()


async def test_reindex_scope_change_is_rejected_before_using_evidence(bridge, monkeypatch):
    service, _ = bridge
    monkeypatch.setattr(
        document_tools,
        "_scope",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=UUID(int=20),
                    document_id=UUID(int=10),
                    pipeline_hash="changed-hash",
                )
            ]
        ),
    )
    with pytest.raises(document_tools.DocumentServiceError) as exc:
        await document_tools.DocumentToolSession.validate(service)
    assert exc.value.code == "document_scope_changed"


async def test_resume_restores_sources_from_owned_tool_records_and_pinned_versions(
    bridge, monkeypatch
):
    service, _ = bridge
    reference = hit().model_dump(mode="json", exclude={"text"})
    run = SimpleNamespace(rag_trace={"document_scope": service.scope_data})
    row = SimpleNamespace(
        id=UUID(int=1),
        document_id=UUID(int=10),
        version_id=UUID(int=20),
        text="Evidence 1",
        page_start=1,
        page_end=1,
        section=None,
        start_offset=None,
        end_offset=None,
    )

    class RestoreSession(Session):
        async def scalar(self, statement):
            return run

        async def scalars(self, statement):
            return SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        tool_name="search_documents",
                        status="completed",
                        result_data={
                            "document_scope": service.scope_data,
                            "document_references": [reference],
                        },
                    )
                ]
            )

        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [(row, "Private.pdf")])

    monkeypatch.setattr(document_tools, "SessionFactory", RestoreSession)
    monkeypatch.setattr(
        document_tools,
        "_scope",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=UUID(int=20),
                    document_id=UUID(int=10),
                    pipeline_hash="hash",
                )
            ]
        ),
    )
    await service.prepare(resume=True)
    assert service.used and service.result.evidence[0].chunk_id == UUID(int=1)
    assert service.current_chunks == {UUID(int=1)}
    assert service.result.evidence[0].citation_id == "S1"


@pytest.mark.parametrize("failed", [False, True])
async def test_summary_delegate_preserves_actual_usage_and_returns_once_to_parent(
    bridge, monkeypatch, failed
):
    service, context = bridge
    calls = []

    async def summary(**kwargs):
        calls.append(kwargs)
        assert kwargs["planned_query"].mode == "summary" and kwargs["preserve_limits"]
        await kwargs["recorder"](
            AgentLoopEvent(
                kind="model_completed",
                step_number=1,
                usage=TokenUsage(prompt_tokens=8, completion_tokens=2, total_tokens=10),
            )
        )
        kwargs["result"].trace = {"calls": [{"usage": {"total_tokens": 10}}]}
        if failed:
            raise document_tools.DocumentServiceError(
                "Summary limit", code="document_summary_limit"
            )
        kwargs["result"].evidence = [hit()]
        yield LLMChunk(content="Complete summary")

    monkeypatch.setattr(document_tools, "stream_document_qa", summary)
    model = SimpleNamespace(provider="test", model="summary-model", aclose=AsyncMock())
    monkeypatch.setattr(document_tools, "create_chat_model", lambda: model)
    result = await service.summarize(context, {"query": "Summarize all"})
    assert result.is_error is failed and result.usage.total_tokens == 10
    assert result.data["internal_model_calls"] == 1
    service.quota_guard.record_tokens.assert_not_awaited()
    assert result.data["document_references"] == (
        [] if failed else [service.result.evidence[0].model_dump(mode="json", exclude={"text"})]
    )
    model.aclose.assert_awaited_once()


async def test_real_summary_subgraph_map_reduce_inside_tool_keeps_step_usage_and_all_sources(
    bridge,
    monkeypatch,
):
    from backend.services import document_qa, usage_limits

    service, context = bridge
    hits = [hit(n, text=f"Paragraph {n}. " + "data " * 300) for n in range(1, 13)]
    rows = [
        (
            SimpleNamespace(
                id=h.chunk_id,
                document_id=h.document_id,
                version_id=h.version_id,
                text=h.text,
                page_start=h.page_start,
                page_end=h.page_end,
                section=None,
                start_offset=None,
                end_offset=None,
            ),
            h.title,
        )
        for h in hits
    ]

    class SummarySession(Session):
        async def execute(self, statement):
            if any(
                d.get("entity") is document_qa.DocumentChunk for d in statement.column_descriptions
            ):
                return SimpleNamespace(all=lambda: rows)
            return SimpleNamespace(all=lambda: [(UUID(int=10), "Private.pdf")])

    class SummaryModel:
        provider, model = "test", "notes-model"
        requests = 0

        async def stream(self, messages, **kwargs):
            self.requests += 1
            assert kwargs["json_mode"] is False  # No second planner/rewrite call.
            yield LLMChunk(
                content="Concise faithful notes.",
                finish_reason="stop",
                usage=TokenUsage(prompt_tokens=8, completion_tokens=2, total_tokens=10),
            )

        async def aclose(self):
            pass

    class RootModel(Model):
        async def stream(self, messages, **kwargs):
            self.messages.append(messages)
            if len(self.messages) == 1:
                yield LLMChunk(
                    finish_reason="tool_calls",
                    tool_call_deltas=(
                        LLMToolCallDelta(
                            index=0,
                            id="summary",
                            name="summarize_documents",
                            arguments='{"query":"Summarize all documents"}',
                        ),
                    ),
                )
            else:
                yield LLMChunk(content="Final summary", finish_reason="stop")

    monkeypatch.setattr(document_qa, "SessionFactory", SummarySession)
    monkeypatch.setattr(
        document_qa,
        "_scope",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id=UUID(int=20),
                    document_id=UUID(int=10),
                    pipeline_hash="hash",
                )
            ]
        ),
    )
    monkeypatch.setattr(document_qa, "_recent_history", AsyncMock(return_value=([], False)))
    monkeypatch.setattr(usage_limits, "get_current_usage", AsyncMock(return_value=0))
    child_model = SummaryModel()
    monkeypatch.setattr(document_tools, "create_chat_model", lambda: child_model)
    registry = ToolRegistry()
    register_document_tools(
        registry, search=service.search, summarize=service.summarize, timeout_seconds=60
    )
    model = RootModel()
    items = [
        i
        async for i in stream_agent_graph(
            model=model,
            messages=[{"role": "user", "content": "Summarize all"}],
            registry=registry,
            context=context,
            react_enabled=True,
        )
    ]
    done = next(i for i in items if isinstance(i, AgentLoopEvent) and i.kind == "tool_completed")
    assert not done.tool_result.is_error
    assert child_model.requests >= 3
    assert done.tool_result.usage.total_tokens == child_model.requests * 10
    assert len(service.result.evidence) == 12
    assert done.tool_result.data["coverage"]["processed_chunks"] == 12
    assert len(model.messages) == 2
    assert "document_references" not in json.dumps(model.messages[-1])
    final_model = [i for i in items if isinstance(i, AgentLoopEvent) and i.kind == "model_started"][
        -1
    ]
    assert final_model.step_number == child_model.requests + 2
