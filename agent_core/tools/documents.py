"""Document capabilities with host-owned scope; no database or model factory here."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools import ToolExecutionPolicy, ToolRegistry, create_context_tool


class DocumentQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    query: str = Field(
        min_length=1,
        max_length=4000,
        description="One standalone question in the user's language; resolve history references.",
    )

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Document query must contain text without null characters")
        return value.strip()


def register_document_tools(registry: ToolRegistry, *, search, summarize, timeout_seconds):
    descriptions = {
        "search_documents": (
            "Search private documents selected for this conversation. Returns original passages "
            "with page/section metadata, not a generated answer. Use for document questions, "
            "follow-ups, explanations, comparisons or summaries of a SPECIFIC TOPIC. Use history "
            "into one standalone query. Search before answering about selected documents; previous "
            "assistant answers are not document evidence. Scope and Fast/Deep mode are set by host."
        ),
        "summarize_documents": (
            "Summarize ALL selected private documents using the bounded full-coverage workflow, "
            "including map/reduce when necessary. Use only for a whole-document overview, not a "
            "specific topic. If full coverage exceeds the supported limit, report that limit and "
            "ask to narrow the task; never substitute top-k search and claim a full summary."
        ),
    }
    for name, handler in (("search_documents", search), ("summarize_documents", summarize)):
        registry.register(
            create_context_tool(
                name=name,
                description=descriptions[name],
                args_schema=DocumentQueryInput,
                coroutine=handler,
            ),
            policy=ToolExecutionPolicy(timeout_seconds=timeout_seconds, max_retries=0),
            capability=CapabilityDescriptor(
                capability_id=name,
                kind="tool",
                name=name,
                description=descriptions[name],
                version="1.0.0",
                side_effect="read_only",
                requires_approval=False,
            ),
        )


def create_document_registry(base: ToolRegistry, *, search, summarize, timeout_seconds):
    """Never attach a user's document handlers to the shared host/plugin registry."""
    registry = base.fork()
    register_document_tools(
        registry, search=search, summarize=summarize, timeout_seconds=timeout_seconds
    )
    return registry
