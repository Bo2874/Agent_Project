from types import SimpleNamespace
from uuid import uuid4

from agent_core.history_compression import (
    HISTORY_SUMMARY_VERSION,
    HistoryCompressionPolicy,
    merge_summary_contents,
    summarize_messages,
)
from agent_core.history_eval import (
    RetentionProbe,
    build_history_retention_benchmark,
    evaluate_benchmark,
    evaluate_retention,
)
from backend.models import ConversationSummary
from backend.services.history_compression import prepare_history_context


class ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeSession:
    def __init__(self, messages: list[object], summaries: list[object]) -> None:
        self.results = [messages, summaries]
        self.added: list[ConversationSummary] = []
        self.commits = 0

    async def scalars(self, _statement: object) -> ScalarResult:
        return ScalarResult(self.results.pop(0))

    def add(self, value: ConversationSummary) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


def test_extractive_summary_is_versioned_and_smaller_than_source() -> None:
    messages = [
        SimpleNamespace(role="user", content="A" * 1_000),
        SimpleNamespace(role="assistant", content="B" * 1_000),
    ]

    summary = summarize_messages(messages, max_chars=800)

    assert HISTORY_SUMMARY_VERSION == "extractive-history-v2"
    assert len(summary) <= 800
    assert len(summary) < 2_000
    assert "USER:" in summary


def test_merging_summaries_compresses_again() -> None:
    summaries = [
        SimpleNamespace(tier=1, content="A" * 500),
        SimpleNamespace(tier=1, content="B" * 500),
    ]

    merged = merge_summary_contents(summaries, max_chars=500)

    assert len(merged) <= 500
    assert len(merged) < 1_000


async def test_prepare_history_context_builds_multiple_tiers_and_keeps_recent_raw() -> None:
    conversation_id = uuid4()
    messages = [
        SimpleNamespace(
            id=uuid4(),
            conversation_id=conversation_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}-" + ("x" * 100),
        )
        for index in range(60)
    ]
    session = FakeSession(messages, [])

    context = await prepare_history_context(  # type: ignore[arg-type]
        session,
        conversation_id,
        HistoryCompressionPolicy(
            recent_message_limit=10,
            batch_size=5,
            merge_factor=3,
            max_summary_chars=500,
        ),
    )

    assert len(context.messages) == 10
    assert context.messages[0].content.startswith("message-50-")
    assert context.created_summary_count == 14
    assert len(context.summaries) == 2
    assert ":tier:3:" in context.summaries[0].source
    assert ":tier:1:" in context.summaries[1].source
    assert all(summary.summary_version == HISTORY_SUMMARY_VERSION for summary in session.added)
    assert all(summary.summary_char_count < summary.source_char_count for summary in session.added)
    assert session.commits == 1


async def test_new_summary_version_does_not_reuse_old_version() -> None:
    conversation_id = uuid4()
    messages = [
        SimpleNamespace(
            id=uuid4(),
            conversation_id=conversation_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}-" + ("x" * 100),
        )
        for index in range(30)
    ]
    legacy_summary = SimpleNamespace(
        id=uuid4(),
        conversation_id=conversation_id,
        tier=1,
        revision=7,
        summary_version="extractive-history-v1",
        content="legacy summary",
        source_start_message_id=messages[0].id,
        source_end_message_id=messages[19].id,
        source_message_count=20,
        source_char_count=2_000,
        summary_char_count=14,
        superseded_by_id=None,
    )
    session = FakeSession(messages, [legacy_summary])

    context = await prepare_history_context(  # type: ignore[arg-type]
        session,
        conversation_id,
        HistoryCompressionPolicy(
            recent_message_limit=10,
            batch_size=5,
            merge_factor=10,
            max_summary_chars=500,
        ),
    )

    assert len(session.added) == 4
    assert all(summary.summary_version == HISTORY_SUMMARY_VERSION for summary in session.added)
    assert all("extractive-history-v2" in block.source for block in context.summaries)
    assert all("legacy summary" not in block.content for block in context.summaries)


def test_retention_match_is_case_and_accent_insensitive() -> None:
    report = evaluate_retention(
        "POSTGRESQL được dùng tại Da Nang.",
        (
            RetentionProbe("database", ("PostgreSQL",)),
            RetentionProbe("location", ("Đà Nẵng",)),
        ),
    )

    assert report.retention_rate == 1.0
    assert report.lost_labels == ()


def test_benchmark_measures_information_before_and_after_compression() -> None:
    result = evaluate_benchmark()

    raw = result["raw"]
    tier_one = result["tier_1"]
    tier_two = result["tier_2"]
    assert isinstance(raw, dict)
    assert isinstance(tier_one, dict)
    assert isinstance(tier_two, dict)
    assert raw["retention_rate"] == 1.0
    assert tier_one["retention_rate"] >= 0.9
    assert tier_two["retention_rate"] >= 0.8


def test_v2_keeps_important_fact_from_middle_of_long_message() -> None:
    messages, _ = build_history_retention_benchmark()
    long_message = SimpleNamespace(
        role="user",
        content=(
            "Phần mở đầu mô tả rất dài về các lựa chọn chưa được quyết định, "
            + ("thông tin phụ " * 20)
            + ", Database chính là PostgreSQL và port là 5432, "
            + ("ghi chú tham khảo " * 20)
            + ", phần kết chỉ là lời cảm ơn."
        ),
    )

    summary = summarize_messages([long_message, messages[1]], max_chars=350)
    report = evaluate_retention(
        summary,
        (
            RetentionProbe("database", ("PostgreSQL",)),
            RetentionProbe("port", ("5432",)),
        ),
    )

    assert report.retention_rate == 1.0
