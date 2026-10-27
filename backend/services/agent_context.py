"""Small, owner-scoped execution audit; never reuse credentials or arbitrary tool payloads."""

import json
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select

from agent_core.context_budget import ContextBlock, estimate_tokens
from backend.database import SessionFactory
from backend.models import AgentRun, Conversation, RunStep, ToolCall


def execution_rows(records, token_budget=1_500):
    rows = []
    for tool, run in records:
        data = tool.result_data or {}
        row = {
            "tool": tool.tool_name,
            "status": tool.status,
            "run_status": run.status,
            "stop_reason": run.stop_reason,
            "error_code": tool.error_code,
            "finished_at": tool.finished_at.isoformat() if tool.finished_at else None,
        }
        # Only host web metadata is allowed; never copy arguments, email bodies,
        # Python stdout or arbitrary plugin result fields into later turns.
        if tool.tool_name == "search_web":
            sources = []
            for source in data.get("sources", [])[:4]:
                if not isinstance(source, dict):
                    continue
                url = source.get("url")
                if isinstance(url, str):
                    try:
                        parsed = urlsplit(url)
                    except ValueError:
                        continue
                    if (
                        parsed.scheme in {"https", "http"}
                        and parsed.hostname
                        and not parsed.username
                    ):
                        sources.append(
                            urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:500]
                        )
            row["sources"] = sources
            row["search_requests"] = data.get("search_requests", 0)
        if tool.tool_name in {"search_documents", "summarize_documents"}:
            row["reference_count"] = len(data.get("document_references", []))
        candidate = [*rows, row]
        if estimate_tokens(json.dumps(candidate, ensure_ascii=False)) > token_budget:
            break
        rows = candidate
    if not rows:
        return ()
    return (
        ContextBlock(
            source="confirmed_execution_history",
            content=json.dumps({"order": "newest_first", "actions": rows}, ensure_ascii=False),
        ),
    )


async def load_execution_context(*, owner: UUID, conversation_id: UUID, current_run: UUID):
    async with SessionFactory() as session:
        records = (
            await session.execute(
                select(ToolCall, AgentRun)
                .join(RunStep, ToolCall.run_step_id == RunStep.id)
                .join(AgentRun, RunStep.agent_run_id == AgentRun.id)
                .join(Conversation, AgentRun.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == owner,
                    Conversation.id == conversation_id,
                    AgentRun.id != current_run,
                )
                .order_by(ToolCall.created_at.desc(), ToolCall.id.desc())
                .limit(24)
            )
        ).all()
    return execution_rows(records)
