"""Resumable vectors + FTS on the preparation lease; publish only a complete version."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, literal_column, select, update

from agent_core.rag.documents import PreparationError
from agent_core.rag.embeddings import MODEL, EmbeddingError, F2EmbeddingClient
from backend.core.config import PROJECT_ROOT, get_settings
from backend.database import SessionFactory
from backend.models import DocumentChunk

LEXICAL_CONFIG = "pg-simple-unaccent-heading-v1"
_client: F2EmbeddingClient | None = None


def get_embedding_client() -> F2EmbeddingClient:
    global _client
    if _client is None:
        settings = get_settings()
        cache = settings.document_model_cache_path
        if not cache.is_absolute():
            cache = PROJECT_ROOT / cache
        _client = F2EmbeddingClient(
            project_root=PROJECT_ROOT,
            cache_path=cache,
            timeout_seconds=settings.document_embedding_timeout_seconds,
            memory_mb=settings.document_embedding_memory_mb,
            batch_size=settings.document_embedding_batch_size,
        )
    return _client


async def close_embedding_client() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.close()


def lexical_vector(title: str):
    config = literal_column("'pg_catalog.simple'::regconfig")
    heading = func.public.unaccent(
        func.lower(title + " " + func.coalesce(DocumentChunk.section, ""))
    )
    body = func.public.unaccent(func.lower(DocumentChunk.lexical_text))
    return func.setweight(func.to_tsvector(config, heading), literal_column("'A'::\"char\"")).op(
        "||"
    )(func.setweight(func.to_tsvector(config, body), literal_column("'B'::\"char\"")))


def has_prepared_content(version) -> bool:
    return bool(
        version.chunk_count
        and version.pipeline_hash
        and version.parsed_storage_key
        and version.parser_version
        and version.chunker_version
        and version.embedding_revision
    )


async def index_if_prepared(context, version_id: UUID) -> dict | None:
    from backend.services.document_ingestion import _locked

    try:
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            if not has_prepared_content(version):
                return None
            if version.status == "ready" and document.active_version_id == version_id:
                return {"indexed": True, "chunk_count": version.chunk_count}
            if version.embedding_model != MODEL:
                raise PreparationError(
                    "embedding_model_mismatch", "Model của tài liệu không được hỗ trợ."
                )
            owner, document_id, revision = (
                document.owner_user_id,
                document.id,
                version.embedding_revision,
            )
            count = await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.owner_user_id == owner,
                    DocumentChunk.version_id == version_id,
                )
            )
            if count != version.chunk_count:
                raise PreparationError(
                    "document_chunks_incomplete", "Các chunk đã lưu chưa đầy đủ."
                )
            ingestion.status, ingestion.stage = "running", "embedding"
            ingestion.total_chunks, ingestion.finished_at = count, None
            ingestion.started_at = ingestion.started_at or datetime.now(UTC)
            ingestion.error_code = ingestion.error_message = None
            version.status, document.status = "embedding", "embedding"
            version.error_code = version.error_message = None
            version.pipeline_config = {
                **version.pipeline_config,
                "awaiting_embedding": False,
                "content_prepared": True,
            }
            await session.commit()
        client = get_embedding_client()
        profile = (await client.encode([], revision=revision)).profile
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            if version.pipeline_config.get("embedding_profile") != profile:
                # Unpublished partial vectors from another implementation must be rebuilt together.
                await session.execute(
                    update(DocumentChunk)
                    .where(
                        DocumentChunk.owner_user_id == owner,
                        DocumentChunk.version_id == version_id,
                    )
                    .values(embedding=None)
                )
            version.pipeline_config = {
                **version.pipeline_config,
                "embedding_profile": profile,
                "content_pipeline_hash": version.pipeline_config.get(
                    "content_pipeline_hash", version.pipeline_hash
                ),
            }
            ingestion.completed_chunks = await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.owner_user_id == owner,
                    DocumentChunk.version_id == version_id,
                    DocumentChunk.embedding.is_not(None),
                )
            )
            await session.commit()
        while True:
            await context.raise_if_cancelled()
            async with SessionFactory() as session:
                _document, version, _ingestion, _job = await _locked(session, context, version_id)
                rows = (
                    await session.execute(
                        select(DocumentChunk.id, DocumentChunk.embedding_text)
                        .where(
                            DocumentChunk.owner_user_id == owner,
                            DocumentChunk.document_id == document_id,
                            DocumentChunk.version_id == version_id,
                            DocumentChunk.embedding.is_(None),
                        )
                        .order_by(DocumentChunk.chunk_index)
                        .limit(client.batch_size)
                    )
                ).all()
            if not rows:
                break
            batch = await client.encode([row.embedding_text for row in rows], revision=revision)
            if batch.profile != profile:
                raise PreparationError(
                    "embedding_profile_changed", "Cấu hình embedding thay đổi khi đang xử lý."
                )
            await context.raise_if_cancelled()
            async with SessionFactory() as session:
                _document, _version, ingestion, _job = await _locked(session, context, version_id)
                for row, vector in zip(rows, batch.vectors, strict=True):
                    await session.execute(
                        update(DocumentChunk)
                        .where(
                            DocumentChunk.id == row.id,
                            DocumentChunk.owner_user_id == owner,
                            DocumentChunk.version_id == version_id,
                        )
                        .values(embedding=vector)
                    )
                ingestion.completed_chunks = await session.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(
                        DocumentChunk.owner_user_id == owner,
                        DocumentChunk.version_id == version_id,
                        DocumentChunk.embedding.is_not(None),
                    )
                )
                await session.commit()
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            document.status, version.status, ingestion.stage = "indexing", "indexing", "indexing"
            await session.commit()
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            scope = (
                DocumentChunk.owner_user_id == owner,
                DocumentChunk.document_id == document_id,
                DocumentChunk.version_id == version_id,
            )
            indexed = await session.execute(
                update(DocumentChunk)
                .where(*scope)
                .values(search_tsv=lexical_vector(document.title))
            )
            valid = await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    *scope,
                    DocumentChunk.embedding.is_not(None),
                    func.vector_norm(DocumentChunk.embedding).between(0.999, 1.001),
                )
            )
            if valid != version.chunk_count or indexed.rowcount != version.chunk_count:
                raise PreparationError(
                    "document_index_incomplete", "Index tài liệu chưa đầy đủ; chưa thể công bố."
                )
            version.pipeline_config = {
                **version.pipeline_config,
                "lexical_config": LEXICAL_CONFIG,
                "awaiting_embedding": False,
            }
            version.pipeline_hash = hashlib.sha256(
                json.dumps(
                    {
                        "content": version.pipeline_config["content_pipeline_hash"],
                        "embedding": profile,
                        "lexical": LEXICAL_CONFIG,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            version.status, version.ready_at = "ready", datetime.now(UTC)
            document.status, document.active_version_id = "ready", version_id
            document.revision += 1
            ingestion.status, ingestion.finished_at = "succeeded", datetime.now(UTC)
            ingestion.completed_chunks = ingestion.total_chunks = version.chunk_count
            await session.commit()
        return {"indexed": True, "chunk_count": valid}
    except EmbeddingError as exc:
        raise PreparationError(exc.code, str(exc), retryable=exc.retryable) from None
