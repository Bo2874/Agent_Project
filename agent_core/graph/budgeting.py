"""Run-local admission estimates. Provider usage, never estimates, is billed as actual."""

from contextlib import asynccontextmanager
from dataclasses import dataclass

from agent_core.context_budget import estimate_tokens

WEB_SEARCH_ESTIMATED_TOKENS = 10_000
UNBILLED_TOOL_ERRORS = frozenset(
    {
        "invalid_tool_arguments",
        "tool_schema_validation_failed",
        "tool_not_found",
        "tool_not_loaded",
        "tool_not_allowlisted",
        "tool_permission_denied",
        "tool_approval_denied",
        "tool_approval_required",
    }
)


@dataclass
class UsageReservation:
    actual: int | None = None
    known_tokens: int = 0


@asynccontextmanager
async def unreserved_usage(amount):
    yield UsageReservation()


def model_allowance(messages, tools, output_tokens: int) -> int:
    return (
        32
        + sum(4 + estimate_tokens(m) for m in messages)
        + estimate_tokens(tools or [])
        + output_tokens
    )


def final_allowance(messages, output_tokens: int) -> int:
    # Leave room for the next observation as well as a final, tool-free model turn.
    return model_allowance(messages, [], output_tokens) + 2_000


def tool_allowance(name: str, available: int, final_reserve: int, web_estimate: int) -> int:
    if name == "search_web":
        return web_estimate
    if name == "summarize_documents":
        return max(0, available - final_reserve)
    return 0


def partial_answer(state, reason: str) -> str:
    descriptions = {
        "token_limit": "ngân sách token",
        "step_limit": "số bước cho phép",
        "time_limit": "thời gian cho phép",
        "quota_limit": "quota sử dụng",
        "unknown_billing": "giới hạn an toàn khi trạng thái tính phí chưa xác định",
        "repeated_tool_call": "giới hạn lặp hành động",
        "repeated_observation": "giới hạn lặp kết quả",
    }
    parts = [
        f"Đã dừng vì chạm {descriptions.get(reason, 'giới hạn thực thi')}. Yêu cầu chưa hoàn tất."
    ]
    for row in state.get("tool_observations", [])[-8:]:
        name = row["tool_name"]
        if row["is_error"]:
            parts.append(f"- {name}: chưa thành công ({row.get('error_code') or 'tool_error'}).")
        elif name in {"search_documents", "summarize_documents"}:
            parts.append(f"- {name}: đã nhận kết quả tài liệu; chưa hoàn tất câu trả lời tổng hợp.")
        else:
            # Display the observation as an attributed result, not a new model claim.
            content = row["content"]
            if len(content) > 12_000:
                content = content[:12_000] + "\n[Kết quả dài đã được rút gọn.]"
            parts.append(f"Kết quả đã nhận từ {name}:\n\n{content}")
    parts.append("Các hành động còn lại chưa được thực hiện. Không tự động gọi lại các công cụ.")
    return "\n\n".join(parts)
