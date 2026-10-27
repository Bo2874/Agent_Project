"""Deterministic fusion and source-preserving context packing, independent of storage."""

import json
from dataclasses import dataclass

from agent_core.context_budget import ContextBlock, measure_context_blocks
from agent_core.contracts.retrieval import RetrievalResult

FUSION_RECIPE = "rrf60-equal-20x20-top10-v2"
PACKING_RECIPE = "ranked-top10-budget8000-v3"
MAX_EVIDENCE_TOKENS = 8000


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
    ordered = sorted(hits, key=lambda key: (-scores[key], str(key[0]), str(key[1])))[:10]
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
    duplicate_skipped: int = 0
    content_duplicate_skipped: int = 0


def pack_evidence(
    hits: list[RetrievalResult], *, token_budget: int, label_sources: bool = False
) -> PackedEvidence:
    """Keep whole passages from one ordered top-ten ranking within the source budget.

    Fast supplies RRF order; Deep supplies reranker order. Never re-sort here.
    Only exact text duplicates within the same version/section are collapsed;
    partially overlapping passages retain their original text and provenance.
    Search API retains opaque source IDs; QA measures its final S1/S2 labels.
    """
    if token_budget < 0:
        raise ValueError("Evidence budget cannot be negative")
    token_budget = min(token_budget, MAX_EVIDENCE_TOKENS)
    items, blocks, skipped = [], [], 0
    seen, seen_content = set(), set()
    duplicate_skipped, content_duplicate_skipped = 0, 0
    for hit in hits[:10]:
        if hit.chunk_id in seen:
            duplicate_skipped += 1
            continue
        # Preserve indentation/table spacing and distinct section context.
        content_key = (hit.version_id, hit.section, hit.text.replace("\r\n", "\n"))
        if content_key in seen_content:
            content_duplicate_skipped += 1
            continue
        if label_sources:
            hit = hit.model_copy(update={"citation_id": f"S{len(items) + 1}"})
        block = evidence_block(hit)
        if measure_context_blocks("rag", [*blocks, block]) > token_budget:
            skipped += 1
            continue
        items.append(hit)
        blocks.append(block)
        seen.add(hit.chunk_id)
        seen_content.add(content_key)
    return PackedEvidence(
        items=items,
        blocks=blocks,
        estimated_tokens=measure_context_blocks("rag", blocks),
        budget_skipped=skipped,
        duplicate_skipped=duplicate_skipped,
        content_duplicate_skipped=content_duplicate_skipped,
    )
