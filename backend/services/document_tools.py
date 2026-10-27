"""Run-scoped document bridge: private evidence, references and bounded summary delegation."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy import select

from agent_core.graph import AgentLimits, AgentLoopEvent
from agent_core.llm.types import LLMChunk, TokenUsage
from agent_core.rag.qa import QueryPlan
from agent_core.rag.retrieval import evidence_block, pack_evidence
from agent_core.tools import ToolResult
from agent_core.tools.documents import DocumentQueryInput
from agent_core.tools.types import ToolAuthorizationError
from backend.database import SessionFactory
from backend.dependencies_llm import create_chat_model
from backend.models import AgentRun, Conversation, DocumentChunk, RunStep, ToolCall
from backend.schemas.document import DocumentSearchRequest
from backend.services.document_qa import DocumentQAResult, stream_document_qa
from backend.services.document_query import retrieve_document_evidence
from backend.services.document_retrieval import _hit, _scope, scoped_chunks
from backend.services.documents import DocumentServiceError

DOCUMENT_TOOLS = frozenset({"search_documents", "summarize_documents"})
STAGES = {
    "scope": "Đọc tài liệu đã chọn",
    "embedding": "Tạo embedding câu hỏi",
    "embedding_model": "Nạp model embedding",
    "dense": "Tìm kiếm ngữ nghĩa trong tài liệu",
    "lexical": "Tìm kiếm từ khóa trong tài liệu",
    "fusion": "Gộp kết quả tìm kiếm",
    "rerank": "Xếp hạng lại các đoạn ứng viên",
    "reranker_model": "Nạp model reranker",
    "evidence": "Chọn các đoạn nguồn cho câu trả lời",
}


class DocumentToolSession:
    def __init__(
        self,
        *,
        owner,
        conversation_id,
        message_id,
        run_id,
        result,
        search_mode,
        context_budget,
        quota_guard,
        recorder,
    ):
        self.owner, self.conversation_id = owner, conversation_id
        self.message_id, self.run_id = message_id, run_id
        self.result, self.search_mode = result, search_mode
        self.context_budget, self.quota_guard, self.recorder = context_budget, quota_guard, recorder
        self.used = False
        self.actions = []
        self.sequence = 0
        self.current_chunks = set()
        self.summary_chunks = set()
        self.request = DocumentSearchRequest(query="scope", conversation_id=conversation_id)

    @property
    def scope_data(self):
        return {
            "document_ids": [str(v) for v in self.result.document_ids],
            "versions": {str(k): v for k, v in self.result.versions.items()},
        }

    async def prepare(self, *, resume=False):
        async with SessionFactory() as session:
            versions = await _scope(session, self.owner, self.request)
            self.result.document_ids = sorted([v.document_id for v in versions], key=str)
            self.result.versions = {v.id: v.pipeline_hash for v in versions}
            run = await session.scalar(
                select(AgentRun)
                .join(Conversation)
                .where(
                    AgentRun.id == self.run_id,
                    Conversation.user_id == self.owner,
                )
            )
            if run is None:
                raise DocumentServiceError(
                    "Lần chạy không khả dụng.", code="document_run_unavailable"
                )
            previous = dict(run.rag_trace or {})
            if resume and previous.get("document_scope") not in (None, self.scope_data):
                raise DocumentServiceError(
                    "Phạm vi tài liệu đã thay đổi.", code="document_scope_changed"
                )
            self.actions = list(previous.get("document_actions", [])) if resume else []
            self.used = bool(previous.get("document_search_used")) if resume else False
            if not resume:
                # Pin before model/tool execution, including a crash before ToolCall completion.
                run.rag_trace = {**previous, "document_scope": self.scope_data}
                await session.commit()
                return
            # Restore only durable host-tool outputs from this owner/run; model messages
            # never create references. Read current chunk text after checking the scope pin.
            calls = (
                await session.scalars(
                    select(ToolCall)
                    .join(RunStep)
                    .join(AgentRun)
                    .join(Conversation)
                    .where(
                        AgentRun.id == self.run_id,
                        Conversation.user_id == self.owner,
                        ToolCall.tool_name.in_(DOCUMENT_TOOLS),
                    )
                    .order_by(ToolCall.created_at)
                )
            ).all()
            for call in calls:
                data = call.result_data or {}
                if not data.get("document_scope"):
                    continue
                if data["document_scope"] != self.scope_data:
                    raise DocumentServiceError(
                        "Phạm vi tài liệu đã thay đổi. Hãy gửi lại câu hỏi.",
                        code="document_scope_changed",
                        status_code=409,
                    )
                self.used = True
                self.sequence += 1
                refs = data.get("document_references", [])
                if call.status == "completed" and call.tool_name == "search_documents":
                    self.current_chunks = {UUID(r["chunk_id"]) for r in refs}
                if call.status == "completed" and call.tool_name == "summarize_documents":
                    self.summary_chunks.update(UUID(r["chunk_id"]) for r in refs)
                if not refs:
                    continue
                rows = (
                    await session.execute(
                        scoped_chunks(self.owner, list(self.result.versions)).where(
                            DocumentChunk.id.in_([UUID(ref["chunk_id"]) for ref in refs])
                        )
                    )
                ).all()
                found = {str(chunk.id): (chunk, title) for chunk, title in rows}
                if set(found) != {ref["chunk_id"] for ref in refs}:
                    raise DocumentServiceError(
                        "Nguồn tài liệu không còn khả dụng.",
                        code="document_scope_changed",
                        status_code=409,
                    )
                for ref in refs:
                    chunk, title = found[ref["chunk_id"]]
                    self.accept(
                        [
                            _hit(chunk, title, 1.0, "coverage").model_copy(
                                update={
                                    "retrieval_config_version": ref["retrieval_config_version"],
                                }
                            )
                        ]
                    )

    async def validate(self):
        async with SessionFactory() as session:
            versions = await _scope(session, self.owner, self.request)
            if {v.id: v.pipeline_hash for v in versions} != self.result.versions or sorted(
                [v.document_id for v in versions], key=str
            ) != self.result.document_ids:
                raise DocumentServiceError(
                    "Phạm vi tài liệu đã thay đổi. Hãy gửi lại câu hỏi.",
                    code="document_scope_changed",
                    status_code=409,
                )

    def check_context(self, context):
        if (context.user_id, context.conversation_id, context.run_id) != (
            self.owner,
            self.conversation_id,
            self.run_id,
        ):
            raise ToolAuthorizationError("tool_permission_denied")

    def accept(self, hits):
        if any(
            h.document_id not in self.result.document_ids
            or h.version_id not in self.result.versions
            for h in hits
        ):
            raise DocumentServiceError(
                "Nguồn ngoài phạm vi tài liệu.", code="document_scope_changed"
            )
        existing = {hit.chunk_id: hit for hit in self.result.evidence}
        accepted = []
        for hit in hits:
            if hit.chunk_id not in existing:
                hit = hit.model_copy(update={"citation_id": f"S{len(existing) + 1}"})
                existing[hit.chunk_id] = hit
                self.result.evidence.append(hit)
            accepted.append(existing[hit.chunk_id])
        return accepted

    async def persist(self):
        async with SessionFactory() as session:
            run = await session.scalar(
                select(AgentRun)
                .join(Conversation)
                .where(
                    AgentRun.id == self.run_id,
                    Conversation.user_id == self.owner,
                    AgentRun.status == "running",
                )
                .with_for_update(of=AgentRun)
            )
            if run is None:
                raise DocumentServiceError(
                    "Lần chạy không còn khả dụng.", code="document_run_unavailable"
                )
            trace = dict(run.rag_trace or {})
            trace.update(
                reference_policy="supplied_context",
                document_scope=self.scope_data,
                document_actions=self.actions,
                reference_sources=[h.citation_id for h in self.result.evidence],
                evidence=[
                    h.model_dump(mode="json", exclude={"text"}) for h in self.result.evidence
                ],
            )
            if self.used:
                trace["document_search_used"] = True
            run.rag_trace = trace
            self.result.trace = trace
            await session.commit()

    async def finalize(self):
        # Match references to the final bounded RAG context plus summary lineage.
        # Earlier retrieval attempts remain auditable through their ToolCall records.
        visible = self.current_chunks | self.summary_chunks
        self.result.evidence = [h for h in self.result.evidence if h.chunk_id in visible]
        await self.persist()

    async def _progress(self, context, prefix, stage, state, metadata=None):
        if not hasattr(self, "clocks"):
            self.clocks = {}
        key = f"{prefix}.{stage}"
        clock, stamp = self.clocks.setdefault(key, (perf_counter(), datetime.now(UTC).isoformat()))
        row = {
            "id": f"{prefix}.{stage}",
            "label": STAGES.get(stage, stage),
            "state": state,
            "duration_ms": round((perf_counter() - clock) * 1000),
            "started_at": stamp,
        }
        if stage == "scope":
            row["label"] += f" · {self.search_mode.title()}"
        if metadata and isinstance(metadata.get("count"), int):
            row["count"] = metadata["count"]
        if context.progress_callback:
            await context.progress_callback(row)

    async def search(self, context, arguments):
        self.check_context(context)
        query = DocumentQueryInput.model_validate(dict(arguments)).query
        self.used = True
        self.sequence += 1
        prefix = f"documents.{self.sequence}"
        await self.validate()
        await self._progress(context, prefix, "scope", "running")
        try:
            async with SessionFactory() as session:
                response = await retrieve_document_evidence(
                    session,
                    self.owner,
                    DocumentSearchRequest(
                        query=query,
                        conversation_id=self.conversation_id,
                        search_mode=self.search_mode,
                    ),
                    query_is_planned=True,
                    candidates_only=True,
                    on_progress=lambda stage, state, meta: self._progress(
                        context, prefix, stage, state, meta
                    ),
                )
            await self.validate()
            packed = pack_evidence(
                response.items, token_budget=self.context_budget.rag_tokens, label_sources=True
            )
            if response.items and not packed.items:
                raise DocumentServiceError(
                    "Budget context không chứa được đoạn nguồn nào.",
                    code="document_evidence_budget",
                    status_code=422,
                )
            hits = self.accept(packed.items)
            self.current_chunks = {h.chunk_id for h in hits}
            await self._progress(context, prefix, "scope", "completed")
            await self._progress(context, prefix, "evidence", "completed", {"count": len(hits)})
            self.actions.append(
                {
                    "tool": "search_documents",
                    "query": query,
                    "query_origin": "agent_standalone_no_extra_rewrite",
                    "search_mode": self.search_mode,
                    "retrieval": response.retrieval_metadata,
                    "selected_chunks": len(hits),
                    "estimated_tokens": packed.estimated_tokens,
                    "budget_skipped": packed.budget_skipped,
                }
            )
            await self.persist()
            return ToolResult.success(
                json.dumps(
                    {
                        "coverage": "retrieved_passages",
                        "source_labels": [
                            {
                                "source": h.citation_id,
                                "title": h.title,
                                "page_start": h.page_start,
                                "page_end": h.page_end,
                                "section": h.section,
                            }
                            for h in hits
                        ],
                        "notice": "Not exhaustive. Use evidence; acknowledge missing facts."
                        if hits
                        else "No matching passages. Do not invent document contents.",
                    },
                    ensure_ascii=False,
                ),
                data={
                    "document_scope": self.scope_data,
                    "document_references": [
                        h.model_dump(mode="json", exclude={"text"}) for h in hits
                    ],
                },
                context_blocks=tuple(evidence_block(h) for h in hits),
            )
        except DocumentServiceError as exc:
            await self._progress(context, prefix, "scope", "failed")
            self.actions.append({"tool": "search_documents", "error_code": exc.code})
            await self.persist()
            return ToolResult.failure(
                exc.detail, error_code=exc.code, data={"document_scope": self.scope_data}
            )

    async def summarize(self, context, arguments):
        self.check_context(context)
        query = DocumentQueryInput.model_validate(dict(arguments)).query
        self.used = True
        self.sequence += 1
        prefix = f"documents.{self.sequence}.summary"
        await self.validate()
        collected, child = TokenUsage(), DocumentQAResult()
        outer_guard = self.quota_guard

        class Collector:
            async def before_execution(self, action):
                await outer_guard.before_execution(action)
                # Known uncommitted child usage still counts toward the daily allowance.
                from backend.services.usage_limits import DAY_SECONDS, get_current_usage

                if not getattr(outer_guard, "active_reservation", None) and (
                    collected.total_tokens
                    + await get_current_usage(
                        outer_guard.user_id,
                        metric="llm_token",
                        window_seconds=DAY_SECONDS,
                    )
                    >= outer_guard.token_limit
                ):
                    from agent_core.graph import AgentQuotaLimitError

                    raise AgentQuotaLimitError("Đã chạm ngân sách token ngày.")

            async def record_tokens(self, amount):
                # Actual usage is collected from child model events and charged once
                # by the root tool_completed event; never charge it twice.
                pass

            async def __call__(self, event):
                nonlocal collected
                if event.kind in {"model_completed", "model_failed"} and event.usage:
                    collected += event.usage
                    active = getattr(outer_guard, "active_reservation", None)
                    if active is not None:
                        active.known_tokens = max(active.known_tokens, collected.total_tokens)

        async def keep_trace(trace):
            child.trace = trace

        data = {"document_scope": self.scope_data, "provider": None, "model": None}
        model, output, failure = None, [], None
        summary_hits = []
        started = perf_counter()
        try:
            # Reserve a caller model turn after the delegated workflow.
            steps = (context.remaining_steps if context.remaining_steps is not None else 7) - 1
            tokens = context.remaining_tokens if context.remaining_tokens is not None else 20_000
            seconds = context.remaining_seconds if context.remaining_seconds is not None else 120
            if steps < 1 or tokens < 1 or seconds <= 0:
                raise DocumentServiceError(
                    "Không đủ budget để tóm tắt và trả lời.", code="document_qa_budget"
                )
            model = create_chat_model()
            data.update(provider=model.provider, model=model.model)
            collector = Collector()
            async for item in stream_document_qa(
                model=model,
                owner=self.owner,
                conversation_id=self.conversation_id,
                message_id=self.message_id,
                query=query,
                run_id=self.run_id,
                recorder=collector,
                quota_guard=collector,
                limits=AgentLimits(
                    max_steps=steps, max_total_tokens=tokens, max_duration_seconds=seconds
                ),
                context_budget=self.context_budget,
                result=child,
                search_mode=self.search_mode,
                planned_query=QueryPlan(query=query, mode="summary"),
                persist_trace=keep_trace,
                preserve_limits=True,
            ):
                if isinstance(item, LLMChunk) and item.content:
                    output.append(item.content)
                elif isinstance(item, AgentLoopEvent) and item.kind == "pipeline_progress":
                    row = {**item.progress, "id": f"{prefix}.{item.progress['id']}"}
                    if context.progress_callback:
                        await context.progress_callback(row)
            await self.validate()
            summary_hits = self.accept(child.evidence)
            self.summary_chunks.update(h.chunk_id for h in summary_hits)
        except DocumentServiceError as exc:
            failure = (exc.code, exc.detail)
        except asyncio.CancelledError:
            if getattr(outer_guard, "active_reservation", None) is None:
                await outer_guard.record_tokens(collected.total_tokens)
            raise
        except Exception:
            failure = ("document_summary_failed", "Chưa hoàn tất tóm tắt tài liệu. Hãy thử lại.")
        finally:
            if model is not None:
                await model.aclose()
        data.update(
            usage=asdict(collected),
            internal_model_calls=len(child.trace.get("calls", [])),
            document_references=[h.model_dump(mode="json", exclude={"text"}) for h in summary_hits],
            coverage=child.trace.get("coverage", {}),
        )
        self.actions.append(
            {
                "tool": "summarize_documents",
                "query": query,
                "coverage": data["coverage"],
                "calls": child.trace.get("calls", []),
                "error_code": failure[0] if failure else None,
                "latency_ms": round((perf_counter() - started) * 1000),
            }
        )
        await self.persist()
        if failure:
            return ToolResult.failure(failure[1], error_code=failure[0], data=data, usage=collected)
        return ToolResult.success("\n".join(output), data=data, usage=collected)
