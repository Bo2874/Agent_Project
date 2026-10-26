"""Versioned route meanings, not a classifier, permission check or graph dispatcher."""

from dataclasses import dataclass

from agent_core.contracts.routing import RouteName

TAXONOMY_VERSION = "2.0.1"


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    route: RouteName
    name_vi: str
    name_en: str
    definition_vi: str
    definition_en: str
    boundary: str


ROUTE_TAXONOMY: tuple[RouteDefinition, ...] = (
    RouteDefinition(
        "chat",
        "Trả lời hoặc hỏi rõ",
        "Respond or clarify",
        "Trò chuyện, giải thích, soạn thảo, xử lý văn bản user dán trực tiếp hoặc hỏi lại "
        "khi thiếu dữ kiện thiết yếu/tham chiếu chưa rõ sau khi xét history.",
        "Respond using supplied content, or ask for essential missing information.",
        "Ask for missing information before choosing an execution path. "
        "Do not ask again for facts already in history. Writing a plan is chat. "
        "Previous document answers are not pasted user text: follow-up questions about "
        "selected files require document_qa even when history already contains the answer.",
    ),
    RouteDefinition(
        "tool_task",
        "Tác vụ công cụ trực tiếp",
        "Direct capability task",
        "Một mục tiêu thao tác rõ ràng dùng công cụ: đọc dữ liệu, tính chính xác hoặc ghi.",
        "A clear direct operation using a capability, including exact computation or writes.",
        "Pagination/retries are implementation details. "
        "Required approval is enforced by the executor.",
    ),
    RouteDefinition(
        "document_qa",
        "Hỏi đáp theo tài liệu",
        "Document-grounded question",
        "Tìm, hỏi đáp, tóm tắt hoặc so sánh dựa trên tài liệu đã được xác định và được phép đọc. "
        "Bao gồm hỏi nối tiếp, giải thích hoặc xác nhận về file đã chọn "
        "dù history đã có câu trả lời.",
        "Retrieve, answer, summarize or compare using resolved, accessible documents as evidence.",
        "Several documents alone do not imply planning. Prior assistant answers do not replace "
        "retrieval from selected files. Text pasted directly by the user can be answered as chat.",
    ),
    RouteDefinition(
        "planned_task",
        "Workflow nhiều bước",
        "Coordinated workflow",
        "Nhiều mục tiêu/bước thực thi cần tách task, quản lý phụ thuộc, "
        "song song hoặc tổng hợp nguồn.",
        "Coordinate multiple execution goals with task dependencies, "
        "parallel work or source synthesis.",
        "An execution workflow differs from merely writing a plan. "
        "Missing planner implementation does not change the user's intent.",
    ),
)
