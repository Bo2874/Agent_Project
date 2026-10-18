from collections.abc import Collection, Sequence
from dataclasses import dataclass

from agent_core.skills.registry import SkillRegistry
from agent_core.skills.selector import SkillSelector


@dataclass(frozen=True, slots=True)
class SkillSelectionCase:
    case_id: str
    query: str
    expected_skill: str | None


@dataclass(frozen=True, slots=True)
class SkillExecutionTrace:
    selected_skill: str | None
    tool_calls: tuple[str, ...]
    final_response: str
    tool_error_count: int = 0


@dataclass(frozen=True, slots=True)
class SkillAdherenceCase:
    case_id: str
    expected_skill: str
    trace: SkillExecutionTrace
    expected_tool_sequence: tuple[str, ...]
    required_response_markers: tuple[str, ...]
    forbidden_response_markers: tuple[str, ...] = ()
    max_tool_calls: int = 6


@dataclass(frozen=True, slots=True)
class SkillEvalCaseResult:
    case_id: str
    passed: bool
    expected: str | None
    actual: str | None
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillEvalSummary:
    total: int
    passed: int
    pass_rate: float
    cases: tuple[SkillEvalCaseResult, ...]

    @classmethod
    def from_cases(cls, cases: Sequence[SkillEvalCaseResult]) -> "SkillEvalSummary":
        passed = sum(case.passed for case in cases)
        total = len(cases)
        return cls(
            total=total,
            passed=passed,
            pass_rate=passed / total if total else 0.0,
            cases=tuple(cases),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "total": self.total,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "failures": [
                {
                    "case_id": case.case_id,
                    "expected": case.expected,
                    "actual": case.actual,
                    "reasons": list(case.failures),
                }
                for case in self.cases
                if not case.passed
            ],
        }


DEFAULT_SELECTION_CASES = (
    SkillSelectionCase(
        "calc_vi_chain",
        "Tính (120 cộng 30) nhân 2 rồi chia 5 theo từng bước",
        "multi_step_calculation",
    ),
    SkillSelectionCase(
        "calc_vi_operations",
        "Giúp tôi thực hiện phép tính nhiều bước gồm cộng trừ nhân chia",
        "multi_step_calculation",
    ),
    SkillSelectionCase(
        "calc_en_chain",
        "Calculate 10 plus 5 then multiply the result by 4",
        "multi_step_calculation",
    ),
    SkillSelectionCase(
        "calc_auditable",
        "Tôi cần một phép tính số học có các bước kiểm chứng",
        "multi_step_calculation",
    ),
    SkillSelectionCase(
        "text_vi_count",
        "Đếm số từ, ký tự và dòng trong đoạn văn bản này",
        "text_statistics_report",
    ),
    SkillSelectionCase(
        "text_vi_compare",
        "So sánh độ dài và thống kê hai đoạn văn bản",
        "text_statistics_report",
    ),
    SkillSelectionCase(
        "text_en_count",
        "Count the words, characters and lines in this text",
        "text_statistics_report",
    ),
    SkillSelectionCase(
        "text_ratio",
        "Tính số ký tự trung bình mỗi từ của văn bản",
        "text_statistics_report",
    ),
    SkillSelectionCase("none_temperature", "Đổi 20 độ C sang Fahrenheit", None),
    SkillSelectionCase("none_summary", "Tóm tắt nội dung cuộc họp", None),
    SkillSelectionCase("none_greeting", "Xin chào, bạn khỏe không?", None),
    SkillSelectionCase("none_server_time", "Bây giờ server là mấy giờ?", None),
    SkillSelectionCase(
        "none_python_execution",
        "Dùng Python tạo danh sách bình phương các số từ 1 đến 10 và in kết quả",
        None,
    ),
)


DEFAULT_ADHERENCE_CASES = (
    SkillAdherenceCase(
        case_id="calc_valid_trace",
        expected_skill="multi_step_calculation",
        trace=SkillExecutionTrace(
            selected_skill="multi_step_calculation",
            tool_calls=("demo__calculate",) * 4,
            final_response=(
                "Các bước: 120 + 30 = 150; 150 × 2 = 300; 300 / 5 = 60.\n"
                "Kết quả: 60.\nKiểm tra: 60 × 5 = 300."
            ),
        ),
        expected_tool_sequence=("demo__calculate",) * 4,
        required_response_markers=("Các bước", "Kết quả", "Kiểm tra"),
        forbidden_response_markers=("suy luận nội bộ",),
    ),
    SkillAdherenceCase(
        case_id="text_valid_trace",
        expected_skill="text_statistics_report",
        trace=SkillExecutionTrace(
            selected_skill="text_statistics_report",
            tool_calls=("demo__count_text", "demo__calculate"),
            final_response=(
                "Số liệu gốc: 120 ký tự, 24 từ, 3 dòng.\n"
                "Số liệu dẫn xuất: 120 / 24 = 5 ký tự mỗi từ.\n"
                "Ghi chú: tỷ lệ ký tự gồm cả khoảng trắng."
            ),
        ),
        expected_tool_sequence=("demo__count_text", "demo__calculate"),
        required_response_markers=("Số liệu gốc", "Số liệu dẫn xuất", "Ghi chú"),
        forbidden_response_markers=("suy luận nội bộ",),
    ),
)


def evaluate_skill_selection(
    registry: SkillRegistry,
    cases: Sequence[SkillSelectionCase],
    *,
    available_tools: Collection[str],
    selector: SkillSelector | None = None,
) -> SkillEvalSummary:
    skill_selector = selector or SkillSelector()
    results: list[SkillEvalCaseResult] = []
    for case in cases:
        selection = skill_selector.select(
            registry,
            case.query,
            available_tools=available_tools,
        )
        actual = selection.selected_name
        passed = actual == case.expected_skill
        results.append(
            SkillEvalCaseResult(
                case_id=case.case_id,
                passed=passed,
                expected=case.expected_skill,
                actual=actual,
                failures=() if passed else (f"selection_reason={selection.reason}",),
            )
        )
    return SkillEvalSummary.from_cases(results)


def evaluate_skill_adherence(
    registry: SkillRegistry,
    cases: Sequence[SkillAdherenceCase],
) -> SkillEvalSummary:
    results: list[SkillEvalCaseResult] = []
    for case in cases:
        trace = case.trace
        metadata = registry.get_metadata(case.expected_skill)
        failures: list[str] = []
        if trace.selected_skill != case.expected_skill:
            failures.append("wrong_skill")
        if trace.tool_calls != case.expected_tool_sequence:
            failures.append("wrong_tool_sequence")
        unexpected_tools = set(trace.tool_calls) - set(metadata.required_tools)
        if unexpected_tools:
            failures.append(f"unexpected_tools={sorted(unexpected_tools)}")
        if len(trace.tool_calls) > case.max_tool_calls:
            failures.append("tool_call_limit_exceeded")
        if trace.tool_error_count:
            failures.append("tool_error_not_handled")
        response = trace.final_response.casefold()
        for marker in case.required_response_markers:
            if marker.casefold() not in response:
                failures.append(f"missing_response_marker={marker}")
        for marker in case.forbidden_response_markers:
            if marker.casefold() in response:
                failures.append(f"forbidden_response_marker={marker}")
        results.append(
            SkillEvalCaseResult(
                case_id=case.case_id,
                passed=not failures,
                expected=case.expected_skill,
                actual=trace.selected_skill,
                failures=tuple(failures),
            )
        )
    return SkillEvalSummary.from_cases(results)
