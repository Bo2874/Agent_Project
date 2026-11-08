"""Final chat attachments without a live database, Docker or model."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from backend.models.artifact import ArtifactVersion
from backend.services.artifacts import publication


def version():
    return ArtifactVersion(
        id=uuid4(),
        artifact_id=uuid4(),
        parent_version_id=None,
        name="result.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=128,
        sha256="a" * 64,
        calculation_status="fresh",
        created_at=datetime.now(UTC),
    )


async def test_historical_message_exposes_one_final_card_without_deleting_versions(monkeypatch):
    owner, conversation, message = uuid4(), uuid4(), uuid4()
    original, final = version(), version()
    monkeypatch.setattr(publication, "owned_conversation", AsyncMock())
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [(message, original), (message, final)])
        )
    )
    result = await publication.message_artifacts(session, owner, conversation, [message])
    assert [ref.version_id for ref in result[message]] == [final.id]
    # This read path must not delete or rewrite historical version records.
    query = session.execute.call_args.args[0]
    assert query.is_select


async def test_final_message_selection_remains_separate_for_each_answer(monkeypatch):
    first, second = uuid4(), uuid4()
    a, b, c = version(), version(), version()
    monkeypatch.setattr(publication, "owned_conversation", AsyncMock())
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [(first, a), (second, b), (first, c)])
        )
    )
    result = await publication.message_artifacts(session, uuid4(), uuid4(), [first, second])
    assert [ref.version_id for ref in result[first]] == [c.id]
    assert [ref.version_id for ref in result[second]] == [b.id]


async def test_run_queries_only_the_latest_owned_output(monkeypatch):
    owner, conversation, run = uuid4(), uuid4(), uuid4()
    final = version()
    monkeypatch.setattr(publication, "owned_conversation", AsyncMock())
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [final])),
    )
    result = await publication.run_artifacts(session, owner, conversation, run)
    assert [ref.version_id for ref in result] == [final.id]
    query = session.scalars.call_args.args[0]
    compiled = query.compile()
    assert compiled.params["param_1"] == 1
    assert owner in compiled.params.values()
    assert conversation in compiled.params.values()
    assert run in compiled.params.values()
    assert "artifact_versions.created_at DESC" in str(compiled)
