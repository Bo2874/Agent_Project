"""Private document QA, bounded full-scope summaries, run trace and citation access."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import asdict, dataclass, field, replace
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import UUID

from langgraph.config import get_stream_writer
from sqlalchemy import select

from agent_core.context_budget import (
    ContextBlock,
    ContextBudgetError,
    ContextTokenBudget,
    measure_context_blocks,
)
from agent_core.context_middleware import ContextAssemblyMiddleware
from agent_core.contracts.retrieval import RetrievalResult
from agent_core.graph import AgentLimits, AgentLoopEvent
from agent_core.llm.deepseek import to_langchain_messages, to_model_messages
from agent_core.llm.types import ChatModel, LLMChunk, TokenUsage
from agent_core.rag.qa import (
    ANSWER_PROMPT,
    ANSWER_VERSION,
    PLAN_PROMPT,
    PLAN_VERSION,
    SUMMARY_PROMPT,
    GroundedAnswer,
    QueryPlan,
    build_document_qa_graph,
    parse_json,
)
from agent_core.rag.retrieval import evidence_block, pack_evidence
from backend.database import SessionFactory
from backend.models import (
    AgentRun,
    Conversation,
    ConversationDocument,
    Document,
    DocumentChunk,
    DocumentCitation,
    User,
)
from backend.schemas.document import DocumentSearchRequest
from backend.services.document_query import _recent_history, retrieve_document_evidence
from backend.services.document_retrieval import _hit, _scope, scoped_chunks
from backend.services.documents import DocumentServiceError

if TYPE_CHECKING:
    from backend.services.agent_runs import AgentRunRecorder
    from backend.services.usage_limits import AgentQuotaGuard


@dataclass
class DocumentQAResult:
    evidence: list[RetrievalResult] = field(default_factory=list)
    document_ids: list[UUID] = field(default_factory=list)
    versions: dict[UUID, str] = field(default_factory=dict)
    trace: dict = field(default_factory=dict)


async def _save_trace(run_id, owner, trace):
    # Keep text passages out of the durable run trace; citation bindings point to their versions.
    async with SessionFactory() as session:
        run = await session.scalar(
            select(AgentRun)
            .join(Conversation)
            .where(
                AgentRun.id == run_id,
                Conversation.user_id == owner,
                AgentRun.status == "running",
            )
        )
        if run is None:
            raise DocumentServiceError(
                "Lần chạy không còn khả dụng.", code="document_run_unavailable"
            )
        run.rag_trace = json.loads(json.dumps(trace, default=str))
        await session.commit()


async def stream_document_qa(
    *,
    model: ChatModel,
    owner: UUID,
    conversation_id: UUID,
    message_id: UUID,
    query: str,
    run_id: UUID,
    recorder: AgentRunRecorder,
    quota_guard: AgentQuotaGuard,
    limits: AgentLimits,
    context_budget: ContextTokenBudget,
    result: DocumentQAResult,
) -> AsyncIterator[LLMChunk | AgentLoopEvent]:
    request = DocumentSearchRequest(query=query[:4000], conversation_id=conversation_id)
    total_usage, calls = TokenUsage(), 0
    started = perf_counter()
    result.trace = {
        "version": ANSWER_VERSION,
        "original_query": query[:4000],
        "calls": [],
        "retrievals": [],
        "coverage": {},
    }

    async def revalidate():
        async with SessionFactory() as session:
            versions = await _scope(session, owner, request)
            if {v.id: v.pipeline_hash for v in versions} != result.versions:
                raise DocumentServiceError(
                    "Phạm vi tài liệu đã thay đổi. Hãy gửi lại câu hỏi.",
                    status_code=409,
                    code="document_scope_changed",
                )

    async def call_model(prompt, payload, *, blocks=(), version=ANSWER_VERSION, allowed=None):
        nonlocal calls, total_usage
        if calls >= limits.max_steps or total_usage.total_tokens >= limits.max_total_tokens:
            raise DocumentServiceError(
                "Yêu cầu vượt budget hỏi đáp tài liệu. Hãy thu hẹp phạm vi.",
                status_code=422,
                code="document_qa_budget",
            )
        await quota_guard.before_execution("model")
        await revalidate()
        calls += 1
        prepared = ContextAssemblyMiddleware(
            context_budget, rag=blocks, tool_selection_mode="document_qa"
        ).prepare(
            to_langchain_messages(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ]
            )
        )
        usage_context = replace(prepared.usage, prompt_version=version)
        event = AgentLoopEvent(kind="model_started", step_number=calls, context_usage=usage_context)
        await recorder(event)
        get_stream_writer()(event)
        usage, parts, size, status, error = TokenUsage(), [], 0, "failed", None
        clock = perf_counter()
        try:
            async with aclosing(
                model.stream(to_model_messages(prepared.messages), tools=None, user_id=str(owner))
            ) as stream:
                async for chunk in stream:
                    if chunk.usage:
                        usage += chunk.usage
                    if chunk.tool_call_deltas:
                        raise ValueError("Document QA cannot call tools")
                    size += len(chunk.content)
                    if size > 40000:
                        raise ValueError("Output exceeds document QA limit")
                    parts.append(chunk.content)
                    if chunk.finish_reason not in (None, "stop"):
                        raise ValueError("Incomplete model response")
            parsed = parse_json("".join(parts))
            value = (
                QueryPlan.model_validate(parsed)
                if allowed is None
                else GroundedAnswer.model_validate(parsed).validate_sources(set(allowed))
            )
            status = "completed"
            return value
        except asyncio.CancelledError:
            status = "cancelled"
            error = "Đã hủy hỏi đáp tài liệu."
            raise
        except Exception as exc:
            error = "Model chưa tạo được câu trả lời và citation hợp lệ. Hãy thử lại."
            raise DocumentServiceError(
                error, status_code=503, code="document_qa_model_failed"
            ) from exc
        finally:
            duration = round((perf_counter() - clock) * 1000)
            total_usage += usage
            await quota_guard.record_tokens(usage.total_tokens)
            event = AgentLoopEvent(
                kind="model_completed" if status == "completed" else "model_failed",
                step_number=calls,
                duration_ms=duration,
                usage=usage,
                finish_reason="stop"
                if status == "completed"
                else "cancelled"
                if status == "cancelled"
                else None,
                error_message=error,
            )
            await recorder(event)
            get_stream_writer()(event)
            result.trace["calls"].append(
                {
                    "step": calls,
                    "prompt_version": version,
                    "status": status,
                    "usage": asdict(usage),
                    "latency_ms": duration,
                    "provider": model.provider,
                    "model": model.model,
                }
            )
            await _save_trace(run_id, owner, result.trace)

    async def prepare(_):
        if len(query) > 4000:
            raise DocumentServiceError(
                "Câu hỏi tài liệu tối đa 4.000 ký tự. Hãy rút gọn yêu cầu.",
                status_code=422,
                code="document_query_limit",
            )
        async with SessionFactory() as session:
            try:
                versions = await _scope(session, owner, request)
            except DocumentServiceError as exc:
                if exc.code not in {"document_scope_empty", "documents_not_ready"}:
                    raise
                text = (
                    "Bạn hãy tải lên hoặc chọn tài liệu cho hội thoại này, rồi gửi lại câu hỏi."
                    if exc.code == "document_scope_empty"
                    else "Tài liệu đang được xử lý hoặc chưa sẵn sàng. "
                    "Hãy kiểm tra trạng thái file rồi hỏi lại."
                )
                result.trace["availability"] = exc.code
                await _save_trace(run_id, owner, result.trace)
                return {
                    "terminal": True,
                    "answer": {"answer": text, "sources": [], "answerable": False},
                }
            result.document_ids = sorted([v.document_id for v in versions], key=str)
            result.versions = {v.id: v.pipeline_hash for v in versions}
            files = (
                await session.execute(
                    select(Document.id, Document.title)
                    .where(
                        Document.id.in_(result.document_ids),
                        Document.owner_user_id == owner,
                    )
                    .order_by(Document.id)
                )
            ).all()
            history, trimmed = await _recent_history(session, owner, conversation_id, message_id)
        result.trace.update(
            document_ids=[str(v) for v in result.document_ids],
            version_hashes={str(k): v for k, v in result.versions.items()},
            history_messages=len(history),
            history_trimmed=trimmed,
        )
        plan = await call_model(
            PLAN_PROMPT,
            {
                "question": query,
                "recent_history": history,
                "history_trimmed": trimmed,
                "file_count": len(files),
                "files": [{"id": str(v[0]), "title": v[1][:200]} for v in files[:20]],
                "file_metadata_trimmed": len(files) > 20,
            },
            version=PLAN_VERSION,
        )
        if not history:
            plan.query = query
        result.trace["plan"] = plan.model_dump()
        await _save_trace(run_id, owner, result.trace)
        return {"plan": plan.model_dump()}

    async def retrieve(state):
        plan = QueryPlan.model_validate(state["plan"])
        evidence = []
        if plan.mode == "summary":
            async with SessionFactory() as session:
                rows = (
                    await session.execute(
                        scoped_chunks(owner, list(result.versions))
                        .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
                        .limit(65)
                    )
                ).all()
            if len(rows) > 64:
                raise DocumentServiceError(
                    "Tài liệu vượt phạm vi tóm tắt trong một lần hỏi. Hãy chọn ít file hơn "
                    "hoặc hỏi một chủ đề cụ thể.",
                    status_code=422,
                    code="document_summary_limit",
                )
            evidence = [
                _hit(chunk, title, 1.0, "coverage").model_copy(
                    update={"retrieval_config_version": "document-all-chunks-v1"}
                )
                for chunk, title in rows
            ]
            result.trace["coverage"] = {
                "mode": "all_chunks",
                "loaded_chunks": len(rows),
                "processed_chunks": 0,
                "document_count": len(result.document_ids),
            }
        else:
            rankings = []
            for subquery in plan.queries:
                await revalidate()
                async with SessionFactory() as session:
                    response = await retrieve_document_evidence(
                        session,
                        owner,
                        DocumentSearchRequest(
                            query=subquery, document_ids=result.document_ids, mode="hybrid"
                        ),
                    )
                rankings.append(response.items)
                trace = response.model_dump(mode="json", exclude={"items"})
                result.trace["retrievals"].append(trace)
                await _save_trace(run_id, owner, result.trace)
            seen = set()
            # Interleave subquestions so the first query cannot consume the entire context.
            for rank in range(max((len(items) for items in rankings), default=0)):
                for items in rankings:
                    if rank < len(items) and items[rank].chunk_id not in seen:
                        seen.add(items[rank].chunk_id)
                        evidence.append(items[rank])
            evidence = pack_evidence(evidence[:20], token_budget=context_budget.rag_tokens).items
            result.trace["coverage"] = {
                "mode": "retrieved_passages",
                "queries": [
                    {
                        "query": q,
                        "selected_chunks": len(
                            {h.chunk_id for h in items} & {h.chunk_id for h in evidence}
                        ),
                    }
                    for q, items in zip(plan.queries, rankings, strict=True)
                ],
            }
        result.evidence = [
            hit.model_copy(update={"citation_id": f"S{index}"})
            for index, hit in enumerate(evidence, 1)
        ]
        result.trace["evidence"] = [
            hit.model_dump(mode="json", exclude={"text"}) for hit in result.evidence
        ]
        await revalidate()
        await _save_trace(run_id, owner, result.trace)
        return {"evidence": [hit.model_dump(mode="json") for hit in result.evidence]}

    def batches(blocks):
        groups, current = [], []
        for block in blocks:
            if measure_context_blocks("rag", [*current, block]) > context_budget.rag_tokens:
                if not current:
                    raise DocumentServiceError(
                        "Một đoạn nguồn quá dài để xử lý trong lần hỏi này. "
                        "Hãy dùng bản tài liệu gọn hơn hoặc chọn file khác.",
                        status_code=422,
                        code="document_evidence_budget",
                    )
                groups.append(current)
                current = []
            current.append(block)
            if measure_context_blocks("rag", current) > context_budget.rag_tokens:
                raise DocumentServiceError(
                    "Đoạn nguồn vượt budget context.",
                    status_code=422,
                    code="document_evidence_budget",
                )
        if current:
            groups.append(current)
        return groups

    async def answer(state):
        plan = QueryPlan.model_validate(state["plan"])
        allowed = {hit.citation_id for hit in result.evidence}
        blocks = [evidence_block(hit) for hit in result.evidence]
        if not blocks:
            return {
                "answer": {
                    "answer": "Tôi chưa tìm thấy thông tin phù hợp trong các tài liệu đã chọn.",
                    "sources": [],
                    "answerable": False,
                }
            }
        if (
            plan.mode == "summary"
            and measure_context_blocks("rag", blocks) > context_budget.rag_tokens
        ):
            groups = batches(blocks)
            if calls + len(groups) + 1 > limits.max_steps:
                raise DocumentServiceError(
                    "Tóm tắt toàn bộ vượt budget số lần gọi model. Hãy chọn "
                    "ít file hơn hoặc hỏi một chủ đề cụ thể.",
                    status_code=422,
                    code="document_qa_budget",
                )
            level = 0
            while measure_context_blocks("rag", blocks) > context_budget.rag_tokens:
                level += 1
                notes = []
                for group in batches(blocks):
                    group_labels = set()
                    for block in group:
                        group_labels.update(
                            {block.source}
                            if re.fullmatch(r"S\d+", block.source or "")
                            else re.findall(r"\[(S\d+)\]", block.content)
                        )
                    group_labels &= allowed
                    note = await call_model(
                        SUMMARY_PROMPT,
                        {
                            "question": plan.query,
                            "coverage": "one batch of all selected chunks",
                            "allowed_sources": sorted(group_labels),
                        },
                        blocks=group,
                        allowed=group_labels,
                        version="document-summary-notes-v1",
                    )
                    notes.append(
                        ContextBlock(
                            content=note.answer, source=f"summary.{level}.{len(notes) + 1}"
                        )
                    )
                previous = measure_context_blocks("rag", blocks)
                blocks = notes
                if measure_context_blocks("rag", blocks) >= previous:
                    raise DocumentServiceError(
                        "Không thu gọn được bản tóm tắt trong budget.",
                        status_code=422,
                        code="document_summary_budget",
                    )
            result.trace["coverage"]["summary_levels"] = level
            allowed &= set(re.findall(r"\[?(S\d+)\]?", " ".join(b.content for b in blocks)))
        output = await call_model(
            ANSWER_PROMPT,
            {
                "question": plan.query,
                "aspects": plan.queries,
                "mode": plan.mode,
                "coverage": result.trace["coverage"],
                "allowed_sources": sorted(allowed),
            },
            blocks=blocks,
            allowed=allowed,
        )
        if total_usage.total_tokens > limits.max_total_tokens:
            raise DocumentServiceError(
                "Đã vượt budget token hỏi đáp tài liệu.", status_code=422, code="document_qa_budget"
            )
        await revalidate()
        if plan.mode == "summary":
            result.trace["coverage"]["processed_chunks"] = len(result.evidence)
        result.evidence = [hit for hit in result.evidence if hit.citation_id in output.sources]
        result.trace["answerable"] = output.answerable
        result.trace["cited_sources"] = output.sources
        await _save_trace(run_id, owner, result.trace)
        return {"answer": output.model_dump()}

    graph = build_document_qa_graph(prepare=prepare, retrieve=retrieve, answer=answer)
    final = None
    try:
        async with asyncio.timeout(limits.max_duration_seconds):
            async for mode, item in graph.astream({}, stream_mode=["custom", "values"]):
                if mode == "custom":
                    yield item
                else:
                    final = item
        if not final or "answer" not in final:
            raise DocumentServiceError(
                "Không hoàn tất hỏi đáp tài liệu.", code="document_qa_failed"
            )
        yield LLMChunk(content=final["answer"]["answer"], usage=total_usage)
    except TimeoutError:
        raise DocumentServiceError(
            "Hỏi đáp tài liệu vượt giới hạn thời gian. Hãy thử lại hoặc thu hẹp phạm vi.",
            status_code=503,
            code="document_qa_timeout",
        ) from None
    except ContextBudgetError:
        raise DocumentServiceError(
            "Nội dung vượt budget context hỏi đáp tài liệu. Hãy thu hẹp yêu cầu.",
            status_code=422,
            code="document_context_budget",
        ) from None
    finally:
        result.trace["latency_ms"] = round((perf_counter() - started) * 1000)


async def lock_document_answer_scope(session, *, conversation_id, owner, result):
    # Same lock order as selection/upload/deletion: owner -> conversation -> documents.
    await session.scalar(select(User.id).where(User.id == owner).with_for_update())
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == owner)
        .with_for_update()
    )
    if conversation is None:
        raise DocumentServiceError("Hội thoại không còn khả dụng.", code="conversation_not_found")
    if result.document_ids:
        documents = (
            await session.scalars(
                select(Document)
                .where(
                    Document.id.in_(result.document_ids),
                    Document.owner_user_id == owner,
                    Document.deleted_at.is_(None),
                    Document.status == "ready",
                )
                .order_by(Document.id)
                .with_for_update()
            )
        ).all()
        selected = set(
            (
                await session.scalars(
                    select(ConversationDocument.document_id).where(
                        ConversationDocument.conversation_id == conversation_id,
                        ConversationDocument.owner_user_id == owner,
                    )
                )
            ).all()
        )
        if (
            selected != set(result.document_ids)
            or len(documents) != len(result.document_ids)
            or {doc.active_version_id for doc in documents} != set(result.versions)
        ):
            raise DocumentServiceError(
                "Phạm vi tài liệu đã thay đổi. Hãy hỏi lại.",
                status_code=409,
                code="document_scope_changed",
            )


async def bind_document_citations(session, *, message, owner, result):
    for hit in result.evidence:
        session.add(
            DocumentCitation(
                message_id=message.id,
                conversation_id=message.conversation_id,
                owner_user_id=owner,
                citation_id=hit.citation_id,
                document_id=hit.document_id,
                version_id=hit.version_id,
                chunk_id=hit.chunk_id,
                retrieval_config_version=hit.retrieval_config_version,
            )
        )


def citation_statement(owner, conversation_id, message_ids, *, source_id=None):
    # Saved citations remain readable when a file is unselected, but disappear on library deletion.
    statement = (
        select(
            DocumentCitation.message_id,
            DocumentCitation.citation_id,
            DocumentChunk.document_id,
            DocumentChunk.version_id,
            DocumentChunk.id.label("chunk_id"),
            Document.title,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            DocumentChunk.section,
            DocumentChunk.start_offset,
            DocumentChunk.end_offset,
        )
        .select_from(DocumentCitation)
        .join(
            DocumentChunk,
            (DocumentChunk.id == DocumentCitation.chunk_id)
            & (DocumentChunk.version_id == DocumentCitation.version_id)
            & (DocumentChunk.document_id == DocumentCitation.document_id)
            & (DocumentChunk.owner_user_id == owner),
        )
        .join(
            Document,
            (Document.id == DocumentCitation.document_id) & (Document.owner_user_id == owner),
        )
        .join(
            Conversation,
            (Conversation.id == DocumentCitation.conversation_id) & (Conversation.user_id == owner),
        )
        .where(
            DocumentCitation.owner_user_id == owner,
            DocumentCitation.conversation_id == conversation_id,
            DocumentCitation.message_id.in_(message_ids),
            Document.deleted_at.is_(None),
            Document.status != "deleting",
        )
    )
    if source_id is not None:
        statement = statement.where(DocumentCitation.citation_id == source_id).add_columns(
            DocumentChunk.text
        )
    return statement.order_by(DocumentCitation.created_at, DocumentCitation.id)


async def message_citations(session, owner, conversation_id, message_ids, *, source_id=None):
    rows = (
        await session.execute(
            citation_statement(owner, conversation_id, message_ids, source_id=source_id)
        )
    ).all()
    return [dict(row._mapping) for row in rows]
