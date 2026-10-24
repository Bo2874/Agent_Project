"""Conservative, offline route policy over an already-authorized metadata snapshot.

This is a keyword baseline, not a semantic classifier or an authorization policy.
It never loads schemas, Skill instructions, or a decision provider.
"""

import re
from collections.abc import Sequence
from time import perf_counter

from agent_core.capability_catalog import CapabilityCatalog, normalize
from agent_core.contracts import RoutingDecision
from agent_core.contracts.routing import FallbackReason, RouteName
from agent_core.llm.types import ModelMessage

RULES_VERSION = "rules1.1.0"
ROUTING_POLICY_VERSION = "query_route1.0.0"

_NO_TOOLS = re.compile(
    r"\b(?:khong (?:dung|su dung|goi) (?:bat ky )?(?:tool|cong cu)|"
    r"(?:do not|don t|never) (?:use|call) (?:any )?(?:tools?|external services)|"
    r"chi (?:dung|su dung) (?:van ban|noi dung) (?:duoc cung cap|ben duoi)|"
    r"only use (?:the )?(?:provided|following) text)\b"
)
_EXPLAIN = re.compile(r"^(?:(?:hay|please|ban) )?(?:giai thich|explain|dinh nghia|define)\b")
_DRAFT_PLAN = re.compile(
    r"\b(?:viet (?:mot )?(?:ke hoach|dan y)|lap (?:mot )?ke hoach|"
    r"(?:write|draft) (?:a |an )?(?:plan|outline))\b"
)
_PROSE = re.compile(
    r"^(?:(?:hay|please) )?(?:viet|write|draft|ke|tell) "
    r"(?:(?:mot|a|an|me) )?(?:bai viet|bai tho|cau chuyen|article|poem|story|email draft)\b"
)
_INLINE = re.compile(
    r"^(?:(?:hay|please) )?(?:dich|tom tat|viet lai|translate|summarize|summarise|rewrite)\b"
)
_DOCUMENT = re.compile(
    r"\b(?:tai lieu|tep|file|pdf|document) (?:da )?(?:tai len|upload|uploaded|da chon)|"
    r"\b(?:uploaded|selected|attached) (?:documents?|files?|pdf)|"
    r"\b(?:tim trong|search within|search in|tra loi (?:dua )?theo) (?:tai lieu|documents?)\b"
)
_PLANNED = re.compile(
    r"\b(?:thuc thi (?:ke hoach|nhieu buoc)|chay song song|"
    r"(?:execute|run) (?:the |a )?(?:plan|workflow)|in parallel|sub agents?|subagents?)\b"
)
_TOOL = re.compile(
    r"\b(?:tinh|calculate|compute|tim kiem|tra cuu|search|look up|lookup|"
    r"doc (?:tep|file|du lieu)|read (?:the |a )?(?:file|data)|"
    r"lay du lieu|get data|github|chay python|run python|"
    r"may gio|gio hien tai|current time|what time|"
    r"gui|send|tao ban ghi|create record|dat lich|schedule|"
    r"dung tool|use (?:the )?tool)\b"
)
_FOLLOWUP = re.compile(
    r"^(?:tiep tuc|lam tiep|lam di|cai do|continue|do it|go ahead|that one)[ .!?]*$"
)
_NEGATED_ACTION = re.compile(
    r"\b(?:khong (?:can )?|dung |do not |don t |never )"
    r"(?:tim kiem|tra cuu|search|look up|gui|send|tinh|calculate|goi|call)\b"
)
_MISSING = re.compile(
    r"^(?:(?:hay|please) )?(?:tim kiem|tra cuu|search|look up|"
    r"gui (?:no|cai do)|send (?:it|that)|doc (?:tep|file)|read (?:a |the )?file)[ .!?]*$"
)
_STOP_WORDS = frozenset(
    "a an and are ban can cho could cua de duoc for hay help i in is la me mot my of "
    "on please the this to toi tool tools va ve voi you your".split()
)


def _latest_user(messages: Sequence[ModelMessage]) -> tuple[str, str]:
    # Assistant/system text cannot introduce a tool request for this policy.
    users = [m.get("content", "") for m in messages if m.get("role") == "user"]
    texts = [v for v in users if isinstance(v, str)]
    return (texts[-1] if texts else "", texts[-2] if len(texts) > 1 else "")


def route_query(messages: Sequence[ModelMessage], catalog: CapabilityCatalog) -> RoutingDecision:
    """Choose only implemented routes; RAG/planner flags do not install handlers.

    Tool candidates are discovery hints. Day 90 leaves tool/Skill selection to the
    existing budgeted middleware and all authorization/approval checks to execution.
    """
    started = perf_counter()
    latest, previous = _latest_user(messages)
    query = normalize(latest) if len(latest) <= 32768 else ""
    followup = bool(_FOLLOWUP.fullmatch(query))
    source = previous if followup and previous else latest
    effective = normalize(source) if len(source) <= 32768 else ""
    candidates: tuple[str, ...] = ()

    def decision(
        route: RouteName, reason: str, fallback: FallbackReason | None = None
    ) -> RoutingDecision:
        return RoutingDecision(
            route=route,
            provider="rules",
            model_version=RULES_VERSION,
            policy_version=ROUTING_POLICY_VERSION,
            catalog_version=catalog.version,
            latency_ms=(perf_counter() - started) * 1000,
            input_tokens=0,  # No model inference; not Jev usage metadata.
            reason_code=reason,
            fallback_reason=fallback,
            candidate_tool_ids=candidates if route == "tool_task" else (),
        )

    if len(latest) > 32768 or len(source) > 32768 or len(effective) > 32768:
        return decision("clarify", "routing_input_too_large", "ambiguous")
    if not query or (followup and not previous):
        return decision("clarify", "missing_context", "ambiguous")
    if _NO_TOOLS.search(effective):
        return decision("chat", "explicit_no_tools")
    clauses = re.split(r"[;,.!?\n]|\bbut\b|\bnhưng\b", source, flags=re.IGNORECASE)
    positive = [clause for clause in clauses if not _NEGATED_ACTION.search(normalize(clause))]
    if len(positive) < len(clauses):
        source = " ".join(positive)
        effective = normalize(source)
        if not effective.strip():
            return decision("chat", "negated_tool_request")
    if _INLINE.search(effective) and ":" in source:
        return decision("chat", "self_contained_request")
    if _DOCUMENT.search(effective):
        return decision("clarify", "document_qa_unavailable", "capability_unavailable")
    if _EXPLAIN.search(effective) or _DRAFT_PLAN.search(effective) or _PROSE.search(effective):
        return decision("chat", "self_contained_request")
    if _PLANNED.search(effective):
        return decision("clarify", "planned_task_unavailable", "capability_unavailable")
    if _MISSING.fullmatch(effective):
        return decision("clarify", "missing_information", "ambiguous")

    math_request = bool(
        re.fullmatch(r"[\d\s()+*/.%\-]+", source.strip())
        and re.search(r"\d\s*[+*/-]\s*\d", source)
        and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", source.strip())
    )
    catalog_query = f"{effective} calculate tinh" if math_request else effective
    words = set(catalog_query.split()) - _STOP_WORDS
    matching = tuple(
        record
        for record in catalog.search(catalog_query, limit=64)
        if words
        & set(
            normalize(
                " ".join(
                    (
                        record.binding_name,
                        record.descriptor.name,
                        record.descriptor.description,
                        *record.descriptor.tags,
                        *record.keywords,
                    )
                )
            ).split()
        )
        - _STOP_WORDS
    )
    explicit_tool = bool(_TOOL.search(effective) or math_request)
    # A custom plugin's published alias/keyword is also a routing hint, but its
    # description alone must not turn arbitrary conversation into a tool request.
    named_tool = any(
        f" {normalize(record.binding_name)} " in f" {effective} "
        or any(normalize(k) and f" {normalize(k)} " in f" {effective} " for k in record.keywords)
        for record in matching
    )
    if explicit_tool or named_tool:
        if not matching:
            return decision("clarify", "no_accessible_tool", "no_candidate")
        candidates = tuple(record.descriptor.capability_id for record in matching)
        return decision("tool_task", "accessible_tool_request")
    return decision("chat", "general_conversation")


def clarification_message(decision: RoutingDecision, messages: Sequence[ModelMessage]) -> str:
    latest, _ = _latest_user(messages)
    # A local UI language heuristic only; route matching is bilingual/accent-insensitive.
    vietnamese = bool(re.search(r"[^\x00-\x7f]", latest)) or bool(
        re.search(r"\b(?:toi|hay|ban|tiep tuc|tai lieu|tim kiem)\b", normalize(latest))
    )
    if decision.reason_code == "document_qa_unavailable":
        return (
            "Tìm kiếm tài liệu chưa được hỗ trợ trong luồng này. "
            "Bạn có thể dán đoạn cần hỏi vào chat."
            if vietnamese
            else "Document retrieval is not available in this flow. Please paste the relevant text."
        )
    if decision.reason_code == "planned_task_unavailable":
        return (
            "Luồng thực thi nhiều agent chưa sẵn sàng. Bạn muốn thực hiện tác vụ đơn lẻ nào trước?"
            if vietnamese
            else "Multi-agent execution is not available yet. Which single task should we do first?"
        )
    if decision.reason_code == "no_accessible_tool":
        return (
            "Chưa tìm thấy công cụ phù hợp đang khả dụng cho bạn. "
            "Bạn muốn dùng dữ liệu nào để tiếp tục?"
            if vietnamese
            else "No matching tool is currently available to you. "
            "What data should we use to continue?"
        )
    return (
        "Bạn bổ sung mục tiêu hoặc dữ liệu cần xử lý để mình tiếp tục nhé."
        if vietnamese
        else "Please specify the task or the data you want me to work with."
    )
