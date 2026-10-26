import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack
from datetime import datetime
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from langgraph.checkpoint.base import BaseCheckpointSaver

from agent_core.context_budget import ContextBlock, ContextTokenBudget
from agent_core.contracts import RoutingDecision
from agent_core.graph import (
    AgentLimits,
    AgentLoopError,
    AgentLoopEvent,
    AgentQuotaLimitError,
    AgentStreamItem,
    stream_agent_graph,
)
from agent_core.history_compression import HistoryCompressionPolicy
from agent_core.llm.deepseek import LLMConfigurationError, LLMProviderError
from agent_core.llm.types import ChatModel, TokenUsage
from agent_core.prompts import build_chat_messages
from agent_core.routing.query_router import route_query_with_jev
from agent_core.routing.shortlist import SelectionPolicyError
from agent_core.routing.types import DecisionProvider, DecisionProviderError
from agent_core.skills import SkillRegistry, SkillSelectionConfig
from agent_core.tools import ToolContext, ToolRegistry, ToolSelectionConfig
from agent_core.tools.types import ToolAuthorizationError
from backend.core.config import get_settings
from backend.database import SessionFactory
from backend.dependencies import CurrentUser, DbSession
from backend.dependencies_decisions import DecisionProviderDependency
from backend.dependencies_llm import ChatModelDependency
from backend.dependencies_skills import SkillRegistryDependency
from backend.dependencies_tools import ToolRegistryDependency
from backend.models import AgentRun, Message
from backend.schemas.chat import (
    AgentRunTimelineResponse,
    ConversationCreate,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
    ToolApprovalDecision,
    ToolApprovalResponse,
)
from backend.schemas.document import (
    ConversationDocumentSelection,
    ConversationDocumentsResponse,
    DocumentCitationResponse,
    DocumentCitationSourceResponse,
    RagSearchMode,
)
from backend.services.agent_runs import (
    AgentRunRecorder,
    AgentRunResumeError,
    complete_agent_run,
    create_agent_run,
    fail_agent_run,
    list_agent_run_timelines,
    prepare_agent_run_resume,
)
from backend.services.chat import (
    ConversationBusyError,
    add_user_message,
    create_conversation,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
    list_messages,
)
from backend.services.document_qa import DocumentQAResult, message_citations, stream_document_qa
from backend.services.documents import (
    DocumentServiceError,
    get_conversation_documents,
    set_conversation_documents,
)
from backend.services.history_compression import prepare_history_context
from backend.services.langgraph_checkpoints import get_langgraph_checkpointer
from backend.services.langgraph_memory_store import (
    ScopedUserMemoryStore,
    UserMemorySearchPolicy,
)
from backend.services.langsmith_tracing import get_langsmith_tracing
from backend.services.memory import (
    extract_and_store_user_message_memories,
)
from backend.services.plugin_runtime import plugin_runtime
from backend.services.tool_approvals import (
    ToolApprovalForbiddenError,
    ToolApprovalNotFoundError,
    tool_approval_manager,
)
from backend.services.usage_limits import (
    DAY_SECONDS,
    MINUTE_SECONDS,
    AgentQuotaGuard,
    UsageLimit,
    UsageLimitExceeded,
    consume_usage_limits,
    ensure_usage_available,
)

router = APIRouter(prefix="/api/conversations", tags=["chat"])
logger = logging.getLogger(__name__)


def sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def agent_status_payload(event: AgentLoopEvent) -> dict[str, object]:
    tool_name = event.tool_call.name if event.tool_call is not None else "không xác định"
    status_messages = {
        "model_started": "Agent đang xử lý yêu cầu.",
        "model_completed": (
            "Agent đã yêu cầu sử dụng tool."
            if event.finish_reason == "tool_calls"
            else "Agent đang hoàn thiện câu trả lời."
        ),
        "model_failed": "Không thể nhận kết quả từ model.",
        "tool_started": f"Đang chạy tool {tool_name}.",
        "tool_approval_required": f"Tool {tool_name} đang chờ bạn phê duyệt.",
        "tool_approval_resolved": (
            f"Bạn đã cho phép tool {tool_name}."
            if event.approved
            else f"Bạn đã từ chối tool {tool_name}."
        ),
        "tool_completed": (
            f"Tool {tool_name} đã chạy xong."
            if event.tool_result and not event.tool_result.is_error
            else f"Tool {tool_name} chạy thất bại."
        ),
    }
    payload: dict[str, object] = {
        "status": event.kind,
        "step": event.step_number,
        "message": status_messages[event.kind],
    }
    if event.duration_ms is not None:
        payload["duration_ms"] = event.duration_ms
    if event.usage is not None:
        payload["total_tokens"] = event.usage.total_tokens
        payload["cached_input_tokens"] = event.usage.cached_input_tokens
        payload["cache_miss_input_tokens"] = event.usage.cache_miss_input_tokens
        payload["prompt_cache_hit_rate"] = event.usage.prompt_cache_hit_rate
    if event.tool_call is not None:
        payload["tool"] = {
            "call_id": event.tool_call.id,
            "name": event.tool_call.name,
        }
    if event.tool_result is not None:
        payload["success"] = not event.tool_result.is_error
        if event.tool_result.error_code:
            payload["error_code"] = event.tool_result.error_code
    if event.approval_id is not None:
        payload["approval"] = {
            "id": event.approval_id,
            "arguments": dict(event.tool_call.arguments) if event.tool_call else {},
            "approved": event.approved,
        }
    if event.context_usage is not None:
        context_usage = event.context_usage
        if context_usage.prompt_version == "document-qa-plan-v1":
            payload["message"] = (
                "Đang phân tích câu hỏi và ngữ cảnh."
                if event.kind == "model_started"
                else "Đã phân tích câu hỏi và ngữ cảnh."
                if event.kind == "model_completed"
                else "Không hoàn tất phân tích câu hỏi."
            )
        elif (context_usage.prompt_version or "").startswith("document-"):
            payload["message"] = (
                "Đang tổng hợp nội dung dựa trên tài liệu."
                if event.kind == "model_started"
                else "Đã tạo kết quả từ tài liệu."
                if event.kind == "model_completed"
                else "Không hoàn tất kết quả từ tài liệu."
            )
        payload["context"] = {
            "estimator": context_usage.estimator,
            "system_tokens": context_usage.system_tokens,
            "memory_tokens": context_usage.memory_tokens,
            "history_tokens": context_usage.history_tokens,
            "summary_tokens": context_usage.summary_tokens,
            "tool_tokens": context_usage.tool_tokens,
            "rag_tokens": context_usage.rag_tokens,
            "total_tokens": context_usage.total_tokens,
            "budget_tokens": context_usage.max_input_tokens,
            "trimmed_messages": context_usage.trimmed_messages,
            "prompt_version": context_usage.prompt_version,
            "prefix_fingerprint": context_usage.prefix_fingerprint,
            "available_tool_count": context_usage.available_tool_count,
            "loaded_tool_count": context_usage.loaded_tool_count,
            "tool_selection_mode": context_usage.tool_selection_mode,
            "status_tokens": context_usage.status_tokens,
            "skill_tokens": context_usage.skill_tokens,
            "selected_skill": context_usage.selected_skill,
        }
        payload["message"] = (
            f"{payload['message']} Context ước tính: "
            f"{context_usage.total_tokens}/{context_usage.max_input_tokens} token. "
            f"Tools: {context_usage.loaded_tool_count}/"
            f"{context_usage.available_tool_count}."
            + (f" Skill: {context_usage.selected_skill}." if context_usage.selected_skill else "")
        )
    if event.status_block is not None:
        payload["agent_status"] = event.status_block.to_payload()
    return payload


def agent_stream_sse(item: AgentStreamItem) -> str | None:
    """Map the stable agent stream contract to the existing browser SSE protocol."""
    if isinstance(item, AgentLoopEvent):
        if item.kind == "pipeline_progress":
            return sse_event("progress", item.progress or {})
        return sse_event("agent_status", agent_status_payload(item))
    if item.content:
        return sse_event("delta", {"content": item.content})
    return None


async def stream_assistant_response(
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    user_message_id: UUID,
    user_id: UUID,
    user_message_content: str,
    user_message_observed_at: datetime,
    model_messages: list[dict[str, str]],
    model: ChatModel,
    registry: ToolRegistry,
    skill_registry: SkillRegistry,
    tool_context: ToolContext,
    limits: AgentLimits,
    context_budget: ContextTokenBudget,
    tool_selection_config: ToolSelectionConfig,
    skill_selection_config: SkillSelectionConfig,
    history_summary_context: Sequence[ContextBlock],
    graph_checkpointer: BaseCheckpointSaver[Any],
    resume: bool = False,
    decision_provider: DecisionProvider | None = None,
    query_messages: Sequence[dict[str, str]] | None = None,
    run_created_at: datetime | None = None,
    rag_search_mode: RagSearchMode = "fast",
) -> AsyncIterator[str]:
    recorder = AgentRunRecorder(
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        provider=model.provider,
        model_name=model.model,
    )
    yield sse_event(
        "start",
        {
            "model": model.model,
            "run_id": str(agent_run_id),
            "started_at": run_created_at.isoformat() if run_created_at else None,
            "rag_search_mode": rag_search_mode,
        },
    )
    parts: list[str] = []
    usage = TokenUsage()
    settings = get_settings()
    quota_guard = AgentQuotaGuard(
        user_id=user_id,
        token_limit=settings.agent_tokens_per_day,
        tool_call_limit=settings.agent_tool_calls_per_day,
    )

    async def record_event(event: AgentLoopEvent) -> None:
        await recorder(event)
        await quota_guard.record_event(event)

    async def extract_memories() -> None:
        if not resume and settings.memory_extraction_enabled:
            try:
                await quota_guard.before_execution("model")
                extraction_summary = await extract_and_store_user_message_memories(
                    model=model,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message_id=user_message_id,
                    user_message_content=user_message_content,
                    observed_at=user_message_observed_at,
                    min_confidence=Decimal(str(settings.memory_extraction_min_confidence)),
                    timeout_seconds=settings.memory_extraction_timeout_seconds,
                )
                await quota_guard.record_tokens(extraction_summary.total_tokens)
            except AgentQuotaLimitError:
                raise
            except Exception:
                logger.exception("Không thể trích xuất memory từ user message")

    routing_flags = settings.v2_features
    plugin_stack = AsyncExitStack()
    document_result = DocumentQAResult()
    used_document_qa = False
    routing_started = perf_counter()

    async def routing_progress(state):
        row = {
            "id": "routing",
            "label": "Nhận và phân loại yêu cầu",
            "state": state,
            "duration_ms": round((perf_counter() - routing_started) * 1000),
            "elapsed_ms": round((perf_counter() - routing_started) * 1000),
        }
        document_result.trace["progress"] = [row]
        event = AgentLoopEvent(kind="pipeline_progress", step_number=0, progress=row)
        await recorder(event)
        return agent_stream_sse(event)

    async def document_qa_handler():
        nonlocal used_document_qa
        used_document_qa = True
        async for item in stream_document_qa(
            model=model,
            owner=user_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            query=user_message_content,
            run_id=agent_run_id,
            recorder=recorder,
            quota_guard=quota_guard,
            limits=limits,
            context_budget=context_budget,
            result=document_result,
            search_mode=rag_search_mode,
        ):
            yield item

    try:
        yield await routing_progress("running")
        if decision_provider is not None and hasattr(decision_provider, "bind_run"):
            try:
                await decision_provider.bind_run(
                    user_id=user_id,
                    run_id=agent_run_id,
                    token_recorder=quota_guard.record_tokens,
                )
            except Exception:
                if not getattr(decision_provider, "shadow", False):
                    raise
                logger.warning("Shadow routing unavailable; baseline execution continues")
                decision_provider = None
        query_decision = None
        if (
            not resume
            and routing_flags.is_enabled("routing")
            and decision_provider is not None
            and not getattr(decision_provider, "shadow", False)
        ):
            await quota_guard.before_execution("model")
            selected_documents = []
            if routing_flags.is_enabled("rag"):
                async with SessionFactory() as document_session:
                    selected = await get_conversation_documents(
                        document_session, user_id, conversation_id
                    )
                    selected_documents = [
                        {"title": doc.title[:200], "status": doc.status} for doc in selected.items
                    ]
            query_decision = await route_query_with_jev(
                query_messages if query_messages is not None else model_messages,
                decision_provider,
                selected_documents=selected_documents,
            )
        runtime = await plugin_stack.enter_async_context(
            plugin_runtime(registry, skill_registry, tool_context)
        )
        yield await routing_progress("completed")
        if not routing_flags.is_enabled("routing"):
            await extract_memories()
        memory_store = ScopedUserMemoryStore(
            user_id=user_id,
            policy=UserMemorySearchPolicy(
                token_budget=context_budget.memory_tokens,
                min_confidence=Decimal(str(settings.memory_retrieval_min_confidence)),
                max_items=settings.memory_retrieval_max_items,
                candidate_limit=settings.memory_retrieval_candidate_limit,
                excluded_source_message_id=user_message_id,
            ),
        )
        trace_config = get_langsmith_tracing().trace_config(
            run_id=agent_run_id,
            user_id=user_id,
            provider=model.provider,
            model=model.model,
        )
        async for item in stream_agent_graph(
            model=model,
            messages=model_messages,
            registry=runtime.registry,
            context=runtime.context,
            event_handler=record_event,
            approval_handler=tool_approval_manager.wait_for_decision,
            limits=limits,
            context_budget=context_budget,
            tool_selection_config=tool_selection_config,
            skill_registry=runtime.skills,
            skill_selection_config=skill_selection_config,
            history_summary_context=history_summary_context,
            resume=resume,
            execution_guard=quota_guard.before_execution,
            graph_checkpointer=graph_checkpointer,
            memory_store=memory_store,
            trace_config=trace_config,
            routing_flags=routing_flags,
            decision_provider=decision_provider,
            query_routing_decision=query_decision,
            routing_messages=query_messages,
            document_qa_handler=document_qa_handler if routing_flags.is_enabled("rag") else None,
        ):
            if isinstance(item, RoutingDecision):
                yield sse_event("routing", {**item.model_dump(mode="json"), "resumed": resume})
                if item.route in {"chat", "tool_task"}:
                    await extract_memories()
                continue
            encoded_event = agent_stream_sse(item)
            if isinstance(item, AgentLoopEvent):
                if encoded_event is not None:
                    yield encoded_event
                continue
            chunk = item
            if chunk.content:
                parts.append(chunk.content)
                if encoded_event is not None:
                    yield encoded_event
            if chunk.usage is not None:
                usage = chunk.usage

        content = "".join(parts).strip()
        if not content:
            raise LLMProviderError("DeepSeek không trả về nội dung")

        message = await complete_agent_run(
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
            content=content,
            document_result=document_result if used_document_qa else None,
            document_owner=user_id if used_document_qa else None,
        )
        yield sse_event(
            "done",
            {
                "message_id": str(message.id),
                "citations": [
                    hit.model_dump(mode="json", exclude={"text"})
                    for hit in document_result.evidence
                ]
                if used_document_qa
                else [],
                "usage": {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "cached_input_tokens": usage.cached_input_tokens,
                    "cache_miss_input_tokens": usage.cache_miss_input_tokens,
                    "prompt_cache_hit_rate": usage.prompt_cache_hit_rate,
                },
            },
        )
    except asyncio.CancelledError:
        await fail_agent_run(
            agent_run_id,
            "cancelled",
            "Client đã ngắt kết nối",
            stop_reason="client_disconnected",
        )
        raise
    except DocumentServiceError as exc:
        await fail_agent_run(agent_run_id, "failed", exc.detail, stop_reason=exc.code)
        yield sse_event("error", {"message": exc.detail, "code": exc.code, "resumable": False})
    except LLMConfigurationError as exc:
        await fail_agent_run(
            agent_run_id,
            "failed",
            str(exc),
            stop_reason="missing_api_key",
        )
        yield sse_event("error", {"message": str(exc), "code": "missing_api_key"})
    except LLMProviderError as exc:
        await fail_agent_run(
            agent_run_id,
            "failed",
            str(exc),
            stop_reason="llm_provider_error",
        )
        yield sse_event(
            "error",
            {"message": str(exc), "code": "llm_provider_error"},
        )
    except ToolAuthorizationError as exc:
        await fail_agent_run(agent_run_id, "failed", str(exc), stop_reason=exc.code)
        yield sse_event("error", {"message": str(exc), "code": exc.code})
    except AgentLoopError as exc:
        await fail_agent_run(
            agent_run_id,
            "failed",
            str(exc),
            stop_reason=exc.code,
        )
        yield sse_event(
            "error", {"message": str(exc), "code": exc.code, "resumable": not used_document_qa}
        )
    except SelectionPolicyError as exc:
        await fail_agent_run(agent_run_id, "failed", str(exc), stop_reason=exc.code)
        yield sse_event("error", {"message": str(exc), "code": exc.code})
    except DecisionProviderError as exc:
        content = "Không thể phân loại yêu cầu bằng JEV. Vui lòng thử lại."
        await fail_agent_run(agent_run_id, "failed", content, stop_reason=exc.code)
        yield sse_event("error", {"message": content, "code": exc.code})
    except Exception as exc:
        logger.exception("Agent run không hoàn tất")
        await fail_agent_run(
            agent_run_id,
            "failed",
            str(exc),
            stop_reason="llm_error",
        )
        yield sse_event(
            "error",
            {
                "message": "Không thể hoàn tất yêu cầu. Vui lòng thử lại.",
                "code": "llm_error",
            },
        )
    finally:
        try:
            await plugin_stack.aclose()
        finally:
            await model.aclose()


@router.get("", response_model=list[ConversationResponse])
async def get_conversations(session: DbSession, user: CurrentUser) -> list[ConversationResponse]:
    conversations = await list_conversations(session, user.id)
    return [ConversationResponse.model_validate(item) for item in conversations]


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def post_conversation(
    data: ConversationCreate, session: DbSession, user: CurrentUser
) -> ConversationResponse:
    conversation = await create_conversation(session, user.id, data.title)
    return ConversationResponse.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_conversation(
    conversation_id: UUID, session: DbSession, user: CurrentUser
) -> Response:
    try:
        deleted = await delete_conversation(
            session,
            conversation_id=conversation_id,
            user_id=user.id,
            checkpointer=get_langgraph_checkpointer(),
        )
    except ConversationBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_conversation_messages(
    conversation_id: UUID, session: DbSession, user: CurrentUser, response: Response
) -> list[MessageResponse]:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hội thoại",
        )
    messages = await list_messages(session, conversation.id)
    response.headers["Cache-Control"] = "private, no-store"
    citations = await message_citations(session, user.id, conversation.id, [m.id for m in messages])
    by_message = {}
    for citation in citations:
        by_message.setdefault(citation["message_id"], []).append(
            DocumentCitationResponse.model_validate(citation)
        )
    return [
        MessageResponse.model_validate(item).model_copy(
            update={"citations": by_message.get(item.id, [])}
        )
        for item in messages
    ]


@router.get(
    "/{conversation_id}/messages/{message_id}/citations/{citation_id}",
    response_model=DocumentCitationSourceResponse,
)
async def get_citation_source(
    conversation_id: UUID,
    message_id: UUID,
    citation_id: str,
    session: DbSession,
    user: CurrentUser,
    response: Response,
):
    response.headers["Cache-Control"] = "private, no-store"
    rows = await message_citations(
        session, user.id, conversation_id, [message_id], source_id=citation_id
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Nguồn không còn khả dụng hoặc đã bị xóa.")
    return rows[0]


@router.get("/{conversation_id}/documents", response_model=ConversationDocumentsResponse)
async def get_selected_documents(
    conversation_id: UUID, session: DbSession, user: CurrentUser
) -> ConversationDocumentsResponse:
    return await get_conversation_documents(session, user.id, conversation_id)


@router.put("/{conversation_id}/documents", response_model=ConversationDocumentsResponse)
async def put_selected_documents(
    conversation_id: UUID,
    data: ConversationDocumentSelection,
    session: DbSession,
    user: CurrentUser,
) -> ConversationDocumentsResponse:
    return await set_conversation_documents(session, user.id, conversation_id, data.document_ids)


@router.get("/{conversation_id}/runs", response_model=list[AgentRunTimelineResponse])
async def get_conversation_runs(
    conversation_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> list[AgentRunTimelineResponse]:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hội thoại",
        )
    timelines = await list_agent_run_timelines(
        session,
        conversation.id,
        checkpointer=get_langgraph_checkpointer(),
    )
    return [AgentRunTimelineResponse.model_validate(item) for item in timelines]


@router.post(
    "/{conversation_id}/runs/{run_id}/approvals/{approval_id}",
    response_model=ToolApprovalResponse,
)
async def decide_tool_approval(
    conversation_id: UUID,
    run_id: UUID,
    approval_id: UUID,
    data: ToolApprovalDecision,
    session: DbSession,
    user: CurrentUser,
) -> ToolApprovalResponse:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hội thoại",
        )
    try:
        tool_approval_manager.resolve(
            approval_id=approval_id,
            run_id=run_id,
            conversation_id=conversation.id,
            user_id=user.id,
            approved=data.approved,
        )
    except ToolApprovalForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ToolApprovalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ToolApprovalResponse(approval_id=approval_id, approved=data.approved)


@router.post("/{conversation_id}/runs/{run_id}/resume")
async def resume_agent_run(
    conversation_id: UUID,
    run_id: UUID,
    session: DbSession,
    user: CurrentUser,
    model: ChatModelDependency,
    registry: ToolRegistryDependency,
    skill_registry: SkillRegistryDependency,
    decision_provider: DecisionProviderDependency,
) -> StreamingResponse:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    if conversation is None:
        await model.aclose()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hội thoại",
        )
    settings = get_settings()
    try:
        await ensure_usage_available(
            user.id,
            metric="llm_token",
            limit=settings.agent_tokens_per_day,
            window_seconds=DAY_SECONDS,
        )
        await consume_usage_limits(
            user.id,
            (
                UsageLimit(
                    metric="agent_request",
                    limit=settings.agent_requests_per_minute,
                    window_seconds=MINUTE_SECONDS,
                ),
            ),
        )
    except UsageLimitExceeded as exc:
        await model.aclose()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    graph_checkpointer = get_langgraph_checkpointer()
    try:
        await prepare_agent_run_resume(
            session,
            agent_run_id=run_id,
            conversation_id=conversation.id,
            checkpointer=graph_checkpointer,
        )
    except AgentRunResumeError as exc:
        await model.aclose()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    agent_run = await session.get(AgentRun, run_id)
    user_message = (
        await session.get(Message, agent_run.user_message_id)
        if agent_run is not None and agent_run.user_message_id is not None
        else None
    )
    if agent_run is None or user_message is None:
        await model.aclose()
        await fail_agent_run(
            run_id,
            "failed",
            "Không tìm thấy user message gốc để resume",
            stop_reason="resume_source_missing",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Không tìm thấy user message gốc để resume",
        )

    tool_context = ToolContext(
        user_id=user.id,
        conversation_id=conversation.id,
        user_role=user.role,
        run_id=agent_run.id,
    )
    limits = AgentLimits(
        max_steps=settings.agent_max_steps,
        max_total_tokens=settings.agent_max_total_tokens,
        max_duration_seconds=settings.agent_max_duration_seconds,
        max_identical_tool_calls=settings.agent_max_identical_tool_calls,
        max_repeated_observations=settings.agent_max_repeated_observations,
    )
    context_budget = ContextTokenBudget(
        max_input_tokens=settings.context_max_input_tokens,
        system_tokens=settings.context_system_token_budget,
        memory_tokens=settings.context_memory_token_budget,
        history_tokens=settings.context_history_token_budget,
        tool_tokens=settings.context_tool_token_budget,
        rag_tokens=settings.context_rag_token_budget,
    )
    tool_selection_config = ToolSelectionConfig(
        lazy_load_threshold=settings.tool_lazy_load_threshold,
        max_loaded_tools=settings.tool_max_loaded,
    )
    skill_selection_config = SkillSelectionConfig(
        minimum_score=settings.skill_selection_min_score,
        ambiguity_margin=settings.skill_selection_ambiguity_margin,
    )
    return StreamingResponse(
        stream_assistant_response(
            agent_run_id=agent_run.id,
            conversation_id=conversation.id,
            run_created_at=agent_run.last_resumed_at or agent_run.created_at,
            user_message_id=user_message.id,
            user_id=user.id,
            user_message_content=user_message.content,
            user_message_observed_at=user_message.created_at,
            model_messages=[],
            model=model,
            registry=registry,
            skill_registry=skill_registry,
            tool_context=tool_context,
            limits=limits,
            context_budget=context_budget,
            tool_selection_config=tool_selection_config,
            skill_selection_config=skill_selection_config,
            history_summary_context=(),
            graph_checkpointer=graph_checkpointer,
            resume=True,
            rag_search_mode=(agent_run.rag_trace or {}).get("search_mode", "fast"),
            decision_provider=decision_provider,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/messages")
async def post_message(
    conversation_id: UUID,
    data: MessageCreate,
    session: DbSession,
    user: CurrentUser,
    model: ChatModelDependency,
    registry: ToolRegistryDependency,
    skill_registry: SkillRegistryDependency,
    decision_provider: DecisionProviderDependency,
) -> StreamingResponse:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    if conversation is None:
        await model.aclose()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy hội thoại",
        )

    settings = get_settings()
    try:
        await ensure_usage_available(
            user.id,
            metric="llm_token",
            limit=settings.agent_tokens_per_day,
            window_seconds=DAY_SECONDS,
        )
        await consume_usage_limits(
            user.id,
            (
                UsageLimit(
                    metric="agent_request",
                    limit=settings.agent_requests_per_minute,
                    window_seconds=MINUTE_SECONDS,
                ),
                UsageLimit(
                    metric="agent_run",
                    limit=settings.agent_runs_per_day,
                    window_seconds=DAY_SECONDS,
                ),
            ),
        )
    except UsageLimitExceeded as exc:
        await model.aclose()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    user_message = await add_user_message(session, conversation, data.content)
    history_context = await prepare_history_context(
        session,
        conversation.id,
        HistoryCompressionPolicy(
            recent_message_limit=settings.chat_history_limit,
            batch_size=settings.history_compression_batch_size,
            merge_factor=settings.history_summary_merge_factor,
            max_summary_chars=settings.history_summary_max_chars,
        ),
    )
    model_messages = build_chat_messages(history_context.messages)
    agent_run = await create_agent_run(
        session,
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        rag_search_mode=data.rag_search_mode,
    )
    tool_context = ToolContext(
        user_id=user.id,
        conversation_id=conversation.id,
        user_role=user.role,
        run_id=agent_run.id,
    )
    limits = AgentLimits(
        max_steps=settings.agent_max_steps,
        max_total_tokens=settings.agent_max_total_tokens,
        max_duration_seconds=settings.agent_max_duration_seconds,
        max_identical_tool_calls=settings.agent_max_identical_tool_calls,
        max_repeated_observations=settings.agent_max_repeated_observations,
    )
    context_budget = ContextTokenBudget(
        max_input_tokens=settings.context_max_input_tokens,
        system_tokens=settings.context_system_token_budget,
        memory_tokens=settings.context_memory_token_budget,
        history_tokens=settings.context_history_token_budget,
        tool_tokens=settings.context_tool_token_budget,
        rag_tokens=settings.context_rag_token_budget,
    )
    tool_selection_config = ToolSelectionConfig(
        lazy_load_threshold=settings.tool_lazy_load_threshold,
        max_loaded_tools=settings.tool_max_loaded,
    )
    skill_selection_config = SkillSelectionConfig(
        minimum_score=settings.skill_selection_min_score,
        ambiguity_margin=settings.skill_selection_ambiguity_margin,
    )

    return StreamingResponse(
        stream_assistant_response(
            agent_run_id=agent_run.id,
            conversation_id=conversation.id,
            run_created_at=agent_run.last_resumed_at or agent_run.created_at,
            user_message_id=user_message.id,
            user_id=user.id,
            user_message_content=user_message.content,
            user_message_observed_at=user_message.created_at,
            model_messages=model_messages,
            model=model,
            registry=registry,
            skill_registry=skill_registry,
            tool_context=tool_context,
            limits=limits,
            context_budget=context_budget,
            tool_selection_config=tool_selection_config,
            skill_selection_config=skill_selection_config,
            history_summary_context=history_context.summaries,
            query_messages=[
                {"role": item.role, "content": item.content}
                for item in history_context.messages[-settings.chat_history_limit :]
            ],
            graph_checkpointer=get_langgraph_checkpointer(),
            decision_provider=decision_provider,
            rag_search_mode=data.rag_search_mode,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
