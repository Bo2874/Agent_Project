"""Deterministic fusion and source-preserving context packing, independent of storage."""

import json
from dataclasses import dataclass
from uuid import UUID

from agent_core.context_budget import ContextBlock, measure_context_blocks
from agent_core.contracts.retrieval import RetrievalResult

FUSION_RECIPE = "rrf60-equal-20x20-top20-v1"
PACKING_RECIPE = "whole-chunks-target6-max8-v1"


def reciprocal_rank_fusion(*rankings: list[RetrievalResult]) -> list[RetrievalResult]:
    scores, hits = {}, {}
    for ranking in rankings:
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            key = (hit.version_id, hit.chunk_id)
            if key in seen:
                continue
            seen.add(key)
            hits.setdefault(key, hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
    ordered = sorted(hits, key=lambda key: (-scores[key], str(key[0]), str(key[1])))[:20]
    return [
        hits[key].model_copy(update={"score": scores[key], "score_kind": "fusion"})
        for key in ordered
    ]


def evidence_block(hit: RetrievalResult) -> ContextBlock:
    # Preserve the complete source and its offsets. Escaping belongs to the context serializer.
    payload = hit.model_dump(mode="json", exclude={"score", "score_kind"})
    return ContextBlock(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        source=hit.citation_id,
    )


@dataclass(frozen=True)
class PackedEvidence:
    items: list[RetrievalResult]
    blocks: list[ContextBlock]
    estimated_tokens: int
    budget_skipped: int


def pack_evidence(hits: list[RetrievalResult], *, token_budget: int) -> PackedEvidence:
    if token_budget < 0:
        raise ValueError("Evidence budget cannot be negative")
    items, blocks, skipped = [], [], 0
    available_documents = {hit.document_id for hit in hits}
    represented: set[UUID] = set()
    for hit in hits:
        # Aim for six; use at most two extra slots for documents not represented yet.
        if len(items) >= 8 or (len(items) >= 6 and represented == available_documents):
            break
        if len(items) >= 6 and hit.document_id in represented:
            continue
        block = evidence_block(hit)
        if measure_context_blocks("rag", [*blocks, block]) > token_budget:
            skipped += 1
            continue
        items.append(hit)
        blocks.append(block)
        represented.add(hit.document_id)
    return PackedEvidence(items, blocks, measure_context_blocks("rag", blocks), skipped)
