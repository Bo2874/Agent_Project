import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from agent_core.history_compression import merge_summary_contents, summarize_messages

_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class BenchmarkMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    tier: int
    content: str


@dataclass(frozen=True, slots=True)
class RetentionProbe:
    label: str
    required_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetentionReport:
    total: int
    retained: int
    lost_labels: tuple[str, ...]

    @property
    def retention_rate(self) -> float:
        return self.retained / self.total if self.total else 1.0


def _normalize_for_match(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    without_accents = without_accents.replace("đ", "d")
    return _NORMALIZE_PATTERN.sub(" ", without_accents).strip()


def evaluate_retention(text: str, probes: Sequence[RetentionProbe]) -> RetentionReport:
    normalized_text = f" {_normalize_for_match(text)} "
    lost_labels = tuple(
        probe.label
        for probe in probes
        if not all(
            f" {_normalize_for_match(term)} " in normalized_text for term in probe.required_terms
        )
    )
    return RetentionReport(
        total=len(probes),
        retained=len(probes) - len(lost_labels),
        lost_labels=lost_labels,
    )


def build_history_retention_benchmark() -> tuple[
    tuple[BenchmarkMessage, ...], tuple[RetentionProbe, ...]
]:
    messages = (
        BenchmarkMessage(
            "user",
            "Tôi đang xây trợ lý cá nhân cho công việc hằng ngày. Tên tôi là Minh. "
            "Tôi sống ở Đà Nẵng và muốn câu trả lời ngắn gọn, có thể kiểm chứng.",
        ),
        BenchmarkMessage(
            "assistant",
            "Tôi đã ghi nhận hồ sơ và sẽ dùng các thông tin này để cá nhân hóa câu trả lời.",
        ),
        BenchmarkMessage(
            "user",
            "Lịch của tôi có nhiều cuộc họp quốc tế. Múi giờ mặc định là Asia/Ho_Chi_Minh. "
            "Nếu thời gian mơ hồ thì phải hỏi lại trước khi tạo lịch.",
        ),
        BenchmarkMessage(
            "assistant",
            "Tôi sẽ giữ nguyên múi giờ đã chọn và xác nhận khi thời gian chưa rõ ràng.",
        ),
        BenchmarkMessage(
            "user",
            "Kiến trúc backend có nhiều lựa chọn. Database chính là PostgreSQL, chạy ở port 5432. "
            "Không dùng MongoDB trong project này.",
        ),
        BenchmarkMessage(
            "assistant",
            "PostgreSQL sẽ lưu dữ liệu nghiệp vụ; tôi sẽ không đề xuất MongoDB cho kiến trúc này.",
        ),
        BenchmarkMessage(
            "user",
            "Hệ thống cần phản hồi nhanh khi nhiều người dùng truy cập. Redis chỉ dùng cho cache "
            "và rate limit, không phải nguồn dữ liệu chính.",
        ),
        BenchmarkMessage(
            "assistant",
            "Redis sẽ là hạ tầng tạm thời cho cache và giới hạn tần suất yêu cầu.",
        ),
        BenchmarkMessage(
            "user",
            "Mốc bàn giao bản thử nghiệm là ngày 2026-12-15. Ngân sách tối đa là 20000000 VND. "
            "Hãy cảnh báo sớm nếu dự kiến vượt ngân sách.",
        ),
        BenchmarkMessage(
            "assistant",
            "Tôi sẽ theo dõi deadline, ngân sách và cảnh báo trước khi có nguy cơ vượt giới hạn.",
        ),
        BenchmarkMessage(
            "user",
            "Mọi tool thanh toán đều phải có user approval. Đây là quy tắc an toàn bắt buộc, "
            "kể cả khi model cho rằng giao dịch có rủi ro thấp.",
        ),
        BenchmarkMessage(
            "assistant",
            "Tôi sẽ dừng ở bước chờ phê duyệt trước mọi hành động thanh toán.",
        ),
        BenchmarkMessage(
            "user",
            "Không bao giờ tự động gửi email. Agent chỉ được tạo bản nháp để tôi đọc, sửa và "
            "bấm xác nhận gửi.",
        ),
        BenchmarkMessage(
            "assistant",
            "Email sẽ luôn dừng ở bản nháp cho đến khi bạn xác nhận rõ ràng.",
        ),
        BenchmarkMessage(
            "user",
            "Tool quản trị chỉ dành cho role admin. User thường không được xem token của người "
            "khác hoặc thay đổi cấu hình toàn hệ thống.",
        ),
        BenchmarkMessage(
            "assistant",
            "Tôi sẽ kiểm tra role ở backend trước khi cho phép gọi tool quản trị.",
        ),
        BenchmarkMessage(
            "user",
            "Khi một tool lỗi tạm thời, retry tối đa 2 lần. Không retry thao tác có side effect "
            "nếu chưa có idempotency key.",
        ),
        BenchmarkMessage(
            "assistant",
            "Chính sách retry sẽ phân biệt thao tác chỉ đọc và thao tác có side effect.",
        ),
        BenchmarkMessage(
            "user",
            "Dữ liệu cá nhân không được ghi vào log dạng văn bản thuần. Log phải che email, "
            "token truy cập và thông tin định danh nhạy cảm.",
        ),
        BenchmarkMessage(
            "assistant",
            "Tôi sẽ áp dụng redaction trước khi ghi log và giới hạn quyền xem log.",
        ),
    )
    probes = (
        RetentionProbe("Tên người dùng", ("Minh",)),
        RetentionProbe("Nơi ở", ("Đà Nẵng",)),
        RetentionProbe("Múi giờ", ("Asia", "Ho_Chi_Minh")),
        RetentionProbe("Database chính", ("PostgreSQL",)),
        RetentionProbe("Database bị loại", ("Không dùng MongoDB",)),
        RetentionProbe("Cổng database", ("5432",)),
        RetentionProbe("Cache", ("Redis", "cache")),
        RetentionProbe("Deadline", ("2026-12-15",)),
        RetentionProbe("Ngân sách", ("20000000", "VND")),
        RetentionProbe("Phê duyệt thanh toán", ("tool thanh toán", "user approval")),
        RetentionProbe("Không tự gửi email", ("Không bao giờ tự động gửi email",)),
        RetentionProbe("Quyền admin", ("role admin",)),
        RetentionProbe("Giới hạn retry", ("retry tối đa 2 lần",)),
        RetentionProbe("Idempotency", ("idempotency key",)),
        RetentionProbe("Bảo vệ log", ("Log phải che email",)),
    )
    return messages, probes


def evaluate_benchmark(*, batch_size: int = 5, max_chars: int = 2_000) -> dict[str, object]:
    messages, probes = build_history_retention_benchmark()
    raw_text = "\n".join(f"{message.role}: {message.content}" for message in messages)
    tier_one = [
        summarize_messages(messages[index : index + batch_size], max_chars=max_chars)
        for index in range(0, len(messages), batch_size)
    ]
    tier_one_text = "\n".join(tier_one)
    tier_two_text = merge_summary_contents(
        [BenchmarkSummary(tier=1, content=content) for content in tier_one],
        max_chars=max_chars,
    )
    reports = {
        "raw": evaluate_retention(raw_text, probes),
        "tier_1": evaluate_retention(tier_one_text, probes),
        "tier_2": evaluate_retention(tier_two_text, probes),
    }
    return {
        name: {
            "total": report.total,
            "retained": report.retained,
            "retention_rate": round(report.retention_rate, 4),
            "lost_labels": list(report.lost_labels),
        }
        for name, report in reports.items()
    }
