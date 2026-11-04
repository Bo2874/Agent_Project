from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_core.context_budget import ContextTokenBudget
from agent_core.history_compression import (
    HISTORY_SUMMARY_VERSION,
    LLM_HISTORY_SUMMARY_VERSION,
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
from backend.services import history_compression
from backend.services.history_compression import prepare_history_context

REAL_SUMMARIZE = history_compression._summarize


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
        self.rollbacks = 0

    async def scalar(self, _statement):
        return uuid4()

    async def scalars(self, _statement: object) -> ScalarResult:
        return ScalarResult(self.results.pop(0))

    def add(self, value: ConversationSummary) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self):
        pass

    async def rollback(self):
        self.rollbacks += 1
        self.added.clear()


@pytest.fixture(autouse=True)
def summary_model_boundary(monkeypatch):
    async def summarize(entries, **kwargs):
        return "Faithful compact notes."

    mock = AsyncMock(side_effect=summarize)
    monkeypatch.setattr(history_compression, "_summarize", mock)
    return mock


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
        ),
        user_id=uuid4(),
        user_message_id=messages[-1].id,
        budget=ContextTokenBudget(),
    )

    assert len(context.messages) == 10
    assert context.messages[0].content.startswith("message-50-")
    assert context.created_summary_count == 14
    assert len(context.summaries) == 2
    assert ":tier:3:" in context.summaries[0].source
    assert ":tier:1:" in context.summaries[1].source
    assert all(summary.summary_version == LLM_HISTORY_SUMMARY_VERSION for summary in session.added)
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
        ),
        user_id=uuid4(),
        user_message_id=messages[-1].id,
        budget=ContextTokenBudget(),
    )

    assert len(session.added) == 4
    assert all(summary.summary_version == LLM_HISTORY_SUMMARY_VERSION for summary in session.added)
    assert all(LLM_HISTORY_SUMMARY_VERSION in block.source for block in context.summaries)
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


def _messages(count, *, old_chars=300, recent_chars=100):
    conversation = uuid4()
    return [
        SimpleNamespace(
            id=uuid4(),
            conversation_id=conversation,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}: "
            + "x" * (old_chars if index < count - 20 else recent_chars),
        )
        for index in range(count)
    ]


async def _prepare(session, messages, budget=None):
    return await prepare_history_context(
        session,
        messages[0].conversation_id,
        HistoryCompressionPolicy(),
        user_id=uuid4(),
        user_message_id=messages[-1].id,
        budget=budget or ContextTokenBudget(),
    )


@pytest.mark.parametrize(
    "count,calls,raw", [(20, 0, 20), (29, 0, 29), (30, 1, 20), (40, 2, 20), (60, 5, 20)]
)
async def test_llm_batch_and_merge_thresholds(count, calls, raw, summary_model_boundary):
    messages = _messages(count)
    session = FakeSession(messages, [])
    result = await _prepare(session, messages)
    assert len(result.messages) == raw
    assert result.messages[-1] is messages[-1]
    assert summary_model_boundary.await_count == calls
    assert result.created_summary_count == calls
    if count == 60:
        merged_entries = summary_model_boundary.call_args.args[0]
        assert len(merged_entries) == 4 and all(e["tier"] == 1 for e in merged_entries)
        assert len(result.summaries) == 1 and ":tier:2:" in result.summaries[0].source


async def test_committed_llm_summaries_are_reused_without_another_paid_call(summary_model_boundary):
    messages = _messages(30)
    first = FakeSession(messages, [])
    await _prepare(first, messages)
    saved = list(first.added)
    second = FakeSession(messages, saved)
    result = await _prepare(second, messages)
    assert summary_model_boundary.await_count == 1
    assert result.created_summary_count == 0
    assert result.summaries[0].content == saved[0].content


async def test_summary_can_exceed_old_character_cap_within_shared_budget(summary_model_boundary):
    summary_model_boundary.side_effect = None
    summary_model_boundary.return_value = "Useful notes " * 550
    messages = _messages(30, old_chars=2000)
    result = await _prepare(FakeSession(messages, []), messages)
    assert len(result.summaries[0].content) > 6000


async def test_summary_allowance_follows_remaining_history_space(summary_model_boundary):
    messages = _messages(30, old_chars=1000, recent_chars=240)
    await _prepare(
        FakeSession(messages, []), messages, ContextTokenBudget(memory_history_tokens=4000)
    )
    small = summary_model_boundary.call_args.kwargs["output_tokens"]
    await _prepare(
        FakeSession(messages, []), messages, ContextTokenBudget(memory_history_tokens=10000)
    )
    large = summary_model_boundary.call_args.kwargs["output_tokens"]
    assert 0 < small < large


async def test_short_history_batch_can_save_summary_longer_than_source(monkeypatch):
    from agent_core.llm.types import LLMChunk

    class Audit:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def add(self, item):
            pass

        async def commit(self):
            pass

    class Model:
        provider, model = "controlled", "summary-model"

        async def stream(self, messages, **kwargs):
            yield LLMChunk(content="Faithful notes. " * 200, finish_reason="stop")

        async def aclose(self):
            pass

    monkeypatch.setattr(history_compression, "_summarize", REAL_SUMMARIZE)
    monkeypatch.setattr(history_compression, "SessionFactory", Audit)
    monkeypatch.setattr(history_compression, "create_chat_model", lambda **_: Model())
    messages = _messages(30, old_chars=0)
    session = FakeSession(messages, [])
    result = await _prepare(session, messages)
    assert result.summaries[0].content == ("Faithful notes. " * 200).strip()
    assert session.added[0].summary_char_count > session.added[0].source_char_count
    assert session.commits == 1


@pytest.mark.parametrize("source_chars,summary_chars", [(10, 10), (10, 100)])
def test_summary_database_accepts_equal_or_longer_summary(source_chars, summary_chars):
    from sqlalchemy import Column, MetaData, Table, Uuid, create_engine

    metadata = MetaData()
    for name in ("conversations", "messages"):
        Table(name, metadata, Column("id", Uuid, primary_key=True))
    table = ConversationSummary.__table__.to_metadata(metadata)
    engine = create_engine("sqlite://")
    try:
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                table.insert().values(
                    id=uuid4(),
                    conversation_id=uuid4(),
                    tier=1,
                    revision=1,
                    summary_version=LLM_HISTORY_SUMMARY_VERSION,
                    content="x" * summary_chars,
                    source_message_count=10,
                    source_char_count=source_chars,
                    summary_char_count=summary_chars,
                )
            )
            assert connection.scalar(table.select().with_only_columns(table.c.content)) == (
                "x" * summary_chars
            )
    finally:
        engine.dispose()


async def test_history_failure_rolls_back_new_summaries_without_extractive_fallback(
    summary_model_boundary,
):
    summary_model_boundary.side_effect = [
        "First successful summary",
        RuntimeError("Provider failed"),
    ]
    messages = _messages(40)
    session = FakeSession(messages, [])
    with pytest.raises(history_compression.HistoryCompressionError, match="LLM"):
        await _prepare(session, messages)
    assert summary_model_boundary.await_count == 2
    assert session.commits == 0 and session.rollbacks == 1 and not session.added
    assert len(messages) == 40 and messages[0].content.startswith("message-0")


async def test_no_paid_summary_when_recent_history_alone_exhausts_budget(summary_model_boundary):
    messages = _messages(30, recent_chars=1000)
    with pytest.raises(history_compression.HistoryCompressionError, match="ngân sách"):
        await _prepare(
            FakeSession(messages, []), messages, ContextTokenBudget(memory_history_tokens=500)
        )
    summary_model_boundary.assert_not_awaited()


async def test_chat_does_not_start_agent_when_llm_history_preparation_fails(monkeypatch):
    from fastapi import HTTPException

    from backend.routes import chat
    from backend.schemas.chat import MessageCreate

    owner, conversation, message = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        chat, "get_owned_conversation", AsyncMock(return_value=SimpleNamespace(id=conversation))
    )
    monkeypatch.setattr(chat, "consume_usage_limits", AsyncMock())
    monkeypatch.setattr(chat, "add_usage", AsyncMock())
    monkeypatch.setattr(
        chat, "add_user_message", AsyncMock(return_value=SimpleNamespace(id=message))
    )
    monkeypatch.setattr(
        chat,
        "prepare_history_context",
        AsyncMock(side_effect=history_compression.HistoryCompressionError("Summary failed")),
    )
    create_run = AsyncMock()
    monkeypatch.setattr(chat, "create_agent_run", create_run)
    model = SimpleNamespace(aclose=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await chat.post_message(
            conversation_id=conversation,
            data=MessageCreate(content="Next question"),
            session=None,
            user=SimpleNamespace(id=owner, role="user"),
            model=model,
            registry=None,
            skill_registry=None,
            decision_provider=None,
        )
    assert error.value.status_code == 503
    create_run.assert_not_awaited()
    model.aclose.assert_awaited_once()


@pytest.mark.parametrize(
    "finish,content,valid",
    [
        ("stop", "notes " * 1200, True),
        ("length", "partial", False),
        ("stop", " ", False),
        ("stop", "notes " * 9000, False),
    ],
    ids=["long-summary", "truncated", "empty", "history-budget-exceeded"],
)
async def test_llm_summary_stream_audit_usage_and_output_reservation(
    monkeypatch, finish, content, valid
):
    from agent_core.llm.types import LLMChunk, TokenUsage

    records, caps = [], []

    class Audit:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def add(self, item):
            records.append(item)

        async def commit(self):
            pass

    class Model:
        provider, model = "controlled", "summary-model"
        calls, closed = 0, False

        async def stream(self, messages, tools=None, **kwargs):
            self.calls += 1
            assert tools is None and "untrusted" in messages[0]["content"]
            yield LLMChunk(content=content, finish_reason=finish, usage=TokenUsage(total_tokens=91))

        async def aclose(self):
            self.closed = True

    model = Model()

    def factory(*, max_tokens):
        caps.append(max_tokens)
        return model

    monkeypatch.setattr(history_compression, "SessionFactory", Audit)
    monkeypatch.setattr(history_compression, "create_chat_model", factory)
    add = AsyncMock()
    monkeypatch.setattr(history_compression, "add_usage", add)
    arguments = dict(
        entries=[{"role": "user", "content": "original " * 5000}],
        output_tokens=9000,
        budget=ContextTokenBudget(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        version=LLM_HISTORY_SUMMARY_VERSION,
    )
    if valid:
        assert len(await REAL_SUMMARIZE(**arguments)) > 6000
    else:
        with pytest.raises(history_compression.HistoryCompressionError):
            await REAL_SUMMARIZE(**arguments)
    assert caps == [9000] and model.calls == 1 and model.closed
    assert records[0].status == ("completed" if valid else "failed")
    assert records[0].total_tokens == 91
    assert add.await_count == 1 and add.call_args.kwargs["amount"] == 91


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
