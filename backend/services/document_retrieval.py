"""Owner-scoped exact cosine and lexical retrieval, with hybrid orchestration dispatch."""

import re
import unicodedata
from uuid import UUID

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.contracts.retrieval import RetrievalResult
from agent_core.rag.chunking import normalize_text
from agent_core.rag.embeddings import MODEL, RECIPE, EmbeddingError
from backend.models import (
    Conversation,
    ConversationDocument,
    Document,
    DocumentChunk,
    DocumentVersion,
)
from backend.schemas.document import DocumentSearchRequest, DocumentSearchResponse
from backend.services.document_indexing import LEXICAL_CONFIG, get_embedding_client
from backend.services.documents import DocumentServiceError

RETRIEVAL_CONFIG = "pgvector-exact-simple-fts-v1"
_STOPWORDS = frozenset(
    (
        "toi cho biet hay cac nhung mot cua va la ve trong nay do duoc "
        "a an the of to in is are what please me tell"
    ).split()
)


def lexical_terms(query: str) -> str:
    def key(word):
        value = unicodedata.normalize("NFKD", word.lower().replace("đ", "d"))
        return "".join(char for char in value if not unicodedata.combining(char))

    quoted = re.findall(r'["“]([^"”]+)["”]', query)
    prioritized = re.findall(r"[^\W_]+", " ".join(quoted), flags=re.UNICODE)
    words = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    prioritized += [word for word in words if any(char.isdigit() for char in word)] + words
    result, seen = [], set()
    for word in prioritized:
        normalized = key(word)
        if normalized in seen or normalized in _STOPWORDS or not 1 <= len(word) <= 80:
            continue
        seen.add(normalized)
        result.append(word.lower())
        if len(result) == 16:
            break
    # Terms contain letters/numbers only. Query operators come only from this function.
    return " | ".join(result)


async def _scope(session: AsyncSession, owner: UUID, request: DocumentSearchRequest):
    identifiers = list(request.document_ids)
    if request.conversation_id is not None:
        conversation = await session.scalar(
            select(Conversation.id).where(
                Conversation.id == request.conversation_id,
                Conversation.user_id == owner,
            )
        )
        if conversation is None:
            raise DocumentServiceError("Không tìm thấy hội thoại.", code="conversation_not_found")
        identifiers = list(
            (
                await session.scalars(
                    select(ConversationDocument.document_id).where(
                        ConversationDocument.conversation_id == request.conversation_id,
                        ConversationDocument.owner_user_id == owner,
                    )
                )
            ).all()
        )
    if not identifiers:
        raise DocumentServiceError(
            "Chưa chọn tài liệu để tìm kiếm.", status_code=409, code="document_scope_empty"
        )
    documents = (
        await session.scalars(
            select(Document).where(
                Document.id.in_(identifiers),
                Document.owner_user_id == owner,
                Document.deleted_at.is_(None),
                Document.status != "deleting",
            )
        )
    ).all()
    if len(documents) != len(set(identifiers)):
        raise DocumentServiceError("Một số tài liệu không khả dụng.")
    if any(
        document.status != "ready" or document.active_version_id is None for document in documents
    ):
        raise DocumentServiceError(
            "Một số tài liệu chưa xử lý xong.", status_code=409, code="documents_not_ready"
        )
    versions = (
        await session.scalars(
            select(DocumentVersion)
            .join(
                Document,
                (Document.id == DocumentVersion.document_id)
                & (Document.active_version_id == DocumentVersion.id),
            )
            .where(
                Document.id.in_(identifiers),
                Document.owner_user_id == owner,
                Document.deleted_at.is_(None),
                Document.status == "ready",
                DocumentVersion.owner_user_id == owner,
                DocumentVersion.status == "ready",
            )
        )
    ).all()
    if len(versions) != len(documents) or any(
        version.pipeline_config.get("lexical_config") != LEXICAL_CONFIG for version in versions
    ):
        raise DocumentServiceError(
            "Index tài liệu chưa sẵn sàng hoặc khác cấu hình hiện tại.",
            status_code=409,
            code="document_index_config",
        )
    return versions


def scoped_chunks(owner: UUID, version_ids: list[UUID]):
    return (
        select(DocumentChunk, Document.title)
        .join(
            DocumentVersion,
            (DocumentVersion.id == DocumentChunk.version_id)
            & (DocumentVersion.document_id == DocumentChunk.document_id),
        )
        .join(
            Document,
            (Document.id == DocumentChunk.document_id)
            & (Document.active_version_id == DocumentChunk.version_id),
        )
        .where(
            DocumentChunk.owner_user_id == owner,
            DocumentVersion.owner_user_id == owner,
            Document.owner_user_id == owner,
            Document.deleted_at.is_(None),
            Document.status == "ready",
            DocumentVersion.status == "ready",
            DocumentChunk.version_id.in_(version_ids),
        )
    )


def _hit(chunk, title, score, kind):
    return RetrievalResult(
        citation_id=f"source.c{chunk.id.hex}",
        document_id=chunk.document_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        title=title,
        text=chunk.text,
        score=float(score),
        score_kind=kind,
        retrieval_config_version=RETRIEVAL_CONFIG,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section=chunk.section,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
    )


async def search_documents(session: AsyncSession, owner: UUID, request: DocumentSearchRequest):
    if request.mode == "hybrid":
        from backend.services.document_query import retrieve_document_evidence

        return await retrieve_document_evidence(session, owner, request)
    versions = await _scope(session, owner, request)
    version_ids = sorted((version.id for version in versions), key=str)
    query = normalize_text(request.query)
    if request.mode == "dense":
        profiles = [version.pipeline_config.get("embedding_profile") for version in versions]
        profile = profiles[0]
        if (
            not isinstance(profile, dict)
            or any(item != profile for item in profiles)
            or profile.get("model") != MODEL
            or profile.get("recipe") != RECIPE
            or any(version.embedding_revision != profile.get("revision") for version in versions)
        ):
            raise DocumentServiceError(
                "Các tài liệu cần cùng cấu hình embedding để tìm kiếm chung.",
                status_code=409,
                code="document_embedding_config",
            )
        revision = profile["revision"]
        # Release the connection during model download/inference; revalidate scope afterwards.
        await session.rollback()
        try:
            encoded = await get_embedding_client().encode([query], revision=revision, query=True)
        except EmbeddingError as exc:
            raise DocumentServiceError(
                str(exc),
                status_code=422 if exc.code.endswith(("limit", "revision")) else 503,
                code=exc.code,
            ) from None
        if encoded.profile != profile:
            raise DocumentServiceError(
                "Index dùng phiên bản thư viện embedding khác; cần lập lại index.",
                status_code=409,
                code="document_embedding_config",
            )
        current = await _scope(session, owner, request)
        if sorted((version.id for version in current), key=str) != version_ids:
            raise DocumentServiceError(
                "Phạm vi tài liệu đã thay đổi. Hãy gửi lại câu hỏi.",
                status_code=409,
                code="document_scope_changed",
            )
        if any(version.pipeline_config.get("embedding_profile") != profile for version in current):
            raise DocumentServiceError(
                "Cấu hình tài liệu đã thay đổi.", status_code=409, code="document_scope_changed"
            )
        distance = DocumentChunk.embedding.cosine_distance(encoded.vectors[0])
        statement = (
            scoped_chunks(owner, version_ids)
            .add_columns(distance.label("score"))
            .where(
                DocumentChunk.embedding.is_not(None),
            )
            .order_by(distance, DocumentChunk.id)
            .limit(request.limit)
        )
        rows = (await session.execute(statement)).all()
        hits = [
            _hit(chunk, title, max(-1.0, min(1.0, 1.0 - score)), "similarity")
            for chunk, title, score in rows
        ]
    else:
        terms = lexical_terms(query)
        hits = []
        if terms:
            tsquery = func.to_tsquery(
                literal_column("'pg_catalog.simple'::regconfig"),
                func.public.unaccent(terms),
                type_=TSQUERY,
            )
            rank = func.ts_rank_cd(DocumentChunk.search_tsv, tsquery, 32)
            statement = (
                scoped_chunks(owner, version_ids)
                .add_columns(rank.label("score"))
                .where(
                    DocumentChunk.search_tsv.op("@@")(tsquery),
                )
                .order_by(rank.desc(), DocumentChunk.id)
                .limit(request.limit)
            )
            rows = (await session.execute(statement)).all()
            hits = [_hit(chunk, title, score, "lexical") for chunk, title, score in rows]
    return DocumentSearchResponse(
        query=request.query,
        mode=request.mode,
        items=hits,
        retrieval_config_version=RETRIEVAL_CONFIG,
    )
