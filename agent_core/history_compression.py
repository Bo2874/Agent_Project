import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

HISTORY_SUMMARY_VERSION = "extractive-history-v2"
_WHITESPACE_PATTERN = re.compile(r"\s+")
_SEGMENT_PATTERN = re.compile(r"(?<=[.!?;])\s+|\s*\n+\s*|,\s+")
_NUMBER_PATTERN = re.compile(r"\b\d[\d./:_-]*\b")
_SALIENT_PATTERN = re.compile(
    r"\b(?:"
    r"admin|approval|budget|cache|database|deadline|email|id|idempotency|không|luôn|"
    r"mongodb|múi giờ|must|ngân sách|never|only|phải|port|postgresql|redis|retry|"
    r"role|sống|tên|timezone|token|tool|ưu tiên"
    r")\b",
    re.IGNORECASE,
)


class HistoryMessage(Protocol):
    role: str
    content: str


class StoredSummary(Protocol):
    tier: int
    content: str


@dataclass(frozen=True, slots=True)
class HistoryCompressionPolicy:
    recent_message_limit: int = 20
    batch_size: int = 10
    merge_factor: int = 4
    max_summary_chars: int = 6_000
    version: str = HISTORY_SUMMARY_VERSION

    def __post_init__(self) -> None:
        if self.recent_message_limit < 2:
            raise ValueError("Phải giữ lại ít nhất 2 message gần nhất")
        if self.batch_size < 2:
            raise ValueError("Compression batch phải có ít nhất 2 message")
        if self.merge_factor < 2:
            raise ValueError("Summary merge factor phải lớn hơn hoặc bằng 2")
        if self.max_summary_chars < 200:
            raise ValueError("Summary phải cho phép ít nhất 200 ký tự")
        if not self.version.strip():
            raise ValueError("Summary version không được để trống")


def _normalize_text(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _segment_score(segment: str, index: int, segment_count: int) -> int:
    score = len(_SALIENT_PATTERN.findall(segment)) * 4
    score += len(_NUMBER_PATTERN.findall(segment)) * 5
    if ":" in segment or "=" in segment:
        score += 2
    if index in {0, segment_count - 1}:
        score += 1
    return score


def _compact_text(value: str, limit: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    if limit <= 12:
        return normalized[:limit]

    segments = [segment.strip() for segment in _SEGMENT_PATTERN.split(value) if segment.strip()]
    if len(segments) <= 1:
        head_size = max(int(limit * 0.55) - 2, 1)
        tail_size = max(limit - head_size - 3, 1)
        return f"{normalized[:head_size]} … {normalized[-tail_size:]}"

    ranked = sorted(
        enumerate(segments),
        key=lambda item: (
            -_segment_score(item[1], item[0], len(segments)),
            item[0],
        ),
    )
    selected: dict[int, str] = {}
    used_chars = 0
    separator_chars = 3
    for index, segment in ranked:
        normalized_segment = _normalize_text(segment)
        remaining = limit - used_chars - (separator_chars if selected else 0)
        if remaining <= 12:
            continue
        if len(normalized_segment) > remaining and selected:
            continue
        candidate = _compact_text(normalized_segment, remaining)
        selected[index] = candidate
        used_chars += len(candidate) + (separator_chars if len(selected) > 1 else 0)
        if used_chars >= limit - 12:
            break

    compacted = " … ".join(selected[index] for index in sorted(selected))
    return compacted[:limit].rstrip()


def _join_compacted_entries(entries: Sequence[str], max_chars: int) -> str:
    if not entries:
        raise ValueError("Không có dữ liệu để tạo summary")
    return _compact_text("\n".join(entries), max_chars)


def summarize_messages(
    messages: Sequence[HistoryMessage],
    *,
    max_chars: int,
) -> str:
    source_chars = sum(len(message.content) for message in messages)
    target_chars = min(max_chars, max(min(int(source_chars * 0.7), source_chars - 1), 1))
    entries = [
        f"{'USER' if message.role == 'user' else 'ASSISTANT'}: {message.content}"
        for message in messages
    ]
    return _join_compacted_entries(entries, target_chars)


def merge_summary_contents(
    summaries: Sequence[StoredSummary],
    *,
    max_chars: int,
) -> str:
    source_chars = sum(len(summary.content) for summary in summaries)
    target_chars = min(max_chars, max(min(int(source_chars * 0.7), source_chars - 1), 1))
    entries = [f"TIER {summary.tier}: {summary.content}" for summary in summaries]
    return _join_compacted_entries(entries, target_chars)
